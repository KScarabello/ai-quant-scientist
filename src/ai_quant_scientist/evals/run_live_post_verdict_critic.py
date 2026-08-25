"""Guarded live runner for the bounded V0.16 post-verdict Critic."""

from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime
from enum import Enum
from typing import Any

from ai_quant_scientist.services.openai_post_verdict_research_critic import OpenAIPostVerdictResearchCritic
from ai_quant_scientist.services.post_verdict_research_critic import GovernedPostVerdictResearchCritic
from ai_quant_scientist.services.post_verdict_research_critic_prompts import (
    get_post_verdict_research_critic_prompt_hash,
)
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


def run_live_post_verdict_critic(
    *,
    scientific_verdict_id: str,
    model: str = "gpt-5.6-terra",
    allow_live_api: bool = False,
    output_dir: str = "artifacts/evals",
    db_path: str = "data/ai_quant_scientist.db",
) -> str:
    if not allow_live_api:
        raise RuntimeError("Live API calls are disabled. Add --allow-live-api to enable.")
    store = SQLiteStore(db_path)
    critic = OpenAIPostVerdictResearchCritic(model=model)
    service = GovernedPostVerdictResearchCritic(store=store, critic=critic)
    result = service.critique(scientific_verdict_id)
    artifact = _build_artifact(store=store, result=result)
    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, f"post_verdict_critic_{scientific_verdict_id}_{int(time.time())}.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(_json_safe(artifact), handle, indent=2, sort_keys=True)
        handle.write("\n")
    return path


def _build_artifact(*, store: SQLiteStore, result) -> dict[str, Any]:
    invocation = result.invocation
    intent = result.intent
    verdict = store.get_scientific_verdict(intent.scientific_verdict_id)
    claim_set = store.get_hypothesis_claim_set(intent.hypothesis_claim_set_id)
    return {
        "timestamp": int(time.time()),
        "mode": "post_verdict_critic",
        "scientific_verdict_id": intent.scientific_verdict_id,
        "prior_overall_verdict": None if verdict is None else verdict.overall_status.value,
        "research_scope": intent.research_scope_payload(),
        "frozen_hypothesis_claims": None if claim_set is None else [
            {
                "outcome": item.outcome.value,
                "expected_direction": item.expected_direction.value,
            }
            for item in claim_set.claims
        ],
        "critic_prompt_version": intent.prompt_version,
        "critic_prompt_hash": get_post_verdict_research_critic_prompt_hash(intent.prompt_version),
        "critic_invocation_id": invocation.id,
        "post_verdict_research_intent_id": intent.id,
        "decision": intent.decision.value,
        "revision_kind": intent.revision_kind.value,
        "diagnosis": intent.diagnosis,
        "next_step_rationale": intent.next_step_rationale,
        "reused_existing_authoritative_intent": result.reused_existing,
        "no_downstream_action_occurred": {
            "hypothesis_scientist_invoked": False,
            "research_designer_invoked": False,
            "spec_materializer_invoked": False,
            "initial_experiment_executor_invoked": False,
            "scientific_verdict_evaluator_invoked": False,
            "revision_planner_invoked": False,
            "result_evaluator_invoked": False,
            "lifecycle_transition_invoked": False,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the bounded V0.16 post-verdict Critic once.")
    parser.add_argument("--scientific-verdict-id", required=True)
    parser.add_argument("--model", default="gpt-5.6-terra")
    parser.add_argument("--allow-live-api", action="store_true")
    parser.add_argument("--output-dir", default="artifacts/evals")
    parser.add_argument("--db-path", default="data/ai_quant_scientist.db")
    args = parser.parse_args()
    path = run_live_post_verdict_critic(
        scientific_verdict_id=args.scientific_verdict_id,
        model=args.model,
        allow_live_api=args.allow_live_api,
        output_dir=args.output_dir,
        db_path=args.db_path,
    )
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
