from __future__ import annotations

import argparse
import dataclasses
import json
import os
import time
from datetime import datetime
from enum import Enum
from typing import List, Optional

from ai_quant_scientist.evals.critic_eval import load_cases_from_file, CriticEvalSuite, build_critic_context
from ai_quant_scientist.services.openai_research_critic import OpenAIResearchCritic


def _serialise_decision(decision) -> dict:
    """Convert a CriticDecision (frozen slotted dataclass) to a JSON-safe dict."""
    raw = dataclasses.asdict(decision)
    return _json_safe(raw)


def _json_safe(obj):
    """Recursively convert Enum and datetime values to JSON-safe types."""
    if isinstance(obj, Enum):
        return obj.name
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_json_safe(v) for v in obj]
    return obj


def _extract_tokens_from_serialised(parsed: dict) -> dict:
    """Pull token counts out of the compact provenance stored in decision.raw_response."""
    try:
        usage = json.loads(parsed.get("raw_response") or "{}").get("usage") or {}
        return {
            "input_tokens": usage.get("input_tokens") or 0,
            "output_tokens": usage.get("output_tokens") or 0,
            "reasoning_tokens": usage.get("reasoning_tokens") or 0,
        }
    except Exception:
        return {"input_tokens": 0, "output_tokens": 0, "reasoning_tokens": 0}


