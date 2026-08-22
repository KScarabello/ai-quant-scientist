"""Immutable research records persisted by SQLite."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from types import MappingProxyType
from typing import Any
from uuid import uuid4

from .enums import ResearchAction, ResearchStage, RunStatus, SpecRevisionProposalStatus


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return str(uuid4())


def freeze_json_value(value: Any) -> Any:
    """Recursively freeze JSON-like structures for authoritative records."""
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): freeze_json_value(item) for key, item in value.items()})
    if isinstance(value, list | tuple):
        return tuple(freeze_json_value(item) for item in value)
    return value


def thaw_json_value(value: Any) -> Any:
    """Recursively thaw immutable JSON-like structures for persistence."""
    if isinstance(value, Mapping):
        return {str(key): thaw_json_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [thaw_json_value(item) for item in value]
    return value


@dataclass(frozen=True, slots=True)
class Hypothesis:
    id: str
    research_run_id: str
    statement: str
    rationale: str
    created_at: datetime = field(default_factory=utcnow)


@dataclass(frozen=True, slots=True)
class ResearchSpec:
    id: str
    research_run_id: str
    version: int
    hypothesis_id: str
    parameters: dict[str, Any]
    parent_spec_id: str | None = None
    revision_proposal_id: str | None = None
    design_intent_id: str | None = None
    spec_materialization_proposal_id: str | None = None
    selected_capability_id: str | None = None
    materializer_version: str | None = None
    created_at: datetime = field(default_factory=utcnow)
    frozen_at: datetime | None = field(default_factory=utcnow)
    is_frozen: bool = True


@dataclass(frozen=True, slots=True)
class ResearchRun:
    id: str
    stage: ResearchStage
    status: RunStatus
    hypothesis_id: str
    active_spec_id: str
    iteration_count: int
    max_iterations: int
    next_required_action: ResearchAction = ResearchAction.NONE
    created_at: datetime = field(default_factory=utcnow)
    updated_at: datetime = field(default_factory=utcnow)


@dataclass(frozen=True, slots=True)
class ResearchAttempt:
    id: str
    research_run_id: str
    spec_id: str
    attempt_number: int
    stage: ResearchStage
    started_at: datetime
    completed_at: datetime
    status: str


@dataclass(frozen=True, slots=True)
class ExperimentResult:
    """Measured output from a research tool.

    The legacy ``passed`` field remains for compatibility, but deterministic
    scientific judgment is now handled by the Result Evaluator.
    """

    id: str
    attempt_id: str
    tool_name: str
    metrics: dict[str, Any]
    summary: str
    passed: bool
    created_at: datetime = field(default_factory=utcnow)


@dataclass(frozen=True, slots=True)
class AuditEvent:
    id: str
    research_run_id: str
    event_type: str
    action: str
    reason: str
    state_before: dict[str, Any]
    state_after: dict[str, Any]
    metadata: dict[str, Any]
    created_at: datetime = field(default_factory=utcnow)


@dataclass(frozen=True, slots=True)
class SpecRevisionProposal:
    id: str
    research_run_id: str
    parent_spec_id: str
    trigger_evaluation_id: str
    proposed_parameters: dict[str, Any]
    change_summary: str
    reason: str
    change_record: dict[str, Any]
    status: SpecRevisionProposalStatus
    created_at: datetime = field(default_factory=utcnow)
    decided_at: datetime | None = None
    accepted_spec_id: str | None = None


def record_to_state(record: Any) -> dict[str, Any]:
    """Return a JSON-ready snapshot of a domain record."""

    def convert(value: Any) -> Any:
        if isinstance(value, Enum):
            return value.value
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, Mapping):
            return {key: convert(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [convert(item) for item in value]
        return value

    return {field_name: convert(getattr(record, field_name)) for field_name in record.__dataclass_fields__}
