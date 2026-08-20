"""Models for AI Research Critic context and decisions."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any
from uuid import uuid4


def new_id() -> str:
    return str(uuid4())


def utcnow() -> datetime:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc)


class CriticDecisionType(str, Enum):
    PROPOSE_REVISION = "PROPOSE_REVISION"
    NO_USEFUL_REVISION = "NO_USEFUL_REVISION"


class CriticConfidence(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"


_VALID_CONFIDENCE: frozenset[str] = frozenset(c.value for c in CriticConfidence)


@dataclass(frozen=True, slots=True)
class CriticContext:
    id: str
    research_run_id: str
    hypothesis: dict[str, Any]
    current_spec: dict[str, Any]
    attempt: dict[str, Any]
    result: dict[str, Any]
    evaluation: dict[str, Any]
    prior_lineage: list[dict[str, Any]]
    allowed_revision_constraints: dict[str, Any]
    context_version: str = "v1"
    created_at: datetime = field(default_factory=utcnow)


@dataclass(frozen=True, slots=True)
class CriticDecision:
    id: str
    research_run_id: str
    decision_type: CriticDecisionType
    parent_spec_id: str | None
    changes: dict[str, Any] | None
    rationale: str | None
    prediction: str | None
    confidence: str | None
    provider: str | None = None
    model: str | None = None
    raw_response: str | None = None
    created_at: datetime = field(default_factory=utcnow)


def validate_critic_decision(decision: "CriticDecision") -> None:
    """Enforce cross-field contract. Raises ValueError on any violation.

    Rules (post-Benchmark-V1):
      PROPOSE_REVISION  — parent_spec_id, exactly-one change, rationale, prediction,
                          and confidence in {low, medium, high} are all required.
      NO_USEFUL_REVISION — changes must be absent; confidence is optional but if
                           present must be in {low, medium, high}.
    """
    dt = decision.decision_type
    if dt == CriticDecisionType.PROPOSE_REVISION:
        if not decision.parent_spec_id:
            raise ValueError("PROPOSE_REVISION requires parent_spec_id")
        if not decision.changes:
            raise ValueError("PROPOSE_REVISION requires exactly one change")
        if len(decision.changes) != 1:
            raise ValueError("PROPOSE_REVISION requires exactly one change")
        if not decision.rationale or not decision.rationale.strip():
            raise ValueError("PROPOSE_REVISION requires non-empty rationale")
        if not decision.prediction or not decision.prediction.strip():
            raise ValueError("PROPOSE_REVISION requires non-empty prediction")
        if decision.confidence not in _VALID_CONFIDENCE:
            raise ValueError(
                f"PROPOSE_REVISION confidence must be one of "
                f"{sorted(_VALID_CONFIDENCE)}, got {decision.confidence!r}"
            )
    elif dt == CriticDecisionType.NO_USEFUL_REVISION:
        if decision.changes is not None:
            raise ValueError("NO_USEFUL_REVISION must not include changes")
        if decision.confidence is not None and decision.confidence not in _VALID_CONFIDENCE:
            raise ValueError(
                f"NO_USEFUL_REVISION confidence must be one of "
                f"{sorted(_VALID_CONFIDENCE)} or null, got {decision.confidence!r}"
            )
    else:
        raise ValueError(f"Unknown decision type: {decision.decision_type!r}")


@dataclass(frozen=True, slots=True)
class CriticInvocation:
    id: str
    research_run_id: str
    evaluation_id: str | None
    parent_spec_id: str | None
    context_version: str
    prompt_version: str | None
    provider: str | None
    model: str | None
    context_snapshot: dict[str, Any] | None
    raw_response: str | None
    parsed_decision: dict[str, Any] | None
    validation_status: str | None
    validation_errors: dict[str, Any] | None
    resulting_proposal_id: str | None
    created_at: datetime = field(default_factory=utcnow)
    completed_at: datetime | None = None
 