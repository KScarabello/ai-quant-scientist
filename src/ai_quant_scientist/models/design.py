"""Structured research-design and deterministic contrast-plan governance models."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .research import freeze_json_value, new_id, thaw_json_value, utcnow


class ResearchDesignKind(str, Enum):
    """Small V1 vocabulary for supervised initial experiment design."""

    PARAMETER_SENSITIVITY = "PARAMETER_SENSITIVITY"


class DesignVariable(str, Enum):
    SIGNAL_THRESHOLD = "signal_threshold"
    LOOKBACK = "lookback"


class DesignOutcome(str, Enum):
    TRADE_COUNT = "trade_count"
    NET_PNL = "net_pnl"
    SHARPE = "sharpe"
    SCORE = "score"


class ComparisonIntent(str, Enum):
    CONTRAST_PARAMETER_LEVELS = "CONTRAST_PARAMETER_LEVELS"


class AnalysisIntent(str, Enum):
    SENSITIVITY_ANALYSIS = "SENSITIVITY_ANALYSIS"


class SpecFeasibilityStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"


class SpecFeasibilityReasonCode(str, Enum):
    CAPABILITY_NOT_FOUND = "CAPABILITY_NOT_FOUND"
    CAPABILITY_DISABLED = "CAPABILITY_DISABLED"
    SELECTED_CAPABILITY_INCOMPATIBLE = "SELECTED_CAPABILITY_INCOMPATIBLE"
    REQUIRED_PARAMETER_MISSING = "REQUIRED_PARAMETER_MISSING"
    UNSUPPORTED_PARAMETER = "UNSUPPORTED_PARAMETER"
    PARAMETER_TYPE_INVALID = "PARAMETER_TYPE_INVALID"
    SPEC_PAYLOAD_UNSUPPORTED = "SPEC_PAYLOAD_UNSUPPORTED"


class SpecFeasibilityPhase(str, Enum):
    MATERIALIZATION = "MATERIALIZATION"
    ACCEPTANCE = "ACCEPTANCE"


class SpecMaterializationProposalStatus(str, Enum):
    PROPOSED = "PROPOSED"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"


class ExperimentConditionRole(str, Enum):
    BASELINE = "BASELINE"
    COMPARATOR = "COMPARATOR"


class InitialExperimentCompletionRule(str, Enum):
    ALL_CONDITIONS_REQUIRED = "ALL_CONDITIONS_REQUIRED"


class InitialExperimentPlanProposalStatus(str, Enum):
    PROPOSED = "PROPOSED"
    ACCEPTED = "ACCEPTED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    REJECTED = "REJECTED"


class ConditionExecutionStatus(str, Enum):
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


@dataclass(frozen=True, slots=True)
class ResearchDesignIntent:
    """Immutable V1 scientific design intent for initial experiment planning."""

    id: str
    candidate_id: str
    design_kind: ResearchDesignKind
    independent_variables: tuple[DesignVariable, ...]
    dependent_outcomes: tuple[DesignOutcome, ...]
    controls: tuple[DesignVariable, ...]
    comparison_intent: ComparisonIntent
    analysis_intent: AnalysisIntent
    falsification_condition: str
    rationale: str
    source: str = "manual"
    provider: str | None = None
    model: str | None = None
    prompt_version: str | None = None
    ontology_version: str | None = None
    ontology_fingerprint: str | None = None
    created_at: Any = field(default_factory=utcnow)

    def __post_init__(self) -> None:
        if not self.candidate_id or not self.candidate_id.strip():
            raise ValueError("ResearchDesignIntent requires non-empty candidate_id")
        if not isinstance(self.design_kind, ResearchDesignKind):
            raise ValueError(f"Invalid design_kind: {self.design_kind!r}")
        if not self.independent_variables:
            raise ValueError("ResearchDesignIntent requires at least one independent variable")
        if not self.dependent_outcomes:
            raise ValueError("ResearchDesignIntent requires at least one dependent outcome")
        if not self.rationale or not self.rationale.strip():
            raise ValueError("ResearchDesignIntent requires non-empty rationale")
        if not self.falsification_condition or not self.falsification_condition.strip():
            raise ValueError("ResearchDesignIntent requires non-empty falsification_condition")
        if not self.source or not self.source.strip():
            raise ValueError("ResearchDesignIntent requires non-empty source")
        if self.ontology_version is not None and not self.ontology_version.strip():
            raise ValueError("ResearchDesignIntent ontology_version must be non-empty when provided")
        if self.ontology_fingerprint is not None and (
            len(self.ontology_fingerprint) != 64
            or any(ch not in "0123456789abcdef" for ch in self.ontology_fingerprint)
        ):
            raise ValueError("ResearchDesignIntent ontology_fingerprint must be 64-char lowercase SHA-256 hex")

        _validate_enum_tuple(
            self.independent_variables,
            DesignVariable,
            "independent_variables",
        )
        _validate_enum_tuple(
            self.dependent_outcomes,
            DesignOutcome,
            "dependent_outcomes",
        )
        _validate_enum_tuple(self.controls, DesignVariable, "controls")

        object.__setattr__(
            self,
            "independent_variables",
            tuple(sorted(self.independent_variables, key=lambda item: item.value)),
        )
        object.__setattr__(
            self,
            "dependent_outcomes",
            tuple(sorted(self.dependent_outcomes, key=lambda item: item.value)),
        )
        object.__setattr__(
            self,
            "controls",
            tuple(sorted(self.controls, key=lambda item: item.value)),
        )

        if not isinstance(self.comparison_intent, ComparisonIntent):
            raise ValueError(f"Invalid comparison_intent: {self.comparison_intent!r}")
        if not isinstance(self.analysis_intent, AnalysisIntent):
            raise ValueError(f"Invalid analysis_intent: {self.analysis_intent!r}")

        if self.design_kind == ResearchDesignKind.PARAMETER_SENSITIVITY and len(self.independent_variables) != 1:
            raise ValueError("PARAMETER_SENSITIVITY requires exactly one independent variable")

    @classmethod
    def create(
        cls,
        *,
        candidate_id: str,
        design_kind: ResearchDesignKind,
        independent_variables: tuple[DesignVariable, ...],
        dependent_outcomes: tuple[DesignOutcome, ...],
        controls: tuple[DesignVariable, ...],
        comparison_intent: ComparisonIntent,
        analysis_intent: AnalysisIntent,
        falsification_condition: str,
        rationale: str,
        source: str = "manual",
        provider: str | None = None,
        model: str | None = None,
        prompt_version: str | None = None,
        ontology_version: str | None = None,
        ontology_fingerprint: str | None = None,
    ) -> "ResearchDesignIntent":
        return cls(
            id=new_id(),
            candidate_id=candidate_id,
            design_kind=design_kind,
            independent_variables=independent_variables,
            dependent_outcomes=dependent_outcomes,
            controls=controls,
            comparison_intent=comparison_intent,
            analysis_intent=analysis_intent,
            falsification_condition=falsification_condition,
            rationale=rationale,
            source=source,
            provider=provider,
            model=model,
            prompt_version=prompt_version,
            ontology_version=ontology_version,
            ontology_fingerprint=ontology_fingerprint,
        )


@dataclass(frozen=True, slots=True)
class SpecFeasibilityDecision:
    """Exact condition-feasibility evidence for a deterministic implementation."""

    id: str
    candidate_id: str
    design_intent_id: str
    selected_capability_id: str
    status: SpecFeasibilityStatus
    reason_codes: tuple[SpecFeasibilityReasonCode, ...]
    proposed_parameters: Mapping[str, Any]
    validation_notes: str
    spec_feasibility_version: str
    registry_version: str
    registry_fingerprint: str
    materializer_version: str
    plan_id: str | None = None
    condition_id: str | None = None
    phase: SpecFeasibilityPhase = SpecFeasibilityPhase.MATERIALIZATION
    created_at: Any = field(default_factory=utcnow)

    def __post_init__(self) -> None:
        if not isinstance(self.status, SpecFeasibilityStatus):
            raise ValueError(f"Invalid status: {self.status!r}")
        _validate_enum_tuple(self.reason_codes, SpecFeasibilityReasonCode, "reason_codes")
        if not isinstance(self.phase, SpecFeasibilityPhase):
            raise ValueError(f"Invalid phase: {self.phase!r}")
        object.__setattr__(self, "proposed_parameters", _freeze_mapping(self.proposed_parameters))

    @property
    def is_pass(self) -> bool:
        return self.status == SpecFeasibilityStatus.PASS


@dataclass(frozen=True, slots=True)
class SpecMaterializationProposal:
    """Historical V0.13A single-spec proposal retained for v7 readability."""

    id: str
    candidate_id: str
    design_intent_id: str
    candidate_feasibility_decision_id: str
    selected_capability_id: str
    proposed_parameters: Mapping[str, Any]
    materializer_version: str
    materialization_policy_version: str
    materialization_policy_fingerprint: str
    materialization_trace: Mapping[str, Any]
    spec_feasibility_decision_id: str
    status: SpecMaterializationProposalStatus
    created_at: Any = field(default_factory=utcnow)
    decided_at: Any | None = None
    accepted_spec_id: str | None = None
    resulting_research_run_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "proposed_parameters", _freeze_mapping(self.proposed_parameters))
        object.__setattr__(self, "materialization_trace", freeze_json_value(self.materialization_trace))


@dataclass(frozen=True, slots=True)
class SpecMaterializationResult:
    """Historical V0.13A single-spec materialization result."""

    design_intent: ResearchDesignIntent
    spec_feasibility_decision: SpecFeasibilityDecision
    proposal: SpecMaterializationProposal


@dataclass(frozen=True, slots=True)
class ExperimentCondition:
    """One exact precommitted executable condition inside an initial experiment plan."""

    id: str
    ordinal: int
    role: ExperimentConditionRole
    exact_parameters: Mapping[str, Any]
    selected_capability_id: str
    expected_tool_kind: str
    created_at: Any = field(default_factory=utcnow)

    def __post_init__(self) -> None:
        if self.ordinal < 1:
            raise ValueError("ExperimentCondition ordinal must be >= 1")
        if not isinstance(self.role, ExperimentConditionRole):
            raise ValueError(f"Invalid role: {self.role!r}")
        if not self.selected_capability_id:
            raise ValueError("ExperimentCondition requires selected_capability_id")
        if not self.expected_tool_kind:
            raise ValueError("ExperimentCondition requires expected_tool_kind")
        object.__setattr__(self, "exact_parameters", _freeze_mapping(self.exact_parameters))


@dataclass(frozen=True, slots=True)
class InitialExperimentPlan:
    """Immutable precommitted scientific comparison plan."""

    id: str
    candidate_id: str
    design_intent_id: str
    candidate_feasibility_decision_id: str
    selected_capability_id: str
    design_kind: ResearchDesignKind
    independent_variable: DesignVariable
    control_variables: tuple[DesignVariable, ...]
    dependent_outcomes: tuple[DesignOutcome, ...]
    ordered_conditions: tuple[ExperimentCondition, ...]
    completion_rule: InitialExperimentCompletionRule
    materializer_version: str
    materialization_policy_version: str
    materialization_policy_fingerprint: str
    registry_version: str
    registry_fingerprint: str
    created_at: Any = field(default_factory=utcnow)

    def __post_init__(self) -> None:
        if not self.candidate_id:
            raise ValueError("InitialExperimentPlan requires candidate_id")
        if not self.design_intent_id:
            raise ValueError("InitialExperimentPlan requires design_intent_id")
        if not self.candidate_feasibility_decision_id:
            raise ValueError("InitialExperimentPlan requires candidate_feasibility_decision_id")
        if not self.selected_capability_id:
            raise ValueError("InitialExperimentPlan requires selected_capability_id")
        if not isinstance(self.design_kind, ResearchDesignKind):
            raise ValueError(f"Invalid design_kind: {self.design_kind!r}")
        if not isinstance(self.independent_variable, DesignVariable):
            raise ValueError(f"Invalid independent_variable: {self.independent_variable!r}")
        _validate_enum_tuple(self.control_variables, DesignVariable, "control_variables")
        _validate_enum_tuple(self.dependent_outcomes, DesignOutcome, "dependent_outcomes")
        if not isinstance(self.completion_rule, InitialExperimentCompletionRule):
            raise ValueError(f"Invalid completion_rule: {self.completion_rule!r}")
        if len(self.ordered_conditions) != 2:
            raise ValueError("V0.13A.1 requires exactly two ordered conditions")
        roles = tuple(condition.role for condition in self.ordered_conditions)
        ordinals = tuple(condition.ordinal for condition in self.ordered_conditions)
        if roles != (ExperimentConditionRole.BASELINE, ExperimentConditionRole.COMPARATOR):
            raise ValueError("Ordered conditions must be exactly BASELINE then COMPARATOR")
        if ordinals != (1, 2):
            raise ValueError("Ordered conditions must have ordinals 1 then 2")
        baseline, comparator = self.ordered_conditions
        if baseline.selected_capability_id != self.selected_capability_id:
            raise ValueError("Baseline condition capability must match plan capability")
        if comparator.selected_capability_id != self.selected_capability_id:
            raise ValueError("Comparator condition capability must match plan capability")
        baseline_keys = set(baseline.exact_parameters)
        comparator_keys = set(comparator.exact_parameters)
        if baseline_keys != comparator_keys:
            raise ValueError("All conditions must constrain the same exact parameter set")
        control_names = {item.value for item in self.control_variables}
        changed_keys = {
            key
            for key in baseline_keys
            if baseline.exact_parameters[key] != comparator.exact_parameters[key]
        }
        if changed_keys != {self.independent_variable.value}:
            raise ValueError("Only the independent variable may differ across V1 conditions")
        if control_names and any(
            baseline.exact_parameters[name] != comparator.exact_parameters[name]
            for name in control_names
        ):
            raise ValueError("Control variables must remain fixed across conditions")


@dataclass(frozen=True, slots=True)
class InitialExperimentPlanProposal:
    """Durable governed proposal for a precommitted initial experiment plan."""

    id: str
    plan_id: str
    candidate_id: str
    design_intent_id: str
    candidate_feasibility_decision_id: str
    materialization_feasibility_decision_ids: tuple[str, ...]
    materialization_trace: Mapping[str, Any]
    status: InitialExperimentPlanProposalStatus
    created_at: Any = field(default_factory=utcnow)
    decided_at: Any | None = None
    completed_at: Any | None = None
    contrast_result_id: str | None = None
    accepted_at: Any | None = None

    def __post_init__(self) -> None:
        if not self.plan_id:
            raise ValueError("InitialExperimentPlanProposal requires plan_id")
        if not self.materialization_feasibility_decision_ids:
            raise ValueError("InitialExperimentPlanProposal requires feasibility evidence")
        if not isinstance(self.status, InitialExperimentPlanProposalStatus):
            raise ValueError(f"Invalid status: {self.status!r}")
        object.__setattr__(
            self,
            "materialization_feasibility_decision_ids",
            tuple(self.materialization_feasibility_decision_ids),
        )
        object.__setattr__(self, "materialization_trace", freeze_json_value(self.materialization_trace))


@dataclass(frozen=True, slots=True)
class InitialExperimentPlanMaterializationResult:
    """Combined deterministic output of initial experiment-plan materialization."""

    design_intent: ResearchDesignIntent
    plan: InitialExperimentPlan
    condition_feasibility_decisions: tuple[SpecFeasibilityDecision, ...]
    proposal: InitialExperimentPlanProposal


@dataclass(frozen=True, slots=True)
class ConditionExecutionRecord:
    """Persistent execution record for one plan condition."""

    id: str
    plan_id: str
    condition_id: str
    ordinal: int
    role: ExperimentConditionRole
    selected_capability_id: str
    exact_parameters: Mapping[str, Any]
    status: ConditionExecutionStatus
    experiment_result_id: str
    tool_name: str
    metrics: Mapping[str, Any]
    summary: str
    passed: bool
    executed_at: Any = field(default_factory=utcnow)

    def __post_init__(self) -> None:
        if not isinstance(self.role, ExperimentConditionRole):
            raise ValueError(f"Invalid role: {self.role!r}")
        if not isinstance(self.status, ConditionExecutionStatus):
            raise ValueError(f"Invalid status: {self.status!r}")
        object.__setattr__(self, "exact_parameters", _freeze_mapping(self.exact_parameters))
        object.__setattr__(self, "metrics", _freeze_mapping(self.metrics))


@dataclass(frozen=True, slots=True)
class OutcomeContrast:
    """Deterministic comparison for one measured outcome across two conditions."""

    outcome: DesignOutcome
    baseline_value: float
    comparator_value: float
    delta: float
    baseline_condition_id: str
    comparator_condition_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.outcome, DesignOutcome):
            raise ValueError(f"Invalid outcome: {self.outcome!r}")


@dataclass(frozen=True, slots=True)
class ParameterSensitivityContrastResult:
    """Deterministic proof that a precommitted contrast actually occurred."""

    id: str
    plan_id: str
    independent_variable: DesignVariable
    baseline_condition_id: str
    comparator_condition_id: str
    baseline_parameter_value: float
    comparator_parameter_value: float
    outcomes: tuple[OutcomeContrast, ...]
    created_at: Any = field(default_factory=utcnow)

    def __post_init__(self) -> None:
        if not isinstance(self.independent_variable, DesignVariable):
            raise ValueError(f"Invalid independent_variable: {self.independent_variable!r}")
        _validate_enum_tuple(
            tuple(outcome.outcome for outcome in self.outcomes),
            DesignOutcome,
            "outcomes",
        )
        object.__setattr__(
            self,
            "outcomes",
            tuple(sorted(self.outcomes, key=lambda item: item.outcome.value)),
        )


def _validate_enum_tuple(values: tuple[Any, ...], enum_cls: type[Enum], field_name: str) -> None:
    for value in values:
        if not isinstance(value, enum_cls):
            raise ValueError(f"{field_name} entries must be {enum_cls.__name__}, got {value!r}")


def _freeze_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    return freeze_json_value(dict(value))


def thaw_mapping(value: Mapping[str, Any]) -> dict[str, Any]:
    thawed = thaw_json_value(value)
    if not isinstance(thawed, dict):
        raise ValueError("Expected mapping-like value")
    return thawed
