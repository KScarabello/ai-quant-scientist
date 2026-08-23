"""Deterministic AI-safe ontologies for the bounded Research Designer."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from ..models.design import (
    AnalysisIntent,
    ComparisonIntent,
    DesignOutcome,
    DesignVariable,
    ExpectedDirection,
    ResearchDesignKind,
)
from ..models.research import freeze_json_value, thaw_json_value
from ..models.research_designer import RESEARCH_DESIGN_INTENT_CONTRACT_VERSION


RESEARCH_DESIGN_ONTOLOGY_V1_VERSION = "research_design_ontology_v1"
RESEARCH_DESIGN_ONTOLOGY_V2_VERSION = "research_design_ontology_v2"
RESEARCH_DESIGN_ONTOLOGY_VERSION = RESEARCH_DESIGN_ONTOLOGY_V1_VERSION
RESEARCH_PREDICTION_PLAN_CONTRACT_VERSION = "research_prediction_plan_v1"

PARAMETER_SENSITIVITY_SEMANTICS = (
    "PARAMETER_SENSITIVITY expresses a precommitted scientific comparison where deterministic "
    "software later chooses exact condition values and execution sequencing."
)

EXACT_VALUE_BOUNDARY = (
    "Exact parameter values, baseline/comparator settings, condition count/order, capability IDs, "
    "and exact implementation compatibility are chosen later by deterministic software."
)

FALSIFICATION_BOUNDARY = (
    "falsification_condition is scientific prose only and is not an authoritative execution rule."
)

CONTROL_BOUNDARY = (
    "Controls must remain separate from the independent variable and describe what should stay fixed "
    "when deterministic software materializes the experiment."
)

PREDICTION_SEMANTICS_V2 = (
    "Directional predictions declare how each selected dependent outcome is expected to move when "
    "the independent variable moves from the deterministic baseline condition to the deterministic "
    "comparator condition. The AI does not choose exact condition values or verdicts."
)


def _payload_without_fingerprint(version: str) -> dict[str, Any]:
    if version == "v1":
        return {
            "version": RESEARCH_DESIGN_ONTOLOGY_V1_VERSION,
            "intent_contract_version": RESEARCH_DESIGN_INTENT_CONTRACT_VERSION,
            "supported_design_kinds": [ResearchDesignKind.PARAMETER_SENSITIVITY.value],
            "design_variables": [DesignVariable.SIGNAL_THRESHOLD.value, DesignVariable.LOOKBACK.value],
            "eligible_independent_variables_by_design_kind": {
                ResearchDesignKind.PARAMETER_SENSITIVITY.value: [DesignVariable.SIGNAL_THRESHOLD.value],
            },
            "required_controls_by_design_kind": {
                ResearchDesignKind.PARAMETER_SENSITIVITY.value: [DesignVariable.LOOKBACK.value],
            },
            "supported_dependent_outcomes": [
                DesignOutcome.TRADE_COUNT.value,
                DesignOutcome.NET_PNL.value,
                DesignOutcome.SHARPE.value,
            ],
            "comparison_intents": [ComparisonIntent.CONTRAST_PARAMETER_LEVELS.value],
            "analysis_intents": [AnalysisIntent.SENSITIVITY_ANALYSIS.value],
            "variable_semantics": {
                DesignVariable.SIGNAL_THRESHOLD.value: "Primary signal gating threshold for sensitivity comparison.",
                DesignVariable.LOOKBACK.value: "Historical lookback control retained as a fixed context variable in V1.",
            },
            "outcome_semantics": {
                DesignOutcome.TRADE_COUNT.value: "Number of executed trades under the deterministic stub run.",
                DesignOutcome.NET_PNL.value: "Net profit and loss measured by deterministic execution.",
                DesignOutcome.SHARPE.value: "Risk-adjusted outcome measured by deterministic execution.",
            },
            "parameter_sensitivity_semantics": PARAMETER_SENSITIVITY_SEMANTICS,
            "exact_value_boundary": EXACT_VALUE_BOUNDARY,
            "falsification_boundary": FALSIFICATION_BOUNDARY,
            "control_boundary": CONTROL_BOUNDARY,
            "constraints": [
                "Exactly one independent variable is allowed.",
                "Controls must be separate from independent variables.",
                "Dependent outcomes must use only supported ontology values.",
                "Return NO_VALID_DESIGN when the candidate cannot be expressed within this bounded V1 contract.",
            ],
        }
    if version == "v2":
        return {
            "version": RESEARCH_DESIGN_ONTOLOGY_V2_VERSION,
            "intent_contract_version": RESEARCH_DESIGN_INTENT_CONTRACT_VERSION,
            "prediction_contract_version": RESEARCH_PREDICTION_PLAN_CONTRACT_VERSION,
            "supported_design_kinds": [ResearchDesignKind.PARAMETER_SENSITIVITY.value],
            "design_variables": [DesignVariable.SIGNAL_THRESHOLD.value, DesignVariable.LOOKBACK.value],
            "eligible_independent_variables_by_design_kind": {
                ResearchDesignKind.PARAMETER_SENSITIVITY.value: [DesignVariable.SIGNAL_THRESHOLD.value],
            },
            "required_controls_by_design_kind": {
                ResearchDesignKind.PARAMETER_SENSITIVITY.value: [DesignVariable.LOOKBACK.value],
            },
            "supported_dependent_outcomes": [
                DesignOutcome.TRADE_COUNT.value,
                DesignOutcome.NET_PNL.value,
                DesignOutcome.SHARPE.value,
            ],
            "comparison_intents": [ComparisonIntent.CONTRAST_PARAMETER_LEVELS.value],
            "analysis_intents": [AnalysisIntent.SENSITIVITY_ANALYSIS.value],
            "supported_expected_directions": [
                ExpectedDirection.DECREASE.value,
                ExpectedDirection.INCREASE.value,
                ExpectedDirection.NO_CHANGE.value,
            ],
            "variable_semantics": {
                DesignVariable.SIGNAL_THRESHOLD.value: "Primary signal gating threshold for sensitivity comparison.",
                DesignVariable.LOOKBACK.value: "Historical lookback control retained as a fixed context variable.",
            },
            "outcome_semantics": {
                DesignOutcome.TRADE_COUNT.value: "Number of executed trades under deterministic execution.",
                DesignOutcome.NET_PNL.value: "Net profit and loss measured by deterministic execution.",
                DesignOutcome.SHARPE.value: "Risk-adjusted outcome measured by deterministic execution.",
            },
            "parameter_sensitivity_semantics": PARAMETER_SENSITIVITY_SEMANTICS,
            "prediction_semantics": PREDICTION_SEMANTICS_V2,
            "exact_value_boundary": EXACT_VALUE_BOUNDARY,
            "falsification_boundary": FALSIFICATION_BOUNDARY,
            "control_boundary": CONTROL_BOUNDARY,
            "constraints": [
                "Exactly one independent variable is allowed.",
                "Controls must be separate from independent variables.",
                "Dependent outcomes must use only supported ontology values.",
                "Exactly one directional prediction is required for every selected dependent outcome.",
                "Predictions must use only supported expected-direction enum values.",
                "Predictions must not contain exact numeric targets or verdict language.",
                "Return NO_VALID_DESIGN when the candidate cannot be expressed within this bounded V2 contract.",
            ],
        }
    raise KeyError(f"Unknown research design ontology version {version!r}")


def _compute_fingerprint(payload_without_fingerprint: dict[str, Any]) -> str:
    canon = json.dumps(
        payload_without_fingerprint,
        sort_keys=True,
        ensure_ascii=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def compute_research_design_ontology_fingerprint(payload: dict[str, Any]) -> str:
    """Compute the canonical ontology fingerprint for a payload object."""

    if not isinstance(payload, dict):
        raise ValueError("Research design ontology payload must be an object")
    semantic_payload = {key: value for key, value in payload.items() if key != "fingerprint"}
    return _compute_fingerprint(semantic_payload)


@dataclass(frozen=True, slots=True)
class ResearchDesignOntologySnapshot:
    version: str
    fingerprint: str
    intent_contract_version: str
    supported_design_kinds: tuple[str, ...]
    design_variables: tuple[str, ...]
    eligible_independent_variables_by_design_kind: dict[str, tuple[str, ...]]
    required_controls_by_design_kind: dict[str, tuple[str, ...]]
    supported_dependent_outcomes: tuple[str, ...]
    comparison_intents: tuple[str, ...]
    analysis_intents: tuple[str, ...]
    variable_semantics: dict[str, str]
    outcome_semantics: dict[str, str]
    parameter_sensitivity_semantics: str
    exact_value_boundary: str
    falsification_boundary: str
    control_boundary: str
    constraints: tuple[str, ...]
    prediction_contract_version: str | None = None
    supported_expected_directions: tuple[str, ...] | None = None
    prediction_semantics: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "eligible_independent_variables_by_design_kind",
            freeze_json_value(
                {
                    str(key): tuple(str(item) for item in values)
                    for key, values in self.eligible_independent_variables_by_design_kind.items()
                }
            ),
        )
        object.__setattr__(
            self,
            "required_controls_by_design_kind",
            freeze_json_value(
                {
                    str(key): tuple(str(item) for item in values)
                    for key, values in self.required_controls_by_design_kind.items()
                }
            ),
        )
        object.__setattr__(
            self,
            "variable_semantics",
            freeze_json_value({str(key): str(value) for key, value in self.variable_semantics.items()}),
        )
        object.__setattr__(
            self,
            "outcome_semantics",
            freeze_json_value({str(key): str(value) for key, value in self.outcome_semantics.items()}),
        )

    def to_payload(self) -> dict[str, Any]:
        payload = {
            "version": self.version,
            "fingerprint": self.fingerprint,
            "intent_contract_version": self.intent_contract_version,
            "supported_design_kinds": list(self.supported_design_kinds),
            "design_variables": list(self.design_variables),
            "eligible_independent_variables_by_design_kind": _thaw_mapping_of_string_sequences(
                self.eligible_independent_variables_by_design_kind
            ),
            "required_controls_by_design_kind": _thaw_mapping_of_string_sequences(
                self.required_controls_by_design_kind
            ),
            "supported_dependent_outcomes": list(self.supported_dependent_outcomes),
            "comparison_intents": list(self.comparison_intents),
            "analysis_intents": list(self.analysis_intents),
            "variable_semantics": _thaw_string_mapping(self.variable_semantics),
            "outcome_semantics": _thaw_string_mapping(self.outcome_semantics),
            "parameter_sensitivity_semantics": self.parameter_sensitivity_semantics,
            "exact_value_boundary": self.exact_value_boundary,
            "falsification_boundary": self.falsification_boundary,
            "control_boundary": self.control_boundary,
            "constraints": list(self.constraints),
        }
        if self.prediction_contract_version is not None:
            payload["prediction_contract_version"] = self.prediction_contract_version
        if self.supported_expected_directions is not None:
            payload["supported_expected_directions"] = list(self.supported_expected_directions)
        if self.prediction_semantics is not None:
            payload["prediction_semantics"] = self.prediction_semantics
        return payload


def _thaw_mapping_of_string_sequences(value: Any) -> dict[str, list[str]]:
    thawed = thaw_json_value(value)
    if not isinstance(thawed, dict):
        raise ValueError("Expected mapping-like value")
    return {str(key): [str(item) for item in values] for key, values in thawed.items()}


def _thaw_string_mapping(value: Any) -> dict[str, str]:
    thawed = thaw_json_value(value)
    if not isinstance(thawed, dict):
        raise ValueError("Expected mapping-like value")
    return {str(key): str(item) for key, item in thawed.items()}


def build_research_design_ontology_snapshot(version: str = "v1") -> ResearchDesignOntologySnapshot:
    payload = _payload_without_fingerprint(version)
    fingerprint = compute_research_design_ontology_fingerprint(payload)
    return ResearchDesignOntologySnapshot(
        version=payload["version"],
        fingerprint=fingerprint,
        intent_contract_version=payload["intent_contract_version"],
        supported_design_kinds=tuple(payload["supported_design_kinds"]),
        design_variables=tuple(payload["design_variables"]),
        eligible_independent_variables_by_design_kind={
            key: tuple(values)
            for key, values in payload["eligible_independent_variables_by_design_kind"].items()
        },
        required_controls_by_design_kind={
            key: tuple(values)
            for key, values in payload["required_controls_by_design_kind"].items()
        },
        supported_dependent_outcomes=tuple(payload["supported_dependent_outcomes"]),
        comparison_intents=tuple(payload["comparison_intents"]),
        analysis_intents=tuple(payload["analysis_intents"]),
        variable_semantics=dict(payload["variable_semantics"]),
        outcome_semantics=dict(payload["outcome_semantics"]),
        parameter_sensitivity_semantics=payload["parameter_sensitivity_semantics"],
        exact_value_boundary=payload["exact_value_boundary"],
        falsification_boundary=payload["falsification_boundary"],
        control_boundary=payload["control_boundary"],
        constraints=tuple(payload["constraints"]),
        prediction_contract_version=payload.get("prediction_contract_version"),
        supported_expected_directions=(
            tuple(payload["supported_expected_directions"])
            if payload.get("supported_expected_directions") is not None
            else None
        ),
        prediction_semantics=payload.get("prediction_semantics"),
    )


def build_current_research_design_ontology_snapshot() -> ResearchDesignOntologySnapshot:
    return build_research_design_ontology_snapshot(version="v2")
