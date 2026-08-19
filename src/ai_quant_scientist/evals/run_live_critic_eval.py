from __future__ import annotations

import argparse
import json
import os
import time
from typing import List, Optional

from ai_quant_scientist.evals.critic_eval import load_cases_from_file, CriticEvalSuite
from ai_quant_scientist.services.openai_research_critic import OpenAIResearchCritic


def run_live_eval(model: str, eval_path: str, allow_live_api: bool = False, max_cases: Optional[int] = None, case_id: Optional[str] = None, output_dir: str = "artifacts/evals", max_retries_per_case: int = 1):
    if not allow_live_api:
        raise RuntimeError("Live API calls are disabled. Re-run with --allow-live-api to enable paid calls.")

    cases = load_cases_from_file(eval_path)
    if case_id:
        cases = [c for c in cases if c.id == case_id]
    if max_cases is not None:
        cases = cases[:max_cases]

    os.makedirs(output_dir, exist_ok=True)
    timestamp = int(time.time())
    out_path = os.path.join(output_dir, f"openai_eval_{model}_{timestamp}.json")

    client = OpenAIResearchCritic(model=model)

    results = []
    total_calls = 0
    absolute_call_limit = len(cases) * (1 + max_retries_per_case)

    for case in cases:
        if total_calls >= absolute_call_limit:
            break
        # one attempt plus possible retry on transient errors
        attempt = 0
        while attempt <= max_retries_per_case:
            attempt += 1
            total_calls += 1
            try:
                decision = client.critique(case.context)
                res = {"case_id": case.id, "decision": decision.decision_type.name, "parsed": decision.__dict__}
                results.append(res)
                break
            except Exception as exc:
                last_err = str(exc)
                if attempt > max_retries_per_case:
                    results.append({"case_id": case.id, "error": last_err})
                    break
                # otherwise retry once
                continue

    # write artifact incrementally
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"model": model, "prompt_version": client.prompt_version, "timestamp": timestamp, "results": results}, f, indent=2)

    return out_path


def main(argv: List[str] = None):
    p = argparse.ArgumentParser(description="Run live OpenAI critic eval (requires --allow-live-api)")
    p.add_argument("--model", default=None)
    p.add_argument("--eval-set", default="evals/critic_v1.json")
    p.add_argument("--allow-live-api", action="store_true")
    p.add_argument("--max-cases", type=int, default=None)
    p.add_argument("--case-id", default=None)
    p.add_argument("--out", default="artifacts/evals")
    args = p.parse_args(argv)

    model = args.model or os.getenv("AI_QUANT_CRITIC_MODEL", "gpt-5.6-luna")
    if not args.allow_live_api:
        print("Refusing to run live API calls. Add --allow-live-api to proceed.")
        return 2

    out = run_live_eval(model=model, eval_path=args.eval_set, allow_live_api=args.allow_live_api, max_cases=args.max_cases, case_id=args.case_id, output_dir=args.out)
    print(f"Wrote results to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
