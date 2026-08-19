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
 