def run_live_eval(model: str, eval_path: str, allow_live_api: bool = False, max_cases: Optional[int] = None, case_id: Optional[str] = None, output_dir: str = "artifacts/evals", max_retries_per_case: int = 1, prompt_version: str = "v1", repeats: int = 1):
    if not allow_live_api:
        raise RuntimeError("Live API calls are disabled. Re-run with --allow-live-api to enable paid calls.")

    cases = load_cases_from_file(eval_path)
    if case_id:
        cases = [c for c in cases if c.id == case_id]
    if max_cases is not None:
        cases = cases[:max_cases]

    os.makedirs(output_dir, exist_ok=True)
    timestamp = int(time.time())

    client = OpenAIResearchCritic(model=model, prompt_version=prompt_version)

    if repeats == 1:
        # ── Original single-run path — artifact format is unchanged ──
        out_path = os.path.join(output_dir, f"openai_eval_{model}_{timestamp}.json")
        results = []
        total_calls = 0
        absolute_call_limit = len(cases) * (1 + max_retries_per_case)

        for case in cases:
            if total_calls >= absolute_call_limit:
                break
            ctx = build_critic_context(case)  # canonical context with constraints
            attempt = 0
            while attempt <= max_retries_per_case:
                attempt += 1
                total_calls += 1
                try:
                    decision = client.critique(ctx)
                    res = {"case_id": case.id, "decision": decision.decision_type.name, "parsed": _serialise_decision(decision)}
                    results.append(res)
                    break
                except Exception as exc:
                    last_err = str(exc)
                    if attempt > max_retries_per_case:
                        results.append({"case_id": case.id, "error": last_err})
                        break

        with open(out_path, "w", encoding="utf-8") as f:
            json.dump({"model": model, "prompt_version": client.prompt_version, "timestamp": timestamp, "results": results}, f, indent=2)
        return out_path

    # ── Repeatability path (repeats > 1) ──
    out_path = os.path.join(output_dir, f"openai_eval_{model}_repeats{repeats}_{timestamp}.json")
    artifact: dict = {
        "model": model,
        "prompt_version": client.prompt_version,
        "timestamp": timestamp,
        "repeats": repeats,
        "cases": [],
        "summary": None,
    }

    agg_invocations = 0
    agg_successful = 0
    agg_input_tok = 0
    agg_output_tok = 0
    agg_reasoning_tok = 0
    unanimous_count = 0
    mixed_count = 0

    for case in cases:
        repetitions: list = []
        ctx = build_critic_context(case)  # build once; each repeat is an independent invocation

        for rep_idx in range(repeats):
            attempt = 0
            while attempt <= max_retries_per_case:
                attempt += 1
                agg_invocations += 1
                try:
                    decision = client.critique(ctx)
                    repetitions.append({
                        "repeat_index": rep_idx,
                        "decision": decision.decision_type.name,
                        "parsed": _serialise_decision(decision),
                        "error": None,
                    })
                    break
                except Exception as exc:
                    last_err = str(exc)
                    if attempt > max_retries_per_case:
                        repetitions.append({
                            "repeat_index": rep_idx,
                            "decision": None,
                            "parsed": None,
                            "error": last_err,
                        })
                        break

        successful = [r for r in repetitions if r["error"] is None]
        failed = [r for r in repetitions if r["error"] is not None]

        propose_count = sum(1 for r in successful if r["decision"] == "PROPOSE_REVISION")
        no_useful_count = sum(1 for r in successful if r["decision"] == "NO_USEFUL_REVISION")

        agreement_rate = None
        majority_decision = None
        if successful:
            # agreement rate = fraction of successful runs that chose the majority decision
            agreement_rate = max(propose_count, no_useful_count) / len(successful)
            if propose_count > no_useful_count:
                majority_decision = "PROPOSE_REVISION"
            elif no_useful_count > propose_count:
                majority_decision = "NO_USEFUL_REVISION"
            # tied: majority_decision stays None

        confidence_dist: dict = {}
        for r in successful:
            conf = (r["parsed"] or {}).get("confidence")
            key = conf if conf is not None else "null"
            confidence_dist[key] = confidence_dist.get(key, 0) + 1

        input_tok = output_tok = reasoning_tok = 0
        for r in successful:
            tok = _extract_tokens_from_serialised(r["parsed"] or {})
            input_tok += tok["input_tokens"]
            output_tok += tok["output_tokens"]
            reasoning_tok += tok["reasoning_tokens"]

        agg_successful += len(successful)
        agg_input_tok += input_tok
        agg_output_tok += output_tok
        agg_reasoning_tok += reasoning_tok
        if successful:
            if agreement_rate == 1.0:
                unanimous_count += 1
            else:
                mixed_count += 1

        artifact["cases"].append({
            "case_id": case.id,
            "repetitions": repetitions,
            "aggregate": {
                "total_runs": repeats,
                "successful_runs": len(successful),
                "failed_runs": len(failed),
                "PROPOSE_REVISION": propose_count,
                "NO_USEFUL_REVISION": no_useful_count,
                "decision_agreement_rate": agreement_rate,
                "majority_decision": majority_decision,
                "confidence_distribution": confidence_dist,
                "input_tokens_total": input_tok,
                "output_tokens_total": output_tok,
                "reasoning_tokens_total": reasoning_tok,
            },
        })

        # Flush after every case so prior results survive a later failure
        artifact["summary"] = {
            "total_invocations": agg_invocations,
            "total_successful": agg_successful,
            "total_failed": agg_invocations - agg_successful,
            "total_input_tokens": agg_input_tok,
            "total_output_tokens": agg_output_tok,
            "total_reasoning_tokens": agg_reasoning_tok,
            "unanimous_cases": unanimous_count,
            "mixed_cases": mixed_count,
        }
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(artifact, f, indent=2)

    return out_path


def main(argv: List[str] = None):
    p = argparse.ArgumentParser(description="Run live OpenAI critic eval (requires --allow-live-api)")
    p.add_argument("--model", default=None)
    p.add_argument("--eval-set", default="evals/critic_v1.json")
    p.add_argument("--allow-live-api", action="store_true")
    p.add_argument("--max-cases", type=int, default=None)
    p.add_argument("--case-id", default=None)
    p.add_argument("--out", default="artifacts/evals")
    p.add_argument("--prompt-version", default="v1")
    p.add_argument("--repeats", type=int, default=1, help="Number of independent repetitions per case (default 1)")
    args = p.parse_args(argv)

    model = args.model or os.getenv("AI_QUANT_CRITIC_MODEL", "gpt-5.6-luna")
    if not args.allow_live_api:
        print("Refusing to run live API calls. Add --allow-live-api to proceed.")
        return 2

    out = run_live_eval(model=model, eval_path=args.eval_set, allow_live_api=args.allow_live_api, max_cases=args.max_cases, case_id=args.case_id, output_dir=args.out, prompt_version=args.prompt_version, repeats=args.repeats)
    print(f"Wrote results to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
