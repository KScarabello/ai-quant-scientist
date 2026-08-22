"""Domain models for the Bounded Hypothesis Scientist (V0.12A)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from .research import new_id


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


_SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")
MAX_PRIOR_CANDIDATE_SUMMARIES = 5


@dataclass(frozen=True, slots=True)
class PriorCandidateSummary:
    """Bounded AI-readable context about prior candidate science.

    Fingerprint remains the authoritative identity.
    The statement and optional rationale summary are only novelty context for AI.
    """
    fingerprint: str
    hypothesis_statement: str
    hypothesis_rationale_summary: str | None = None

    def __post_init__(self) -> None:
        if not _SHA256_HEX_RE.match(self.fingerprint):
            raise ValueError("PriorCandidateSummary fingerprint must be a 64-char lowercase SHA-256 hex")
        if not self.hypothesis_statement or not self.hypothesis_statement.strip():
            raise ValueError("PriorCandidateSummary requires non-empty hypothesis_statement")
        if self.hypothesis_rationale_summary is not None and not self.hypothesis_rationale_summary.strip():
            raise ValueError(
                "PriorCandidateSummary hypothesis_rationale_summary must be non-empty when provided"
            )


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
    prior_candidate_summaries: tuple[PriorCandidateSummary, ...] | None = None
    source: str = "manual"
    created_at: datetime = field(default_factory=utcnow)

    def __post_init__(self) -> None:
        if not self.research_question or not self.research_question.strip():
            raise ValueError("ResearchBrief requires non-empty research_question")
        if self.prior_candidate_summaries is not None:
            if len(self.prior_candidate_summaries) > MAX_PRIOR_CANDIDATE_SUMMARIES:
                raise ValueError(
                    f"ResearchBrief allows at most {MAX_PRIOR_CANDIDATE_SUMMARIES} prior_candidate_summaries"
                )
            summary_fingerprints = tuple(s.fingerprint for s in self.prior_candidate_summaries)
            if self.prior_candidate_fingerprints is None:
                object.__setattr__(self, "prior_candidate_fingerprints", summary_fingerprints)
            elif self.prior_candidate_fingerprints != summary_fingerprints:
                raise ValueError(
                    "prior_candidate_fingerprints must match prior_candidate_summaries fingerprints exactly"
                )
        if self.prior_candidate_fingerprints is not None:
            for fingerprint in self.prior_candidate_fingerprints:
                if not _SHA256_HEX_RE.match(fingerprint):
                    raise ValueError(
                        "prior_candidate_fingerprints entries must be 64-char lowercase SHA-256 hex"
                    )

    @classmethod
    def create(
        cls,
        research_question: str,
        asset_class_focus: str | None = None,
        instrument_focus: list[str] | None = None,
        methodological_constraints: list[str] | None = None,
        exclusions: list[str] | None = None,
        prior_candidate_fingerprints: list[str] | None = None,
        prior_candidate_summaries: list[PriorCandidateSummary | dict[str, str | None]] | None = None,
        source: str = "manual",
    ) -> "ResearchBrief":
        summaries = None
        if prior_candidate_summaries:
            summaries = tuple(
                s if isinstance(s, PriorCandidateSummary) else PriorCandidateSummary(**s)
                for s in prior_candidate_summaries
            )
        return cls(
            id=new_id(),
            research_question=research_question,
            asset_class_focus=asset_class_focus,
            instrument_focus=tuple(instrument_focus) if instrument_focus else None,
            methodological_constraints=tuple(methodological_constraints) if methodological_constraints else None,
            exclusions=tuple(exclusions) if exclusions else None,
            prior_candidate_fingerprints=tuple(prior_candidate_fingerprints) if prior_candidate_fingerprints else None,
            prior_candidate_summaries=summaries,
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
    ontology_version: str | None = None
    ontology_fingerprint: str | None = None
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
