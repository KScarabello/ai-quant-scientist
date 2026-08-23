"""Domain models for the bounded Research Designer V1."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from ..capabilities.gate import ResearchCandidate
from .design import (
    AnalysisIntent,
    ComparisonIntent,
    DesignOutcome,
    DesignVariable,
    OutcomePrediction,
    ResearchDesignKind,
)
from .research import new_id


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


RESEARCH_DESIGN_INTENT_CONTRACT_VERSION = "research_design_intent_v1"
_SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class ResearchDesignerContext:
    """Bounded scientific context for one designer invocation."""

    candidate: ResearchCandidate
    candidate_feasibility_decision_id: str
    design_ontology_version: str
    design_ontology_fingerprint: str
    design_ontology_payload_json: str
    intent_contract_version: str = RESEARCH_DESIGN_INTENT_CONTRACT_VERSION

    def __post_init__(self) -> None:
        if not self.candidate_feasibility_decision_id or not self.candidate_feasibility_decision_id.strip():
            raise ValueError("ResearchDesignerContext requires candidate_feasibility_decision_id")
        if not self.design_ontology_version or not self.design_ontology_version.strip():
            raise ValueError("ResearchDesignerContext requires design_ontology_version")
        if not _SHA256_HEX_RE.match(self.design_ontology_fingerprint):
            raise ValueError("ResearchDesignerContext requires a SHA-256 ontology fingerprint")
        if not self.design_ontology_payload_json or not self.design_ontology_payload_json.strip():
            raise ValueError("ResearchDesignerContext requires design_ontology_payload_json")
        if not self.intent_contract_version or not self.intent_contract_version.strip():
            raise ValueError("ResearchDesignerContext requires intent_contract_version")
        payload = self.design_ontology_payload
        if payload.get("version") != self.design_ontology_version:
            raise ValueError("ResearchDesignerContext ontology payload version must match design_ontology_version")
        if payload.get("fingerprint") != self.design_ontology_fingerprint:
            raise ValueError(
                "ResearchDesignerContext ontology payload fingerprint must match design_ontology_fingerprint"
            )
        if payload.get("intent_contract_version") != self.intent_contract_version:
            raise ValueError(
                "ResearchDesignerContext ontology payload intent_contract_version must match context"
            )

    @property
    def candidate_id(self) -> str:
        return self.candidate.id

    @property
    def design_ontology_payload(self) -> dict[str, Any]:
        payload = json.loads(self.design_ontology_payload_json)
        if not isinstance(payload, dict):
            raise ValueError("ResearchDesignerContext ontology payload must decode to an object")
        from ..services.research_design_ontology import compute_research_design_ontology_fingerprint

        embedded_fingerprint = payload.get("fingerprint")
        if not isinstance(embedded_fingerprint, str) or not _SHA256_HEX_RE.match(embedded_fingerprint):
            raise ValueError("ResearchDesignerContext ontology payload fingerprint must be SHA-256 hex")
        recomputed_fingerprint = compute_research_design_ontology_fingerprint(payload)
        if embedded_fingerprint != recomputed_fingerprint:
            raise ValueError(
                "ResearchDesignerContext ontology payload fingerprint must match the semantic ontology payload"
            )
        if self.design_ontology_fingerprint != recomputed_fingerprint:
            raise ValueError(
                "ResearchDesignerContext design_ontology_fingerprint must match the semantic ontology payload"
            )
        return payload


class ResearchDesignerDecisionType(str, Enum):
    DESIGN = "DESIGN"
    NO_VALID_DESIGN = "NO_VALID_DESIGN"


@dataclass(frozen=True, slots=True)
class ResearchDesignerDecision:
    """AI-authored bounded design proposal, separate from authoritative intent."""

    id: str
    candidate_id: str
    decision_type: ResearchDesignerDecisionType
    design_kind: ResearchDesignKind | None = None
    independent_variables: tuple[DesignVariable, ...] | None = None
    dependent_outcomes: tuple[DesignOutcome, ...] | None = None
    controls: tuple[DesignVariable, ...] | None = None
    comparison_intent: ComparisonIntent | None = None
    analysis_intent: AnalysisIntent | None = None
    predictions: tuple[OutcomePrediction, ...] | None = None
    falsification_condition: str | None = None
    rationale: str | None = None
    no_valid_design_reason: str | None = None
    provider: str | None = None
    model: str | None = None
    prompt_version: str | None = None
    ontology_version: str | None = None
    ontology_fingerprint: str | None = None
    raw_response: str | None = None
    created_at: datetime = field(default_factory=utcnow)

    def __post_init__(self) -> None:
        if not self.candidate_id or not self.candidate_id.strip():
            raise ValueError("ResearchDesignerDecision requires candidate_id")
        if not isinstance(self.decision_type, ResearchDesignerDecisionType):
            raise ValueError(f"Invalid decision_type: {self.decision_type!r}")
        if self.independent_variables is not None:
            values = tuple(self.independent_variables)
            if all(hasattr(item, "value") for item in values):
                values = tuple(sorted(values, key=lambda item: item.value))
            object.__setattr__(self, "independent_variables", values)
        if self.dependent_outcomes is not None:
            values = tuple(self.dependent_outcomes)
            if all(hasattr(item, "value") for item in values):
                values = tuple(sorted(values, key=lambda item: item.value))
            object.__setattr__(self, "dependent_outcomes", values)
        if self.controls is not None:
            values = tuple(self.controls)
            if all(hasattr(item, "value") for item in values):
                values = tuple(sorted(values, key=lambda item: item.value))
            object.__setattr__(self, "controls", values)
        if self.predictions is not None:
            values = tuple(self.predictions)
            if all(hasattr(item, "outcome") for item in values):
                values = tuple(sorted(values, key=lambda item: item.outcome.value))
            object.__setattr__(self, "predictions", values)
        if self.ontology_fingerprint is not None and not _SHA256_HEX_RE.match(self.ontology_fingerprint):
            raise ValueError("ResearchDesignerDecision ontology_fingerprint must be SHA-256 hex")


@dataclass(frozen=True, slots=True)
class ResearchDesignerInvocation:
    """Append-only audit record for one Research Designer attempt."""

    id: str
    candidate_id: str
    candidate_snapshot_json: str
    candidate_feasibility_decision_id: str
    prompt_version: str
    ontology_version: str
    ontology_fingerprint: str
    intent_contract_version: str
    provider: str | None
    model: str | None
    raw_response: str | None
    parsed_decision_json: str | None
    validation_status: str | None
    validation_errors_json: str | None
    resulting_design_intent_id: str | None
    created_at: datetime = field(default_factory=utcnow)


def new_research_designer_decision_id() -> str:
    return new_id()
