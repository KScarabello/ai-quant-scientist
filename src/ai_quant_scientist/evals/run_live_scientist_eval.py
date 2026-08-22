"""Live runner for the Bounded Hypothesis Scientist eval harness.

Requires --allow-live-api to make real API calls.
Default invocation makes zero API calls.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import os
import time
from datetime import datetime
from enum import Enum
from typing import List, Optional

from ai_quant_scientist.evals.scientist_eval import load_cases_from_file, ScientistEvalSuite
from ai_quant_scientist.services.openai_hypothesis_scientist import OpenAIHypothesisScientist


def _json_safe(obj):
    if isinstance(obj, Enum):
        return obj.name
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_json_safe(v) for v in obj]
    return obj


def run_live_scientist_eval(
    model: str,
    eval_path: str,
    prompt_version: str = "v2",
    allow_live_api: bool = False,
    max_cases: Optional[int] = None,
    case_id: Optional[str] = None,
    output_dir: str = "artifacts/evals",
) -> str:
    if not allow_live_api:
        raise RuntimeError("Live API calls are disabled. Add --allow-live-api to enable.")

    cases = load_cases_from_file(eval_path)
    if case_id:
        cases = [c for c in cases if c.id == case_id]
    if max_cases is not None:
        cases = cases[:max_cases]

    os.makedirs(output_dir, exist_ok=True)
    timestamp = int(time.time())
    out_path = os.path.join(output_dir, f"scientist_eval_{model}_{timestamp}.json")

    scientist = OpenAIHypothesisScientist(model=model, prompt_version=prompt_version)
    suite = ScientistEvalSuite(cases)
    results = suite.run(scientist, prompt_version=prompt_version)

    output = {
        "model": model,
        "prompt_version": prompt_version,
        "timestamp": timestamp,
        "results": [
            {
                "case_id": r.case_id,
                "provider": r.provider,
                "model": r.model,
                "prompt_version": r.prompt_version,
                "ontology_version": r.ontology_version,
                "ontology_fingerprint": r.ontology_fingerprint,
                "decision_type": r.decision_type,
                "contract_passed": r.contract_passed,
                "validation_errors": r.validation_errors,
                "requirement_count": r.requirement_count,
                "hypothesis_statement": r.hypothesis_statement,
                "expected_decision": r.expected_decision,
                "evaluation_focus": r.evaluation_focus,
                "notes": r.notes,
                "parsed": r.parsed_decision,
            }
            for r in results
        ],
        "summary": {
            "total_cases": len(results),
            "contract_passed": sum(1 for r in results if r.contract_passed),
            "propose_count": sum(1 for r in results if r.decision_type == "PROPOSE_HYPOTHESIS"),
            "no_hypothesis_count": sum(1 for r in results if r.decision_type == "NO_HYPOTHESIS"),
        },
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)
    return out_path


def main(argv: List[str] = None) -> int:
    p = argparse.ArgumentParser(description="Run live Hypothesis Scientist eval")
    p.add_argument("--model", default="gpt-5.6-terra")
    p.add_argument("--eval-set", default="evals/scientist_v1.json")
    p.add_argument("--prompt-version", default="v2")
    p.add_argument("--allow-live-api", action="store_true")
    p.add_argument("--max-cases", type=int, default=None)
    p.add_argument("--case-id", default=None)
    p.add_argument("--out", default="artifacts/evals")
    args = p.parse_args(argv)

    if not args.allow_live_api:
        print("Refusing to run live API calls. Add --allow-live-api to proceed.")
        return 2

    out = run_live_scientist_eval(
        model=args.model,
        eval_path=args.eval_set,
        prompt_version=args.prompt_version,
        allow_live_api=args.allow_live_api,
        max_cases=args.max_cases,
        case_id=args.case_id,
        output_dir=args.out,
    )
    print(f"Wrote results to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
