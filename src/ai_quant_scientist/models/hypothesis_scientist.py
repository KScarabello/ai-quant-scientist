"""Domain models for the Bounded Hypothesis Scientist (V0.12A)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from .research import new_id


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ─── ResearchBrief ────────────────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class ResearchBrief:
    """Human or orchestrator-supplied scope for one scientist invocation.

    Constrains what the scientist is asked to investigate.
    Does NOT assert capability availability.
    """
    id: str
    research_question: str
    asset_class_focus: str | None = None           # e.g. "EQUITY", "FUTURES", "SYNTHETIC"
    instrument_focus: tuple[str, ...] | None = None
    methodological_constraints: tuple[str, ...] | None = None
    exclusions: tuple[str, ...] | None = None
    prior_candidate_fingerprints: tuple[str, ...] | None = None
    source: str = "manual"
    created_at: datetime = field(default_factory=utcnow)

    def __post_init__(self) -> None:
        if not self.research_question or not self.research_question.strip():
            raise ValueError("ResearchBrief requires non-empty research_question")

    @classmethod
    def create(
        cls,
        research_question: str,
        asset_class_focus: str | None = None,
        instrument_focus: list[str] | None = None,
        methodological_constraints: list[str] | None = None,
        exclusions: list[str] | None = None,
        prior_candidate_fingerprints: list[str] | None = None,
        source: str = "manual",
    ) -> "ResearchBrief":
        return cls(
            id=new_id(),
            research_question=research_question,
            asset_class_focus=asset_class_focus,
            instrument_focus=tuple(instrument_focus) if instrument_focus else None,
            methodological_constraints=tuple(methodological_constraints) if methodological_constraints else None,
            exclusions=tuple(exclusions) if exclusions else None,
            prior_candidate_fingerprints=tuple(prior_candidate_fingerprints) if prior_candidate_fingerprints else None,
            source=source,
        )


# ─── Decision vocabulary ─────────────────────────────────────────────────────

class HypothesisScientistDecisionType(str, Enum):
    PROPOSE_HYPOTHESIS = "PROPOSE_HYPOTHESIS"
    NO_HYPOTHESIS = "NO_HYPOTHESIS"     # valid scientific decision; brief too underspecified


# ─── HypothesisScientistDecision ─────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class HypothesisScientistDecision:
    """AI-generated scientific proposal — separate from authoritative ResearchCandidate.

    Governance fields (id, source, created_at) are NOT part of this object;
    they are assigned deterministically during candidate materialization.
    """
    id: str
    decision_type: HypothesisScientistDecisionType
    research_brief_id: str

    # Present when PROPOSE_HYPOTHESIS
    hypothesis_statement: str | None = None
    hypothesis_rationale: str | None = None
    # Serialized as JSON for storage; runtime type is tuple[AnyRequirement, ...] | None
    requirements_snapshot: str | None = None   # JSON; use deserialized form for logic

    # Present when NO_HYPOTHESIS
    no_hypothesis_reason: str | None = None

    # Provenance
    provider: str | None = None
    model: str | None = None
    prompt_version: str | None = None
    raw_response: str | None = None
    created_at: datetime = field(default_factory=utcnow)


# ─── Invocation persistence model ────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class HypothesisScientistInvocation:
    """Persisted audit record of one scientist invocation."""
    id: str
    research_brief_id: str
    research_brief_snapshot: str   # JSON
    prompt_version: str
    provider: str | None
    model: str | None
    raw_response: str | None
    parsed_decision_json: str | None
    validation_status: str | None   # "VALID", "INVALID", None (pending)
    validation_errors_json: str | None
    resulting_candidate_id: str | None
    created_at: datetime = field(default_factory=utcnow)
