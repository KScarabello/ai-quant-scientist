"""Live runner for the bounded Research Designer eval harness.

Requires --allow-live-api to make real API calls.
Default invocation makes zero API calls.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime
from enum import Enum
from typing import List, Optional

from ai_quant_scientist.evals.research_designer_eval import (
    ResearchDesignerEvalSuite,
    load_cases_from_file,
)
from ai_quant_scientist.services.openai_research_designer import OpenAIResearchDesigner


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


def run_live_research_designer_eval(
    model: str,
    eval_path: str,
    prompt_version: str = "v1",
    allow_live_api: bool = False,
    max_cases: Optional[int] = None,
    case_id: Optional[str] = None,
    output_dir: str = "artifacts/evals",
) -> str:
    if not allow_live_api:
        raise RuntimeError("Live API calls are disabled. Add --allow-live-api to enable.")

    cases = load_cases_from_file(eval_path)
    if case_id:
        cases = [case for case in cases if case.id == case_id]
    if max_cases is not None:
        cases = cases[:max_cases]

    os.makedirs(output_dir, exist_ok=True)
    timestamp = int(time.time())
    out_path = os.path.join(output_dir, f"research_designer_eval_{model}_{timestamp}.json")

    designer = OpenAIResearchDesigner(model=model, prompt_version=prompt_version)
    suite = ResearchDesignerEvalSuite(cases)
    results = suite.run(designer)

    output = {
        "model": model,
        "prompt_version": prompt_version,
        "timestamp": timestamp,
        "results": [
            {
                "case_id": result.case_id,
                "provider": result.provider,
                "model": result.model,
                "prompt_version": result.prompt_version,
                "ontology_version": result.ontology_version,
                "ontology_fingerprint": result.ontology_fingerprint,
                "runner_outcome": result.runner_outcome,
                "decision_type": result.decision_type,
                "contract_passed": result.contract_passed,
                "validation_errors": result.validation_errors,
                "resulting_design_intent_id": result.resulting_design_intent_id,
                "design_summary": result.design_summary,
                "expected_decision": result.expected_decision,
                "evaluation_focus": result.evaluation_focus,
                "manual_success_criteria": result.manual_success_criteria,
                "notes": result.notes,
                "parsed": result.parsed_decision,
            }
            for result in results
        ],
        "summary": {
            "total_cases": len(results),
            "contract_passed": sum(1 for result in results if result.contract_passed),
            "design_count": sum(1 for result in results if result.runner_outcome == "DESIGN"),
            "no_valid_design_count": sum(1 for result in results if result.runner_outcome == "NO_VALID_DESIGN"),
            "blocked_pre_call_count": sum(1 for result in results if result.runner_outcome == "BLOCKED_PRE_CALL"),
        },
    }
    with open(out_path, "w", encoding="utf-8") as handle:
        json.dump(_json_safe(output), handle, indent=2)
    return out_path


def main(argv: List[str] = None) -> int:
    parser = argparse.ArgumentParser(description="Run live Research Designer eval")
    parser.add_argument("--model", default="gpt-5.6-terra")
    parser.add_argument("--eval-set", default="evals/research_designer_v1.json")
    parser.add_argument("--prompt-version", default="v1")
    parser.add_argument("--allow-live-api", action="store_true")
    parser.add_argument("--max-cases", type=int, default=None)
    parser.add_argument("--case-id", default=None)
    parser.add_argument("--out", default="artifacts/evals")
    args = parser.parse_args(argv)

    if not args.allow_live_api:
        print("Refusing to run live API calls. Add --allow-live-api to proceed.")
        return 2

    out = run_live_research_designer_eval(
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
