"""Guarded live runner for governed adaptive research continuation."""

from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime
from enum import Enum
from typing import Any

from ai_quant_scientist.services.hypothesis_prompts import get_scientist_instructions
from ai_quant_scientist.services.openai_research_continuation import (
    OpenAIResearchContinuationHypothesisScientist,
)
from ai_quant_scientist.services.research_continuation import GovernedResearchContinuation
from ai_quant_scientist.storage.sqlite_store import SQLiteStore


def _json_safe(obj: Any):
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    return obj


class _UnusedContinuationScientist:
    provider = "unused"
    model = "unused"
    prompt_version = "v6"

    def generate(self, context):
        raise AssertionError("Preparation mode must not invoke Hypothesis Scientist")


def run_live_research_continuation(
    *,
    post_verdict_intent_id: str | None = None,
    continuation_authorization_id: str | None = None,
    model: str = "gpt-5.6-terra",
    prepare: bool = False,
    authorize_and_generate: bool = False,
    allow_live_api: bool = False,
    output_dir: str = "artifacts/evals",
    db_path: str = "data/ai_quant_scientist.db",
) -> str:
    if prepare == authorize_and_generate:
        raise RuntimeError("Choose exactly one of --prepare or --authorize-and-generate")
    if prepare:
        if not post_verdict_intent_id:
            raise RuntimeError("Preparation mode requires --post-verdict-intent-id")
        if continuation_authorization_id is not None:
            raise RuntimeError("--continuation-authorization-id is not valid with --prepare")
        return _run_prepare(
            post_verdict_intent_id=post_verdict_intent_id,
            output_dir=output_dir,
            db_path=db_path,
        )

    if not continuation_authorization_id:
        raise RuntimeError("Authorized generation mode requires --continuation-authorization-id")
    if post_verdict_intent_id is not None:
        raise RuntimeError("--post-verdict-intent-id is only valid with --prepare")
    if not allow_live_api:
        raise RuntimeError("Live API calls are disabled. Add --allow-live-api to enable.")
    return _run_authorize_and_generate(
        continuation_authorization_id=continuation_authorization_id,
        model=model,
        output_dir=output_dir,
        db_path=db_path,
    )


def _run_prepare(*, post_verdict_intent_id: str, output_dir: str, db_path: str) -> str:
    store = SQLiteStore(db_path)
    service = GovernedResearchContinuation(
        store=store,
        scientist=_UnusedContinuationScientist(),
    )
    authorization = service.prepare(post_verdict_intent_id)
    artifact = {
        "mode": "prepare",
        "post_verdict_research_intent_id": authorization.post_verdict_research_intent_id,
        "parent_scientific_verdict_id": authorization.parent_scientific_verdict_id,
        "parent_hypothesis_claim_set_id": authorization.parent_hypothesis_claim_set_id,
        "frozen_research_scope": authorization.research_scope_payload(),
        "generation_number": authorization.generation_number,
        "origin": authorization.origin.value,
        "allowed_revision_kind": authorization.allowed_revision_kind.value,
        "continuation_authorization_id": authorization.id,
        "authorization_state": authorization.authorization_status.value,
        "provider_invoked": False,
    }
    return _write_artifact(
        output_dir=output_dir,
        stem=f"research_continuation_prepare_{post_verdict_intent_id}",
        artifact=artifact,
    )


def _run_authorize_and_generate(
    *,
    continuation_authorization_id: str,
    model: str,
    output_dir: str,
    db_path: str,
) -> str:
    store = SQLiteStore(db_path)
    service = GovernedResearchContinuation(
        store=store,
        scientist=OpenAIResearchContinuationHypothesisScientist(model=model, prompt_version="v6"),
    )
    authorization = service.authorize(continuation_authorization_id)
    result = service.generate_hypothesis(continuation_authorization_id)
    final_authorization = store.get_research_continuation_authorization(continuation_authorization_id) or authorization
    artifact = {
        "mode": "authorize_and_generate",
        "continuation_authorization_id": authorization.id,
        "post_verdict_research_intent_id": authorization.post_verdict_research_intent_id,
        "parent_scientific_verdict_id": authorization.parent_scientific_verdict_id,
        "parent_hypothesis_claim_set_id": authorization.parent_hypothesis_claim_set_id,
        "parent_candidate_id": authorization.parent_candidate_id,
        "frozen_research_scope": authorization.research_scope_payload(),
        "authorization_state": final_authorization.authorization_status.value,
        "generation_number": authorization.generation_number,
        "origin": authorization.origin.value,
        "scientist_prompt_version": "v6",
        "scientist_prompt_hash": __import__("hashlib").sha256(
            get_scientist_instructions("v6").encode("utf-8")
        ).hexdigest(),
        "provider_invocation_id": None if result.invocation is None else result.invocation.id,
        "terminal_attempt_status": _json_safe(result.status),
        "new_candidate_id": None if result.candidate is None else result.candidate.id,
        "new_claim_set_id": None if result.claim_set is None else result.claim_set.id,
        "canonical_child_claims": (
            None
            if result.claim_set is None
            else {
                "id": result.claim_set.id,
                "independent_variable": result.claim_set.independent_variable.value,
                "independent_variable_direction": result.claim_set.independent_variable_direction.value,
                "claim_aggregation": result.claim_set.claim_aggregation.value,
                "claims": [
                    {
                        "outcome": item.outcome.value,
                        "expected_direction": item.expected_direction.value,
                    }
                    for item in result.claim_set.claims
                ],
            }
        ),
        "adaptive_origin": authorization.origin.value,
        "no_downstream_action_occurred": {
            "research_designer_invoked": False,
            "spec_materializer_invoked": False,
            "initial_experiment_executor_invoked": False,
            "scientific_verdict_evaluator_invoked": False,
            "result_evaluator_invoked": False,
            "revision_planner_invoked": False,
            "post_verdict_critic_invoked": False,
            "lifecycle_transition_invoked": False,
        },
    }
    return _write_artifact(
        output_dir=output_dir,
        stem=f"research_continuation_generate_{continuation_authorization_id}",
        artifact=artifact,
    )


def _write_artifact(*, output_dir: str, stem: str, artifact: dict[str, Any]) -> str:
    os.makedirs(output_dir, exist_ok=True)
    timestamp = int(time.time())
    out_path = os.path.join(output_dir, f"{stem}_{timestamp}.json")
    payload = {"timestamp": timestamp, **artifact}
    with open(out_path, "w", encoding="utf-8") as handle:
        json.dump(_json_safe(payload), handle, indent=2, sort_keys=True)
        handle.write("\n")
    return out_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the guarded V0.17 research continuation flow.")
    parser.add_argument("--post-verdict-intent-id")
    parser.add_argument("--continuation-authorization-id")
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--authorize-and-generate", action="store_true")
    parser.add_argument("--model", default="gpt-5.6-terra")
    parser.add_argument("--allow-live-api", action="store_true")
    parser.add_argument("--output-dir", default="artifacts/evals")
    parser.add_argument("--db-path", default="data/ai_quant_scientist.db")
    args = parser.parse_args()
    path = run_live_research_continuation(
        post_verdict_intent_id=args.post_verdict_intent_id,
        continuation_authorization_id=args.continuation_authorization_id,
        model=args.model,
        prepare=args.prepare,
        authorize_and_generate=args.authorize_and_generate,
        allow_live_api=args.allow_live_api,
        output_dir=args.output_dir,
        db_path=args.db_path,
    )
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
