"""Guarded live runner for the supervised end-to-end scientist cycle.

Preparation mode requires --allow-live-api to make real AI calls.
Acceptance/execution mode performs zero AI/network calls and requires an exact
persisted proposal ID.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime
from enum import Enum
from typing import Any, List

from ai_quant_scientist.capabilities import build_v1_registry
from ai_quant_scientist.models.design import InitialExperimentPlanProposalStatus
from ai_quant_scientist.models.hypothesis_scientist import ResearchBrief
from ai_quant_scientist.services.openai_hypothesis_scientist import OpenAIHypothesisScientist
from ai_quant_scientist.services.openai_research_designer import OpenAIResearchDesigner
from ai_quant_scientist.services.supervised_research_cycle import (
    SupervisedResearchCycle,
    SupervisedResearchCycleExecutionResult,
    SupervisedResearchCyclePreparationResult,
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


class _UnusedScientist:
    provider = "unused"
    model = "unused"
    prompt_version = "v3"

    def generate(self, brief):
        raise AssertionError("Acceptance/execution mode must not invoke Hypothesis Scientist")


class _UnusedDesigner:
    provider = "unused"
    model = "unused"
    prompt_version = "v1"

    def design(self, context):
        raise AssertionError("Acceptance/execution mode must not invoke Research Designer")


def build_supported_supervised_cycle_brief() -> ResearchBrief:
    return ResearchBrief.create(
        research_question=(
            "For this bounded synthetic smoke test, does making a signal threshold "
            "stricter change trade frequency and risk-adjusted performance for the same "
            "synthetic strategy logic when a synthetic-parametric dataset is treated as the "
            "prerequisite input?"
        ),
        asset_class_focus="SYNTHETIC",
        methodological_constraints=[
            "Treat the synthetic-parametric dataset as the prerequisite input for this smoke test.",
            "Do not require a separate synthetic-data-generation tool for this bounded fixture.",
            "Use deterministic backtest execution outputs and the downstream deterministic contrast calculation as the measurement path.",
            "Do not require a separate statistical-analysis tool for this bounded fixture.",
            "Do not invent any tool prerequisite other than BACKTEST_EXECUTION.",
            "Do not assume real market data or live trading capabilities.",
            "Focus on a single falsifiable hypothesis that can be expressed as a parameter-sensitivity design without exact execution values.",
        ],
        exclusions=[
            "Do not propose exact execution parameter values.",
            "Do not mention capability IDs.",
            "Do not assume autonomous execution approval.",
        ],
        source="live_supervised_cycle_v1",
    )


def run_live_supervised_cycle(
    *,
    model: str = "gpt-5.6-terra",
    allow_live_api: bool = False,
    proposal_id: str | None = None,
    accept_and_execute: bool = False,
    output_dir: str = "artifacts/evals",
    db_path: str = "data/ai_quant_scientist.db",
) -> str:
    if accept_and_execute:
        if not proposal_id:
            raise RuntimeError("Acceptance/execution mode requires --proposal-id")
        return _run_acceptance_and_execution(
            proposal_id=proposal_id,
            output_dir=output_dir,
            db_path=db_path,
        )

    if proposal_id is not None:
        raise RuntimeError("--proposal-id is only valid together with --accept-and-execute")
    if not allow_live_api:
        raise RuntimeError("Live API calls are disabled. Add --allow-live-api to enable.")
    return _run_preparation(
        model=model,
        output_dir=output_dir,
        db_path=db_path,
    )


def _run_preparation(
    *,
    model: str,
    output_dir: str,
    db_path: str,
) -> str:
    store = SQLiteStore(db_path)
    registry = build_v1_registry()
    scientist = OpenAIHypothesisScientist(model=model, prompt_version="v3")
    designer = OpenAIResearchDesigner(model=model, prompt_version="v1")
    cycle = SupervisedResearchCycle(
        store=store,
        registry=registry,
        scientist=scientist,
        designer=designer,
    )

    brief = build_supported_supervised_cycle_brief()
    preparation = cycle.prepare(brief)
    artifact = _build_preparation_artifact(
        store=store,
        model=model,
        brief=brief,
        preparation=preparation,
    )
    return _write_artifact(
        output_dir=output_dir,
        stem=f"supervised_cycle_prepare_{model}",
        artifact=artifact,
    )


def _run_acceptance_and_execution(
    *,
    proposal_id: str,
    output_dir: str,
    db_path: str,
) -> str:
    store = SQLiteStore(db_path)
    cycle = SupervisedResearchCycle(
        store=store,
        registry=build_v1_registry(),
        scientist=_UnusedScientist(),
        designer=_UnusedDesigner(),
    )
    execution = cycle.accept_and_execute(proposal_id)
    artifact = _build_acceptance_execution_artifact(
        store=store,
        proposal_id=proposal_id,
        execution=execution,
    )
    return _write_artifact(
        output_dir=output_dir,
        stem=f"supervised_cycle_execute_{proposal_id}",
        artifact=artifact,
    )


def _build_preparation_artifact(
    *,
    store: SQLiteStore,
    model: str,
    brief: ResearchBrief,
    preparation: SupervisedResearchCyclePreparationResult,
) -> dict[str, Any]:
    hypothesis_invocation = _require_present(
        store.get_hypothesis_scientist_invocation(preparation.hypothesis_scientist_invocation_id),
        f"HypothesisScientistInvocation not found: {preparation.hypothesis_scientist_invocation_id!r}",
    )
    candidate = (
        store.get_research_candidate(preparation.candidate_id)
        if preparation.candidate_id is not None
        else None
    )
    feasibility = (
        store.get_feasibility_decision(preparation.candidate_feasibility_decision_id)
        if preparation.candidate_feasibility_decision_id is not None
        else None
    )
    designer_invocation = (
        _require_present(
            store.get_research_designer_invocation(preparation.research_designer_invocation_id),
            f"ResearchDesignerInvocation not found: {preparation.research_designer_invocation_id!r}",
        )
        if preparation.research_designer_invocation_id is not None
        else None
    )
    design_intent = (
        store.get_research_design_intent(preparation.research_design_intent_id)
        if preparation.research_design_intent_id is not None
        else None
    )
    plan = (
        store.get_initial_experiment_plan(preparation.initial_experiment_plan_id)
        if preparation.initial_experiment_plan_id is not None
        else None
    )
    proposal = (
        store.get_initial_experiment_plan_proposal(preparation.materialization_proposal_id)
        if preparation.materialization_proposal_id is not None
        else None
    )

    return {
        "mode": "prepare",
        "models": {
            "hypothesis_scientist": model,
            "research_designer": model,
        },
        "prompt_versions": {
            "hypothesis_scientist": hypothesis_invocation.prompt_version,
            "research_designer": None if designer_invocation is None else designer_invocation.prompt_version,
        },
        "brief": _json_safe(
            {
                "id": brief.id,
                "research_question": brief.research_question,
                "asset_class_focus": brief.asset_class_focus,
                "instrument_focus": brief.instrument_focus,
                "methodological_constraints": brief.methodological_constraints,
                "exclusions": brief.exclusions,
                "source": brief.source,
            }
        ),
        "hypothesis_scientist_invocation_id": hypothesis_invocation.id,
        "hypothesis_decision": _parsed_json_object(hypothesis_invocation.parsed_decision_json),
        "candidate_id": None if candidate is None else candidate.id,
        "candidate_feasibility_decision": _json_safe(
            {
                "id": None if feasibility is None else feasibility.id,
                "status": None if feasibility is None else feasibility.gate_decision.value,
                "registry_version": None if feasibility is None else feasibility.registry_version,
                "registry_fingerprint": None if feasibility is None else feasibility.registry_fingerprint,
            }
        ),
        "research_designer_invocation_id": None if designer_invocation is None else designer_invocation.id,
        "designer_decision": (
            {}
            if designer_invocation is None
            else _parsed_json_object(designer_invocation.parsed_decision_json)
        ),
        "ontology_versions": {
            "hypothesis_scientist": _parsed_json_object(hypothesis_invocation.parsed_decision_json).get("ontology_version"),
            "research_designer": None if design_intent is None else design_intent.ontology_version,
        },
        "ontology_fingerprints": {
            "hypothesis_scientist": _parsed_json_object(hypothesis_invocation.parsed_decision_json).get("ontology_fingerprint"),
            "research_designer": None if design_intent is None else design_intent.ontology_fingerprint,
        },
        "research_design_intent_id": None if design_intent is None else design_intent.id,
        "initial_experiment_plan_id": None if plan is None else plan.id,
        "materialization_proposal_id": None if proposal is None else proposal.id,
        "exact_materialized_conditions": _condition_summaries(plan),
        "preparation_outcome": preparation.status.value,
        "preparation_message": preparation.message,
        "human_acceptance_occurred": bool(
            proposal is not None
            and proposal.status
            in (
                InitialExperimentPlanProposalStatus.ACCEPTED,
                InitialExperimentPlanProposalStatus.RUNNING,
                InitialExperimentPlanProposalStatus.COMPLETED,
            )
        ),
        "execution_outcome": None,
        "execution_message": None,
        "execution_records": [],
        "contrast_result": None,
    }


def _build_acceptance_execution_artifact(
    *,
    store: SQLiteStore,
    proposal_id: str,
    execution: SupervisedResearchCycleExecutionResult,
) -> dict[str, Any]:
    proposal = _require_present(
        store.get_initial_experiment_plan_proposal(proposal_id),
        f"InitialExperimentPlanProposal not found: {proposal_id!r}",
    )
    plan = _require_present(
        store.get_initial_experiment_plan(proposal.plan_id),
        f"InitialExperimentPlan not found: {proposal.plan_id!r}",
    )
    candidate = _require_present(
        store.get_research_candidate(plan.candidate_id),
        f"ResearchCandidate not found: {plan.candidate_id!r}",
    )
    feasibility = _require_present(
        store.get_feasibility_decision(plan.candidate_feasibility_decision_id),
        f"Feasibility decision not found: {plan.candidate_feasibility_decision_id!r}",
    )
    design_intent = _require_present(
        store.get_research_design_intent(plan.design_intent_id),
        f"ResearchDesignIntent not found: {plan.design_intent_id!r}",
    )
    hypothesis_invocation = store.find_hypothesis_scientist_invocation_by_resulting_candidate_id(candidate.id)
    designer_invocation = store.find_research_designer_invocation_by_resulting_design_intent_id(design_intent.id)
    execution_records = store.list_condition_execution_records(plan.id)
    contrast_result = store.get_parameter_sensitivity_contrast_result(plan.id)

    return {
        "mode": "accept_and_execute",
        "requested_proposal_id": proposal_id,
        "hypothesis_scientist_invocation_id": None if hypothesis_invocation is None else hypothesis_invocation.id,
        "research_designer_invocation_id": None if designer_invocation is None else designer_invocation.id,
        "candidate_id": candidate.id,
        "candidate_feasibility_decision": _json_safe(
            {
                "id": feasibility.id,
                "status": feasibility.gate_decision.value,
                "registry_version": feasibility.registry_version,
                "registry_fingerprint": feasibility.registry_fingerprint,
            }
        ),
        "research_design_intent_id": design_intent.id,
        "initial_experiment_plan_id": plan.id,
        "materialization_proposal_id": proposal.id,
        "exact_materialized_conditions": _condition_summaries(plan),
        "preparation_outcome": InitialExperimentPlanProposalStatus.PROPOSED.value,
        "preparation_message": "Loaded exact persisted proposal for explicit acceptance/execution.",
        "human_acceptance_occurred": proposal.status in (
            InitialExperimentPlanProposalStatus.ACCEPTED,
            InitialExperimentPlanProposalStatus.RUNNING,
            InitialExperimentPlanProposalStatus.COMPLETED,
        ),
        "execution_outcome": execution.status.value,
        "execution_message": execution.message,
        "execution_records": _execution_record_summaries(execution_records),
        "contrast_result": _contrast_result_summary(contrast_result),
    }


def _write_artifact(*, output_dir: str, stem: str, artifact: dict[str, Any]) -> str:
    os.makedirs(output_dir, exist_ok=True)
    timestamp = int(time.time())
    out_path = os.path.join(output_dir, f"{stem}_{timestamp}.json")
    payload = {"timestamp": timestamp, **artifact}
    with open(out_path, "w", encoding="utf-8") as handle:
        json.dump(_json_safe(payload), handle, indent=2)
    return out_path


def _condition_summaries(plan) -> list[dict[str, Any]]:
    if plan is None:
        return []
    return [
        {
            "condition_id": condition.id,
            "ordinal": condition.ordinal,
            "role": condition.role.value,
            "selected_capability_id": condition.selected_capability_id,
            "exact_parameters": dict(condition.exact_parameters),
        }
        for condition in plan.ordered_conditions
    ]


def _execution_record_summaries(records) -> list[dict[str, Any]]:
    return [
        {
            "condition_id": record.condition_id,
            "ordinal": record.ordinal,
            "role": record.role.value,
            "status": record.status.value,
            "metrics": dict(record.metrics),
            "tool_name": record.tool_name,
        }
        for record in records
    ]


def _contrast_result_summary(contrast_result) -> dict[str, Any] | None:
    if contrast_result is None:
        return None
    return {
        "id": contrast_result.id,
        "plan_id": contrast_result.plan_id,
        "independent_variable": contrast_result.independent_variable.value,
        "baseline_parameter_value": contrast_result.baseline_parameter_value,
        "comparator_parameter_value": contrast_result.comparator_parameter_value,
        "outcomes": [
            {
                "outcome": outcome.outcome.value,
                "baseline_value": outcome.baseline_value,
                "comparator_value": outcome.comparator_value,
                "delta": outcome.delta,
            }
            for outcome in contrast_result.outcomes
        ],
    }


def _parsed_json_object(value: str | None) -> dict[str, Any]:
    if value is None:
        return {}
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise ValueError("Expected parsed JSON object")
    return parsed


def _require_present(value, message: str):
    if value is None:
        raise KeyError(message)
    return value


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run live supervised scientist cycle")
    parser.add_argument("--model", default="gpt-5.6-terra")
    parser.add_argument("--allow-live-api", action="store_true")
    parser.add_argument("--proposal-id", default=None)
    parser.add_argument("--accept-and-execute", action="store_true")
    parser.add_argument("--out", default="artifacts/evals")
    parser.add_argument("--db-path", default="data/ai_quant_scientist.db")
    args = parser.parse_args(argv)

    if args.accept_and_execute:
        if not args.proposal_id:
            print("Refusing to execute without an explicit --proposal-id.")
            return 2
        out = run_live_supervised_cycle(
            model=args.model,
            proposal_id=args.proposal_id,
            accept_and_execute=True,
            output_dir=args.out,
            db_path=args.db_path,
        )
        print(f"Wrote results to {out}")
        print(f"Executed exact persisted proposal: {args.proposal_id}")
        return 0

    if args.proposal_id is not None:
        print("--proposal-id is only valid together with --accept-and-execute.")
        return 2
    if not args.allow_live_api:
        print("Refusing to run live API calls. Add --allow-live-api to proceed.")
        return 2

    out = run_live_supervised_cycle(
        model=args.model,
        allow_live_api=args.allow_live_api,
        output_dir=args.out,
        db_path=args.db_path,
    )
    with open(out, "r", encoding="utf-8") as handle:
        artifact = json.load(handle)
    print(f"Wrote results to {out}")
    outcome = artifact.get("preparation_outcome")
    proposal_id = artifact.get("materialization_proposal_id")
    message = artifact.get("preparation_message")
    if outcome == "AWAITING_HUMAN_ACCEPTANCE":
        print(f"Prepared exact proposal_id: {proposal_id}")
        print(
            "Stopped at AWAITING_HUMAN_ACCEPTANCE. Inspect that exact proposal before a separate acceptance/execution command."
        )
    else:
        print(f"Preparation outcome: {outcome}")
        if message:
            print(f"Message: {message}")
        print("No proposal exists to accept or execute.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
