"""Run the frozen critic_v1 benchmark against a local Ollama model."""
from __future__ import annotations

import argparse
import dataclasses
import json
import os
import time
from datetime import datetime
from enum import Enum
from typing import List, Optional

from ai_quant_scientist.evals.critic_eval import load_cases_from_file, build_critic_context
from ai_quant_scientist.services.ollama_research_critic import OllamaResearchCritic


def _serialise_decision(decision) -> dict:
    raw = dataclasses.asdict(decision)
    for k, v in raw.items():
        if isinstance(v, Enum):
            raw[k] = v.name
        elif isinstance(v, datetime):
            raw[k] = v.isoformat()
    return raw


def run_ollama_eval(
    model: str,
    eval_path: str,
    base_url: str = "http://localhost:11434",
    max_cases: Optional[int] = None,
    case_id: Optional[str] = None,
    output_dir: str = "artifacts/evals",
    timeout: int = 120,
    prompt_version: str = "v1",
) -> str:
    cases = load_cases_from_file(eval_path)
    if case_id:
        cases = [c for c in cases if c.id == case_id]
    if max_cases is not None:
        cases = cases[:max_cases]

    os.makedirs(output_dir, exist_ok=True)
    timestamp = int(time.time())
    out_path = os.path.join(output_dir, f"ollama_eval_{model.replace(':', '_')}_{timestamp}.json")

    critic = OllamaResearchCritic(model=model, base_url=base_url, timeout=timeout, prompt_version=prompt_version)

    results = []
    for case in cases:
        t0 = time.monotonic()
        ctx = build_critic_context(case)  # canonical context with constraints
        try:
            decision = critic.critique(ctx)
            latency_ms = int((time.monotonic() - t0) * 1000)

            # Extract Ollama token metadata from raw_response provenance
            prov: dict = {}
            try:
                prov = json.loads(decision.raw_response or "{}").get("provenance", {})
            except Exception:
                pass

            results.append({
                "case_id": case.id,
                "decision": decision.decision_type.name,
                "latency_ms": latency_ms,
                "prompt_eval_count": prov.get("prompt_eval_count"),
                "eval_count": prov.get("eval_count"),
                "total_duration_ns": prov.get("total_duration"),
                "parsed": _serialise_decision(decision),
            })
        except Exception as exc:
            latency_ms = int((time.monotonic() - t0) * 1000)
            results.append({"case_id": case.id, "error": str(exc), "latency_ms": latency_ms})

        # Flush after each case so a later failure doesn't lose completed results
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump({
                "model": model,
                "provider": "ollama",
                "prompt_version": critic.prompt_version,
                "timestamp": timestamp,
                "results": results,
            }, f, indent=2)

    return out_path


def main(argv: List[str] = None) -> int:
    p = argparse.ArgumentParser(description="Run Ollama local critic benchmark against critic_v1")
    p.add_argument("--model", default="llama3.1:8b")
    p.add_argument("--eval-set", default="evals/critic_v1.json")
    p.add_argument("--base-url", default="http://localhost:11434")
    p.add_argument("--max-cases", type=int, default=None)
    p.add_argument("--case-id", default=None)
    p.add_argument("--out", default="artifacts/evals")
    p.add_argument("--timeout", type=int, default=120)
    p.add_argument("--prompt-version", default="v1")
    args = p.parse_args(argv)

    out = run_ollama_eval(
        model=args.model,
        eval_path=args.eval_set,
        base_url=args.base_url,
        max_cases=args.max_cases,
        case_id=args.case_id,
        output_dir=args.out,
        timeout=args.timeout,
        prompt_version=args.prompt_version,
    )
    print(f"Wrote results to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
