"""Deterministic contrast-plan materialization, acceptance, and execution."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from ..capabilities.gate import GateDecision, ResearchCandidate
from ..capabilities.models import AnyRequirement, DataKind, DataRequirement, ToolKind, ToolRequirement
from ..capabilities.registry import CapabilityRegistry
from ..models.design import (
    AnalysisIntent,
    ComparisonIntent,
    ConditionExecutionRecord,
    ConditionExecutionStatus,
    DesignOutcome,
    DesignVariable,
    ExperimentCondition,
    ExperimentConditionRole,
    InitialExperimentCompletionRule,
    InitialExperimentPlan,
    InitialExperimentPlanMaterializationResult,
    InitialExperimentPlanProposal,
    InitialExperimentPlanProposalStatus,
    OutcomeContrast,
    ParameterSensitivityContrastResult,
    ResearchDesignIntent,
    ResearchDesignKind,
    SpecFeasibilityDecision,
    SpecFeasibilityPhase,
    SpecFeasibilityReasonCode,
    SpecFeasibilityStatus,
    thaw_mapping,
)
from ..models.research import ExperimentResult, freeze_json_value, new_id, record_to_state
from ..tools.stub_backtester import StubBacktester
from ..tools.stub_execution_contract import supported_parameter_names, validate_stub_execution_parameters

MATERIALIZER_VERSION = "spec_materializer_v2"
MATERIALIZATION_POLICY_VERSION = "stub_spec_materialization_policy_v2"
SPEC_FEASIBILITY_VERSION = "spec_feasibility_v1"
DEFAULT_INITIAL_MAX_ITERATIONS = 3


class MaterializationBlockedError(RuntimeError):
    """Raised when a candidate or design intent cannot enter materialization."""


@dataclass(frozen=True, slots=True)
class StubMaterializationPolicy:
    version: str
    selected_capability_id: str
    baseline_parameters: Mapping[str, Any]
    comparator_parameters: Mapping[str, Any]
    supported_design_kind: ResearchDesignKind
    supported_independent_variable: DesignVariable
    required_controls: tuple[DesignVariable, ...]
    supported_outcomes: tuple[DesignOutcome, ...]
    comparison_intent: ComparisonIntent
    analysis_intent: AnalysisIntent
    completion_rule: InitialExperimentCompletionRule

    def __post_init__(self) -> None:
        object.__setattr__(self, "baseline_parameters", freeze_json_value(dict(self.baseline_parameters)))
        object.__setattr__(self, "comparator_parameters", freeze_json_value(dict(self.comparator_parameters)))

    def fingerprint(self) -> str:
        payload = {
            "version": self.version,
            "selected_capability_id": self.selected_capability_id,
            "baseline_parameters": dict(self.baseline_parameters),
            "comparator_parameters": dict(self.comparator_parameters),
            "supported_design_kind": self.supported_design_kind.value,
            "supported_independent_variable": self.supported_independent_variable.value,
            "required_controls": [item.value for item in self.required_controls],
            "supported_outcomes": [item.value for item in self.supported_outcomes],
            "comparison_intent": self.comparison_intent.value,
            "analysis_intent": self.analysis_intent.value,
            "completion_rule": self.completion_rule.value,
        }
        canon = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canon.encode("utf-8")).hexdigest()

    def snapshot(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "selected_capability_id": self.selected_capability_id,
            "baseline_parameters": dict(self.baseline_parameters),
            "comparator_parameters": dict(self.comparator_parameters),
            "supported_design_kind": self.supported_design_kind.value,
            "supported_independent_variable": self.supported_independent_variable.value,
            "required_controls": [item.value for item in self.required_controls],
            "supported_outcomes": [item.value for item in self.supported_outcomes],
            "comparison_intent": self.comparison_intent.value,
            "analysis_intent": self.analysis_intent.value,
            "completion_rule": self.completion_rule.value,
            "fingerprint": self.fingerprint(),
        }


def build_stub_materialization_policy() -> StubMaterializationPolicy:
    return StubMaterializationPolicy(
        version=MATERIALIZATION_POLICY_VERSION,
        selected_capability_id="stub_backtester_v1",
        baseline_parameters={"signal_threshold": 2.0, "lookback": 20},
        comparator_parameters={"signal_threshold": 2.5, "lookback": 20},
        supported_design_kind=ResearchDesignKind.PARAMETER_SENSITIVITY,
        supported_independent_variable=DesignVariable.SIGNAL_THRESHOLD,
        required_controls=(DesignVariable.LOOKBACK,),
        supported_outcomes=(
            DesignOutcome.TRADE_COUNT,
            DesignOutcome.NET_PNL,
            DesignOutcome.SHARPE,
        ),
        comparison_intent=ComparisonIntent.CONTRAST_PARAMETER_LEVELS,
        analysis_intent=AnalysisIntent.SENSITIVITY_ANALYSIS,
        completion_rule=InitialExperimentCompletionRule.ALL_CONDITIONS_REQUIRED,
    )


class SpecFeasibilityValidator:
    """Validate exact stub-only condition payloads against the selected implementation."""

    def validate(
        self,
        *,
        candidate_id: str,
        design_intent_id: str,
        selected_capability_id: str,
        proposed_parameters: Mapping[str, Any],
        registry: CapabilityRegistry,
        materializer_version: str,
        plan_id: str | None = None,
        condition_id: str | None = None,
        phase: SpecFeasibilityPhase = SpecFeasibilityPhase.MATERIALIZATION,
    ) -> SpecFeasibilityDecision:
        capability = next(
            (item for item in registry.list_capabilities() if item.capability_id == selected_capability_id),
            None,
        )

        reason_codes: list[SpecFeasibilityReasonCode] = []
        notes: list[str] = []

        if capability is None:
            reason_codes.append(SpecFeasibilityReasonCode.CAPABILITY_NOT_FOUND)
            notes.append(f"Capability not registered: {selected_capability_id}")
        else:
            if not capability.enabled:
                reason_codes.append(SpecFeasibilityReasonCode.CAPABILITY_DISABLED)
                notes.append(f"Capability disabled: {selected_capability_id}")
            if capability.data_kind != DataKind.SYNTHETIC_PARAMETRIC:
                reason_codes.append(SpecFeasibilityReasonCode.SELECTED_CAPABILITY_INCOMPATIBLE)
                notes.append(
                    f"Capability data_kind must be {DataKind.SYNTHETIC_PARAMETRIC.value}, "
                    f"got {capability.data_kind.value}"
                )
            supported_tool_kinds = set(capability.supported_tool_kinds or ())
            if ToolKind.BACKTEST_EXECUTION not in supported_tool_kinds:
                reason_codes.append(SpecFeasibilityReasonCode.SELECTED_CAPABILITY_INCOMPATIBLE)
                notes.append("Capability does not declare BACKTEST_EXECUTION support")

        if not isinstance(proposed_parameters, Mapping):
            reason_codes.append(SpecFeasibilityReasonCode.SPEC_PAYLOAD_UNSUPPORTED)
            notes.append("Proposed parameters payload must be a mapping")
        else:
            validation_notes = validate_stub_execution_parameters(proposed_parameters)
            for note in validation_notes:
                if note.startswith("missing:"):
                    reason_codes.append(SpecFeasibilityReasonCode.REQUIRED_PARAMETER_MISSING)
                    notes.append(note)
                elif note.startswith("unsupported:"):
                    reason_codes.append(SpecFeasibilityReasonCode.UNSUPPORTED_PARAMETER)
                    notes.append(note)
                elif note.startswith("type:"):
                    reason_codes.append(SpecFeasibilityReasonCode.PARAMETER_TYPE_INVALID)
                    notes.append(note)

            if capability is not None and capability.supported_parameters is not None:
                unsupported = sorted(set(proposed_parameters.keys()) - set(capability.supported_parameters))
                if unsupported:
                    reason_codes.append(SpecFeasibilityReasonCode.UNSUPPORTED_PARAMETER)
                    notes.append(
                        f"Capability does not declare support for parameters: {unsupported}"
                    )

        status = SpecFeasibilityStatus.PASS if not reason_codes else SpecFeasibilityStatus.FAIL
        return SpecFeasibilityDecision(
            id=new_id(),
            candidate_id=candidate_id,
            design_intent_id=design_intent_id,
            selected_capability_id=selected_capability_id,
            status=status,
            reason_codes=tuple(reason_codes),
            proposed_parameters=dict(proposed_parameters),
            validation_notes="; ".join(notes) if notes else "Exact condition payload is supported",
            spec_feasibility_version=SPEC_FEASIBILITY_VERSION,
            registry_version=registry.version,
            registry_fingerprint=registry.fingerprint,
            materializer_version=materializer_version,
            plan_id=plan_id,
            condition_id=condition_id,
            phase=phase,
        )


class SpecMaterializer:
    """Deterministically map bounded scientific intent to a precommitted contrast plan."""

    def __init__(
        self,
        *,
        policy: StubMaterializationPolicy | None = None,
        feasibility_validator: SpecFeasibilityValidator | None = None,
    ) -> None:
        self.policy = policy or build_stub_materialization_policy()
        self.feasibility_validator = feasibility_validator or SpecFeasibilityValidator()

    def materialize(
        self,
        *,
        candidate: ResearchCandidate,
        design_intent: ResearchDesignIntent,
        candidate_feasibility_decision,
        registry: CapabilityRegistry,
    ) -> InitialExperimentPlanMaterializationResult:
        decision_value = getattr(
            candidate_feasibility_decision,
            "gate_decision",
            getattr(candidate_feasibility_decision, "decision", None),
        )
        if decision_value != GateDecision.READY_FOR_SPEC:
            raise MaterializationBlockedError("Candidate is not authorized READY_FOR_SPEC")
        if getattr(candidate_feasibility_decision, "candidate_id", None) != candidate.id:
            raise MaterializationBlockedError("Candidate feasibility decision does not belong to candidate")
        if design_intent.candidate_id != candidate.id:
            raise MaterializationBlockedError("ResearchDesignIntent does not belong to candidate")
        if design_intent.design_kind != self.policy.supported_design_kind:
            raise MaterializationBlockedError(
                f"Unsupported design_kind for V0.13A.1 materializer: {design_intent.design_kind.value}"
            )
        if tuple(design_intent.independent_variables) != (self.policy.supported_independent_variable,):
            raise MaterializationBlockedError(
                "Independent variables are not supported by the deterministic materializer policy"
            )
        if tuple(design_intent.controls) != self.policy.required_controls:
            raise MaterializationBlockedError(
                "Controls must match the deterministic materializer policy exactly"
            )
        if not set(design_intent.dependent_outcomes).issubset(set(self.policy.supported_outcomes)):
            raise MaterializationBlockedError("Dependent outcomes are not supported by the policy")
        if design_intent.comparison_intent != self.policy.comparison_intent:
            raise MaterializationBlockedError("comparison_intent is not supported by the policy")
        if design_intent.analysis_intent != self.policy.analysis_intent:
            raise MaterializationBlockedError("analysis_intent is not supported by the policy")
        if not _candidate_supports_stub_materialization(candidate.requirements):
            raise MaterializationBlockedError(
                "Candidate requirements do not authorize the stub synthetic execution path"
            )

        plan_id = new_id()
        baseline = ExperimentCondition(
            id=new_id(),
            ordinal=1,
            role=ExperimentConditionRole.BASELINE,
            exact_parameters=self.policy.baseline_parameters,
            selected_capability_id=self.policy.selected_capability_id,
            expected_tool_kind=ToolKind.BACKTEST_EXECUTION.value,
        )
        comparator = ExperimentCondition(
            id=new_id(),
            ordinal=2,
            role=ExperimentConditionRole.COMPARATOR,
            exact_parameters=self.policy.comparator_parameters,
            selected_capability_id=self.policy.selected_capability_id,
            expected_tool_kind=ToolKind.BACKTEST_EXECUTION.value,
        )
        plan = InitialExperimentPlan(
            id=plan_id,
            candidate_id=candidate.id,
            design_intent_id=design_intent.id,
            candidate_feasibility_decision_id=candidate_feasibility_decision.id,
            selected_capability_id=self.policy.selected_capability_id,
            design_kind=design_intent.design_kind,
            independent_variable=self.policy.supported_independent_variable,
            control_variables=self.policy.required_controls,
            dependent_outcomes=design_intent.dependent_outcomes,
            ordered_conditions=(baseline, comparator),
            completion_rule=self.policy.completion_rule,
            materializer_version=MATERIALIZER_VERSION,
            materialization_policy_version=self.policy.version,
            materialization_policy_fingerprint=self.policy.fingerprint(),
            registry_version=registry.version,
            registry_fingerprint=registry.fingerprint,
        )

        feasibility_decisions = tuple(
            self.feasibility_validator.validate(
                candidate_id=candidate.id,
                design_intent_id=design_intent.id,
                selected_capability_id=condition.selected_capability_id,
                proposed_parameters=condition.exact_parameters,
                registry=registry,
                materializer_version=MATERIALIZER_VERSION,
                plan_id=plan.id,
                condition_id=condition.id,
                phase=SpecFeasibilityPhase.MATERIALIZATION,
            )
            for condition in plan.ordered_conditions
        )

        proposal_status = (
            InitialExperimentPlanProposalStatus.PROPOSED
            if all(decision.is_pass for decision in feasibility_decisions)
            else InitialExperimentPlanProposalStatus.REJECTED
        )
        proposal = InitialExperimentPlanProposal(
            id=new_id(),
            plan_id=plan.id,
            candidate_id=candidate.id,
            design_intent_id=design_intent.id,
            candidate_feasibility_decision_id=candidate_feasibility_decision.id,
            materialization_feasibility_decision_ids=tuple(decision.id for decision in feasibility_decisions),
            materialization_trace={
                "scientific_intent": record_to_state(design_intent),
                "policy": self.policy.snapshot(),
                "ordered_conditions": [record_to_state(condition) for condition in plan.ordered_conditions],
                "selected_capability_id": self.policy.selected_capability_id,
            },
            status=proposal_status,
            decided_at=feasibility_decisions[-1].created_at
            if proposal_status == InitialExperimentPlanProposalStatus.REJECTED
            else None,
        )

        return InitialExperimentPlanMaterializationResult(
            design_intent=design_intent,
            plan=plan,
            condition_feasibility_decisions=feasibility_decisions,
            proposal=proposal,
        )


class GovernedSpecMaterialization:
    """Durable supervised initial experiment-plan path for a future Research Designer."""

    def __init__(
        self,
        *,
        store,
        registry: CapabilityRegistry,
        materializer: SpecMaterializer | None = None,
    ) -> None:
        self._store = store
        self._registry = registry
        self._materializer = materializer or SpecMaterializer()

    def submit_design_intent(self, design_intent: ResearchDesignIntent) -> ResearchDesignIntent:
        self._store.save_research_design_intent(design_intent)
        return design_intent

    def materialize(
        self,
        candidate: ResearchCandidate,
        design_intent: ResearchDesignIntent,
        *,
        candidate_feasibility_decision_id: str,
    ) -> InitialExperimentPlanMaterializationResult:
        self._store.save_research_design_intent(design_intent)
        stored_candidate = self._store.get_research_candidate(candidate.id)
        if stored_candidate is None:
            raise KeyError(f"ResearchCandidate not found: {candidate.id!r}")
        candidate_feasibility_decision = self._store.get_feasibility_decision_by_id(candidate_feasibility_decision_id)
        if candidate_feasibility_decision is None:
            raise MaterializationBlockedError("Candidate feasibility decision not found")
        result = self._materializer.materialize(
            candidate=stored_candidate,
            design_intent=design_intent,
            candidate_feasibility_decision=candidate_feasibility_decision,
            registry=self._registry,
        )
        self._store.save_initial_experiment_plan(result.plan)
        for decision in result.condition_feasibility_decisions:
            self._store.save_spec_feasibility_decision(decision)
        self._store.save_initial_experiment_plan_proposal(result.proposal)
        return result

    def materialize_design_intent(
        self,
        design_intent_id: str,
        *,
        candidate_feasibility_decision_id: str,
    ) -> InitialExperimentPlanMaterializationResult:
        design_intent = self._store.get_research_design_intent(design_intent_id)
        if design_intent is None:
            raise KeyError(f"ResearchDesignIntent not found: {design_intent_id!r}")
        candidate = self._store.get_research_candidate(design_intent.candidate_id)
        if candidate is None:
            raise KeyError(f"ResearchCandidate not found: {design_intent.candidate_id!r}")
        return self.materialize(
            candidate,
            design_intent,
            candidate_feasibility_decision_id=candidate_feasibility_decision_id,
        )

    def accept_proposal(self, proposal_id: str) -> InitialExperimentPlanProposal:
        current_fingerprint = self._materializer.policy.fingerprint()
        return self._store.accept_initial_experiment_plan_proposal(
            proposal_id,
            registry=self._registry,
            feasibility_validator=self._materializer.feasibility_validator,
            materializer_version=MATERIALIZER_VERSION,
            current_policy_version=self._materializer.policy.version,
            current_policy_fingerprint=current_fingerprint,
        )


class InitialExperimentExecutor:
    """Deterministically execute accepted V0.13A.1 plans in persisted condition order."""

    def __init__(self, *, store, research_tool: StubBacktester | None = None) -> None:
        self._store = store
        self._research_tool = research_tool or StubBacktester()

    def execute_plan(self, plan_id: str) -> ParameterSensitivityContrastResult:
        plan = self._store.get_initial_experiment_plan(plan_id)
        if plan is None:
            raise KeyError(f"InitialExperimentPlan not found: {plan_id!r}")
        proposal = self._store.get_initial_experiment_plan_proposal_by_plan_id(plan.id)
        if proposal is None:
            raise KeyError(f"InitialExperimentPlanProposal not found for plan: {plan.id!r}")
        if proposal.status not in (
            InitialExperimentPlanProposalStatus.ACCEPTED,
            InitialExperimentPlanProposalStatus.RUNNING,
            InitialExperimentPlanProposalStatus.COMPLETED,
        ):
            raise RuntimeError("Initial experiment plan must be accepted before execution")

        existing_contrast = self._store.get_parameter_sensitivity_contrast_result(plan.id)
        if proposal.status == InitialExperimentPlanProposalStatus.COMPLETED and existing_contrast is not None:
            return existing_contrast

        if proposal.status == InitialExperimentPlanProposalStatus.ACCEPTED:
            self._store.update_initial_experiment_plan_proposal_status(
                proposal.id,
                InitialExperimentPlanProposalStatus.RUNNING,
            )

        existing_records = {
            record.condition_id: record
            for record in self._store.list_condition_execution_records(plan.id)
            if record.status == ConditionExecutionStatus.COMPLETED
        }

        for condition in plan.ordered_conditions:
            if condition.id in existing_records:
                continue
            result = self._research_tool.run(
                spec=_ephemeral_spec(plan.id, condition),
                attempt_id=new_id(),
            )
            record = ConditionExecutionRecord(
                id=new_id(),
                plan_id=plan.id,
                condition_id=condition.id,
                ordinal=condition.ordinal,
                role=condition.role,
                selected_capability_id=condition.selected_capability_id,
                exact_parameters=condition.exact_parameters,
                status=ConditionExecutionStatus.COMPLETED,
                experiment_result_id=result.id,
                tool_name=result.tool_name,
                metrics=result.metrics,
                summary=result.summary,
                passed=result.passed,
            )
            self._store.save_condition_execution_record(record)
            existing_records[condition.id] = record

        completed = tuple(existing_records.get(condition.id) for condition in plan.ordered_conditions)
        if any(record is None for record in completed):
            raise RuntimeError("Contrast result cannot be produced before all conditions complete")

        baseline_record = existing_records[plan.ordered_conditions[0].id]
        comparator_record = existing_records[plan.ordered_conditions[1].id]
        assert baseline_record is not None and comparator_record is not None

        contrasts = tuple(
            OutcomeContrast(
                outcome=outcome,
                baseline_value=float(baseline_record.metrics[outcome.value]),
                comparator_value=float(comparator_record.metrics[outcome.value]),
                delta=round(
                    float(comparator_record.metrics[outcome.value]) - float(baseline_record.metrics[outcome.value]),
                    10,
                ),
                baseline_condition_id=baseline_record.condition_id,
                comparator_condition_id=comparator_record.condition_id,
            )
            for outcome in plan.dependent_outcomes
        )
        contrast_result = ParameterSensitivityContrastResult(
            id=new_id(),
            plan_id=plan.id,
            independent_variable=plan.independent_variable,
            baseline_condition_id=baseline_record.condition_id,
            comparator_condition_id=comparator_record.condition_id,
            baseline_parameter_value=float(
                plan.ordered_conditions[0].exact_parameters[plan.independent_variable.value]
            ),
            comparator_parameter_value=float(
                plan.ordered_conditions[1].exact_parameters[plan.independent_variable.value]
            ),
            outcomes=contrasts,
        )
        self._store.save_parameter_sensitivity_contrast_result(contrast_result)
        self._store.update_initial_experiment_plan_proposal_status(
            proposal.id,
            InitialExperimentPlanProposalStatus.COMPLETED,
            completed_at=contrast_result.created_at,
            contrast_result_id=contrast_result.id,
        )
        return contrast_result


def _candidate_supports_stub_materialization(requirements: tuple[AnyRequirement, ...]) -> bool:
    has_synthetic_data = False
    has_execution_tool = False
    for requirement in requirements:
        if isinstance(requirement, DataRequirement):
            if requirement.data_kind == DataKind.SYNTHETIC_PARAMETRIC:
                has_synthetic_data = True
        elif isinstance(requirement, ToolRequirement):
            if requirement.tool_kind == ToolKind.BACKTEST_EXECUTION:
                has_execution_tool = True
    return has_synthetic_data and has_execution_tool


def _ephemeral_spec(plan_id: str, condition: ExperimentCondition):
    from ..models.research import ResearchSpec

    return ResearchSpec(
        id=condition.id,
        research_run_id=plan_id,
        version=condition.ordinal,
        hypothesis_id=plan_id,
        parameters=dict(condition.exact_parameters),
        selected_capability_id=condition.selected_capability_id,
        materializer_version=MATERIALIZER_VERSION,
    )
