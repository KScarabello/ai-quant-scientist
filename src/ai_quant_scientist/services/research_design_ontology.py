"""Deterministic AI-safe ontology for the bounded Research Designer V1."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from ..models.design import (
    AnalysisIntent,
    ComparisonIntent,
    DesignOutcome,
    DesignVariable,
    ResearchDesignKind,
)
from ..models.research_designer import RESEARCH_DESIGN_INTENT_CONTRACT_VERSION


RESEARCH_DESIGN_ONTOLOGY_VERSION = "research_design_ontology_v1"

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


def _payload_without_fingerprint() -> dict:
    return {
        "version": RESEARCH_DESIGN_ONTOLOGY_VERSION,
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


def _compute_fingerprint(payload_without_fingerprint: dict) -> str:
    canon = json.dumps(
        payload_without_fingerprint,
        sort_keys=True,
        ensure_ascii=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


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

    def to_payload(self) -> dict:
        return {
            "version": self.version,
            "fingerprint": self.fingerprint,
            "intent_contract_version": self.intent_contract_version,
            "supported_design_kinds": list(self.supported_design_kinds),
            "design_variables": list(self.design_variables),
            "eligible_independent_variables_by_design_kind": {
                key: list(values)
                for key, values in self.eligible_independent_variables_by_design_kind.items()
            },
            "required_controls_by_design_kind": {
                key: list(values)
                for key, values in self.required_controls_by_design_kind.items()
            },
            "supported_dependent_outcomes": list(self.supported_dependent_outcomes),
            "comparison_intents": list(self.comparison_intents),
            "analysis_intents": list(self.analysis_intents),
            "variable_semantics": dict(self.variable_semantics),
            "outcome_semantics": dict(self.outcome_semantics),
            "parameter_sensitivity_semantics": self.parameter_sensitivity_semantics,
            "exact_value_boundary": self.exact_value_boundary,
            "falsification_boundary": self.falsification_boundary,
            "control_boundary": self.control_boundary,
            "constraints": list(self.constraints),
        }


def build_research_design_ontology_snapshot() -> ResearchDesignOntologySnapshot:
    payload = _payload_without_fingerprint()
    fingerprint = _compute_fingerprint(payload)
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
    )
