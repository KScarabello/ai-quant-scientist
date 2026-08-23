"""Supervised end-to-end scientist cycle orchestration.

This module coordinates existing bounded AI and deterministic services.
It does not add scientific authority or bypass human acceptance.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum

from ..capabilities.intake import GovernedResearchIntake
from ..models.hypothesis_scientist import (
    HypothesisScientistDecisionType,
    HypothesisScientistInvocation,
    ResearchBrief,
)
from .hypothesis_scientist import HypothesisScientist, generate_candidate
from .research_designer import GovernedResearchDesigner, ResearchDesigner
from .scientific_verdict import ScientificVerdictEvaluator
from .spec_materialization import (
    GovernedSpecMaterialization,
    InitialExperimentExecutor,
    InitialExperimentPlanProposalStatus,
    MaterializationBlockedError,
)


class SupervisedResearchCyclePreparationStatus(str, Enum):
    NO_HYPOTHESIS = "NO_HYPOTHESIS"
    BLOCKED_CAPABILITY = "BLOCKED_CAPABILITY"
    NO_VALID_DESIGN = "NO_VALID_DESIGN"
    MATERIALIZATION_INFEASIBLE = "MATERIALIZATION_INFEASIBLE"
    AWAITING_HUMAN_ACCEPTANCE = "AWAITING_HUMAN_ACCEPTANCE"


class SupervisedResearchCycleExecutionStatus(str, Enum):
    COMPLETED = "COMPLETED"
    ACCEPTANCE_FAILED = "ACCEPTANCE_FAILED"
    EXECUTION_FAILED = "EXECUTION_FAILED"


@dataclass(frozen=True, slots=True)
class SupervisedResearchCyclePreparationResult:
    status: SupervisedResearchCyclePreparationStatus
    brief_id: str
    hypothesis_scientist_invocation_id: str
    candidate_id: str | None = None
    hypothesis_claim_set_id: str | None = None
    candidate_feasibility_decision_id: str | None = None
    research_designer_invocation_id: str | None = None
    research_design_intent_id: str | None = None
    research_prediction_plan_id: str | None = None
    initial_experiment_plan_id: str | None = None
    materialization_proposal_id: str | None = None
    message: str | None = None


@dataclass(frozen=True, slots=True)
class SupervisedResearchCycleExecutionResult:
    status: SupervisedResearchCycleExecutionStatus
    materialization_proposal_id: str
    initial_experiment_plan_id: str
    contrast_result_id: str | None = None
    scientific_verdict_id: str | None = None
    message: str | None = None


class SupervisedResearchCycle:
    """Thin supervised orchestrator over the existing governed research services."""

    def __init__(
        self,
        *,
        store,
        registry,
        scientist: HypothesisScientist,
        designer: ResearchDesigner,
        intake: GovernedResearchIntake | None = None,
        governed_designer: GovernedResearchDesigner | None = None,
        governed_materialization: GovernedSpecMaterialization | None = None,
        executor: InitialExperimentExecutor | None = None,
        verdict_evaluator: ScientificVerdictEvaluator | None = None,
    ) -> None:
        self._store = store
        self._registry = registry
        self._scientist = scientist
        self._designer = designer
        self._intake = intake or GovernedResearchIntake(store, registry)
        self._governed_designer = governed_designer or GovernedResearchDesigner(
            store=store,
            registry=registry,
            designer=designer,
        )
        self._governed_materialization = governed_materialization or GovernedSpecMaterialization(
            store=store,
            registry=registry,
        )
        self._executor = executor or InitialExperimentExecutor(store=store)
        self._verdict_evaluator = verdict_evaluator or ScientificVerdictEvaluator(store=store)

    def prepare(self, brief: ResearchBrief) -> SupervisedResearchCyclePreparationResult:
        invocation, candidate = generate_candidate(self._scientist, brief, self._store)
        if candidate is None:
            return self._prepare_no_candidate_result(brief, invocation)

        intake_result = self._intake.submit(candidate)
        feasibility_decision = intake_result.feasibility_decision
        if intake_result.is_blocked:
            return SupervisedResearchCyclePreparationResult(
                status=SupervisedResearchCyclePreparationStatus.BLOCKED_CAPABILITY,
                brief_id=brief.id,
                hypothesis_scientist_invocation_id=invocation.id,
                candidate_id=candidate.id,
                hypothesis_claim_set_id=invocation.resulting_claim_set_id,
                candidate_feasibility_decision_id=feasibility_decision.id,
                message="Candidate feasibility gate returned BLOCKED_CAPABILITY.",
            )

        design_result = self._governed_designer.generate_design_intent(
            candidate_id=candidate.id,
            candidate_feasibility_decision_id=feasibility_decision.id,
        )
        if design_result.design_intent is None:
            if (
                design_result.decision is not None
                and design_result.decision.decision_type.value == "NO_VALID_DESIGN"
            ):
                return SupervisedResearchCyclePreparationResult(
                    status=SupervisedResearchCyclePreparationStatus.NO_VALID_DESIGN,
                    brief_id=brief.id,
                    hypothesis_scientist_invocation_id=invocation.id,
                    candidate_id=candidate.id,
                    hypothesis_claim_set_id=invocation.resulting_claim_set_id,
                    candidate_feasibility_decision_id=feasibility_decision.id,
                    research_designer_invocation_id=design_result.invocation.id,
                    message=design_result.decision.no_valid_design_reason,
                )
            raise RuntimeError(
                "Research Designer did not produce an authoritative ResearchDesignIntent"
            )

        try:
            materialized = self._governed_materialization.materialize(
                candidate,
                design_result.design_intent,
                prediction_plan=design_result.prediction_plan,
                candidate_feasibility_decision_id=feasibility_decision.id,
            )
        except MaterializationBlockedError as exc:
            return SupervisedResearchCyclePreparationResult(
                status=SupervisedResearchCyclePreparationStatus.MATERIALIZATION_INFEASIBLE,
                brief_id=brief.id,
                hypothesis_scientist_invocation_id=invocation.id,
                candidate_id=candidate.id,
                hypothesis_claim_set_id=invocation.resulting_claim_set_id,
                candidate_feasibility_decision_id=feasibility_decision.id,
                research_designer_invocation_id=design_result.invocation.id,
                research_design_intent_id=design_result.design_intent.id,
                research_prediction_plan_id=(
                    None if design_result.prediction_plan is None else design_result.prediction_plan.id
                ),
                message=str(exc),
            )

        if materialized.proposal.status != InitialExperimentPlanProposalStatus.PROPOSED:
            return SupervisedResearchCyclePreparationResult(
                status=SupervisedResearchCyclePreparationStatus.MATERIALIZATION_INFEASIBLE,
                brief_id=brief.id,
                hypothesis_scientist_invocation_id=invocation.id,
                candidate_id=candidate.id,
                hypothesis_claim_set_id=invocation.resulting_claim_set_id,
                candidate_feasibility_decision_id=feasibility_decision.id,
                research_designer_invocation_id=design_result.invocation.id,
                research_design_intent_id=design_result.design_intent.id,
                research_prediction_plan_id=(
                    None if design_result.prediction_plan is None else design_result.prediction_plan.id
                ),
                initial_experiment_plan_id=materialized.plan.id,
                materialization_proposal_id=materialized.proposal.id,
                message="Exact materialization feasibility did not pass for every required condition.",
            )

        return SupervisedResearchCyclePreparationResult(
            status=SupervisedResearchCyclePreparationStatus.AWAITING_HUMAN_ACCEPTANCE,
            brief_id=brief.id,
            hypothesis_scientist_invocation_id=invocation.id,
            candidate_id=candidate.id,
            hypothesis_claim_set_id=invocation.resulting_claim_set_id,
            candidate_feasibility_decision_id=feasibility_decision.id,
            research_designer_invocation_id=design_result.invocation.id,
            research_design_intent_id=design_result.design_intent.id,
            research_prediction_plan_id=(
                None if design_result.prediction_plan is None else design_result.prediction_plan.id
            ),
            initial_experiment_plan_id=materialized.plan.id,
            materialization_proposal_id=materialized.proposal.id,
            message="Preparation completed. Explicit human acceptance is required before execution.",
        )

    def accept_and_execute(self, proposal_id: str) -> SupervisedResearchCycleExecutionResult:
        proposal = self._store.get_initial_experiment_plan_proposal(proposal_id)
        if proposal is None:
            raise KeyError(f"InitialExperimentPlanProposal not found: {proposal_id!r}")
        plan = self._store.get_initial_experiment_plan(proposal.plan_id)
        if plan is None:
            raise KeyError(f"InitialExperimentPlan not found: {proposal.plan_id!r}")

        if proposal.status == InitialExperimentPlanProposalStatus.REJECTED:
            return SupervisedResearchCycleExecutionResult(
                status=SupervisedResearchCycleExecutionStatus.ACCEPTANCE_FAILED,
                materialization_proposal_id=proposal.id,
                initial_experiment_plan_id=plan.id,
                message="Rejected plan proposals cannot be accepted for execution.",
            )

        if proposal.status == InitialExperimentPlanProposalStatus.PROPOSED:
            try:
                self._governed_materialization.accept_proposal(proposal.id)
            except Exception as exc:
                return SupervisedResearchCycleExecutionResult(
                    status=SupervisedResearchCycleExecutionStatus.ACCEPTANCE_FAILED,
                    materialization_proposal_id=proposal.id,
                    initial_experiment_plan_id=plan.id,
                    message=str(exc),
                )

        try:
            contrast_result = self._executor.execute_plan(plan.id)
        except Exception as exc:
            return SupervisedResearchCycleExecutionResult(
                status=SupervisedResearchCycleExecutionStatus.EXECUTION_FAILED,
                materialization_proposal_id=proposal.id,
                initial_experiment_plan_id=plan.id,
                message=str(exc),
            )

        scientific_verdict_id = None
        verdict_message = "Deterministic execution completed and persisted a contrast result."
        if plan.research_prediction_plan_id is not None:
            try:
                verdict = self._verdict_evaluator.evaluate_plan(plan.id)
            except Exception as exc:
                return SupervisedResearchCycleExecutionResult(
                    status=SupervisedResearchCycleExecutionStatus.EXECUTION_FAILED,
                    materialization_proposal_id=proposal.id,
                    initial_experiment_plan_id=plan.id,
                    contrast_result_id=contrast_result.id,
                    message=str(exc),
                )
            scientific_verdict_id = verdict.id
            verdict_message = (
                "Deterministic execution completed and persisted both a contrast result and a scientific verdict."
            )

        return SupervisedResearchCycleExecutionResult(
            status=SupervisedResearchCycleExecutionStatus.COMPLETED,
            materialization_proposal_id=proposal.id,
            initial_experiment_plan_id=plan.id,
            contrast_result_id=contrast_result.id,
            scientific_verdict_id=scientific_verdict_id,
            message=verdict_message,
        )

    def _prepare_no_candidate_result(
        self,
        brief: ResearchBrief,
        invocation: HypothesisScientistInvocation,
    ) -> SupervisedResearchCyclePreparationResult:
        parsed = _load_invocation_decision(invocation)
        decision_type = parsed.get("decision_type")
        if decision_type == HypothesisScientistDecisionType.NO_HYPOTHESIS.value:
            return SupervisedResearchCyclePreparationResult(
                status=SupervisedResearchCyclePreparationStatus.NO_HYPOTHESIS,
                brief_id=brief.id,
                hypothesis_scientist_invocation_id=invocation.id,
                message=parsed.get("no_hypothesis_reason"),
            )
        raise RuntimeError(
            "Hypothesis Scientist did not produce an authoritative ResearchCandidate"
        )


def _load_invocation_decision(invocation: HypothesisScientistInvocation) -> dict[str, object]:
    if not invocation.parsed_decision_json:
        return {}
    parsed = json.loads(invocation.parsed_decision_json)
    if not isinstance(parsed, dict):
        raise ValueError("Hypothesis scientist invocation parsed_decision_json must decode to an object")
    return parsed
