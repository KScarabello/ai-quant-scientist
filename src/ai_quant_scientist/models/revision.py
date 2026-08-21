"""Domain models for Revision Intent and deterministic Revision Planner results."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from .critic import _VALID_CONFIDENCE, new_id


def utcnow() -> datetime:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc)


class RevisionDirection(str, Enum):
    INCREASE = "INCREASE"
    DECREASE = "DECREASE"
    PERTURB = "PERTURB"    # direction unknown; sensitivity experiment


class ExperimentType(str, Enum):
    MECHANISTIC_DIAGNOSTIC = "MECHANISTIC_DIAGNOSTIC"
    PARAMETER_SENSITIVITY = "PARAMETER_SENSITIVITY"


@dataclass(frozen=True, slots=True)
class RevisionIntent:
    """Scientific intent produced by the AI critic.

    The AI expresses WHAT experiment is justified and in which direction.
    The exact numeric value is resolved deterministically by RevisionPlanner.
    """
    id: str
    research_run_id: str | None
    parent_spec_id: str
    parameter: str
    direction: RevisionDirection
    experiment_type: ExperimentType
    rationale: str
    prediction: str
    confidence: str
    provider: str | None = None
    model: str | None = None
    prompt_version: str | None = None
    created_at: datetime = field(default_factory=utcnow)


@dataclass(frozen=True, slots=True)
class PlannerResult:
    """Output of RevisionPlanner.plan() — fully deterministic for fixed inputs."""
    intent_id: str
    planned_change: dict[str, Any] | None    # {param: exact_value}, None on rejection
    rejection_reason: str | None             # set when no legal candidate exists
    planner_version: str
    candidates_considered: list[Any]         # ordered candidates examined
    tested_values_skipped: list[Any]         # candidates excluded by lineage
    selected_value: Any | None
    created_at: datetime = field(default_factory=utcnow)


def validate_revision_intent(intent: RevisionIntent) -> None:
    """Raise ValueError if the intent violates its domain contract."""
    if not intent.parent_spec_id:
        raise ValueError("RevisionIntent requires parent_spec_id")
    if not intent.parameter or not intent.parameter.strip():
        raise ValueError("RevisionIntent requires non-empty parameter")
    if not isinstance(intent.direction, RevisionDirection):
        raise ValueError(f"Invalid direction: {intent.direction!r}")
    if not isinstance(intent.experiment_type, ExperimentType):
        raise ValueError(f"Invalid experiment_type: {intent.experiment_type!r}")
    if not intent.rationale or not intent.rationale.strip():
        raise ValueError("RevisionIntent requires non-empty rationale")
    if not intent.prediction or not intent.prediction.strip():
        raise ValueError("RevisionIntent requires non-empty prediction")
    if intent.confidence not in _VALID_CONFIDENCE:
        raise ValueError(
            f"RevisionIntent confidence must be one of {sorted(_VALID_CONFIDENCE)}, "
            f"got {intent.confidence!r}"
        )
