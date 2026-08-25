"""Domain models for the bounded Hypothesis Scientist."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from .design import DesignOutcome, DesignVariable, ExpectedDirection, OutcomePrediction
from .research import new_id


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


_SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")
MAX_PRIOR_CANDIDATE_SUMMARIES = 5
HYPOTHESIS_CLAIM_SET_CONTRACT_VERSION = "hypothesis_claim_set_v1"
RESEARCH_SCOPE_CONTRACT_VERSION = "research_scope_v1"


class HypothesisClaimAggregation(str, Enum):
    ALL_CLAIMS_REQUIRED = "ALL_CLAIMS_REQUIRED"


class ResearchScopeOutcomeAggregation(str, Enum):
    ALL_OUTCOMES_REQUIRED = "ALL_OUTCOMES_REQUIRED"


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


@dataclass(frozen=True, slots=True)
class ResearchScope:
    """Caller-owned canonical material scientific scope for one ResearchBrief."""

    independent_variable: DesignVariable
    requested_outcomes: tuple[DesignOutcome, ...]
    outcome_aggregation: ResearchScopeOutcomeAggregation
    contract_version: str = RESEARCH_SCOPE_CONTRACT_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.independent_variable, DesignVariable):
            raise ValueError(f"Invalid ResearchScope independent_variable: {self.independent_variable!r}")
        if self.independent_variable != DesignVariable.SIGNAL_THRESHOLD:
            raise ValueError("ResearchScope independent_variable is unsupported by the bounded V0.15.2 contract")
        if not self.requested_outcomes:
            raise ValueError("ResearchScope requires at least one requested outcome")
        if any(not isinstance(item, DesignOutcome) for item in self.requested_outcomes):
            raise ValueError("ResearchScope requested_outcomes must be DesignOutcome entries")
        if any(item == DesignOutcome.SCORE for item in self.requested_outcomes):
            raise ValueError("ResearchScope does not support SCORE as a material requested outcome")
        if len(set(self.requested_outcomes)) != len(self.requested_outcomes):
            raise ValueError("ResearchScope requested_outcomes must not repeat outcomes")
        if not isinstance(self.outcome_aggregation, ResearchScopeOutcomeAggregation):
            raise ValueError(f"Invalid ResearchScope outcome_aggregation: {self.outcome_aggregation!r}")
        if self.outcome_aggregation != ResearchScopeOutcomeAggregation.ALL_OUTCOMES_REQUIRED:
            raise ValueError("ResearchScope outcome_aggregation is unsupported by the bounded V0.15.2 contract")
        if self.contract_version != RESEARCH_SCOPE_CONTRACT_VERSION:
            raise ValueError("ResearchScope contract_version must match the bounded V0.15.2 contract")
        object.__setattr__(
            self,
            "requested_outcomes",
            tuple(sorted(self.requested_outcomes, key=lambda item: item.value)),
        )

    def to_payload(self) -> dict[str, object]:
        return {
            "contract_version": self.contract_version,
            "independent_variable": self.independent_variable.value,
            "requested_outcomes": [item.value for item in self.requested_outcomes],
            "outcome_aggregation": self.outcome_aggregation.value,
        }

    @classmethod
    def create(
        cls,
        *,
        independent_variable: DesignVariable | str,
        requested_outcomes: list[DesignOutcome | str],
        outcome_aggregation: ResearchScopeOutcomeAggregation | str,
        contract_version: str = RESEARCH_SCOPE_CONTRACT_VERSION,
    ) -> "ResearchScope":
        return cls(
            independent_variable=(
                independent_variable
                if isinstance(independent_variable, DesignVariable)
                else DesignVariable(independent_variable)
            ),
            requested_outcomes=tuple(
                item if isinstance(item, DesignOutcome) else DesignOutcome(item)
                for item in requested_outcomes
            ),
            outcome_aggregation=(
                outcome_aggregation
                if isinstance(outcome_aggregation, ResearchScopeOutcomeAggregation)
                else ResearchScopeOutcomeAggregation(outcome_aggregation)
            ),
            contract_version=contract_version,
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
    research_scope: ResearchScope | None = None
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
        research_scope: ResearchScope | dict[str, object] | None = None,
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
        scope = None
        if research_scope is not None:
            scope = (
                research_scope
                if isinstance(research_scope, ResearchScope)
                else ResearchScope.create(**research_scope)
            )
        return cls(
            id=new_id(),
            research_question=research_question,
            asset_class_focus=asset_class_focus,
            instrument_focus=tuple(instrument_focus) if instrument_focus else None,
            methodological_constraints=tuple(methodological_constraints) if methodological_constraints else None,
            exclusions=tuple(exclusions) if exclusions else None,
            research_scope=scope,
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
    independent_variable: DesignVariable | None = None
    independent_variable_direction: ExpectedDirection | None = None
    outcome_claims: tuple[OutcomePrediction, ...] | None = None
    claim_aggregation: HypothesisClaimAggregation | None = None

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

    def __post_init__(self) -> None:
        if self.outcome_claims is not None:
            values = tuple(self.outcome_claims)
            if all(hasattr(item, "outcome") for item in values):
                values = tuple(sorted(values, key=lambda item: item.outcome.value))
            object.__setattr__(self, "outcome_claims", values)


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
    resulting_claim_set_id: str | None = None
    created_at: datetime = field(default_factory=utcnow)


@dataclass(frozen=True, slots=True)
class HypothesisClaimSet:
    """Immutable canonical scientific intent for a candidate-side hypothesis."""

    id: str
    candidate_id: str
    hypothesis_scientist_invocation_id: str
    independent_variable: DesignVariable
    independent_variable_direction: ExpectedDirection
    claims: tuple[OutcomePrediction, ...]
    claim_aggregation: HypothesisClaimAggregation
    claim_contract_version: str
    ontology_version: str
    ontology_fingerprint: str
    created_at: datetime = field(default_factory=utcnow)

    def __post_init__(self) -> None:
        if not self.candidate_id:
            raise ValueError("HypothesisClaimSet requires candidate_id")
        if not self.hypothesis_scientist_invocation_id:
            raise ValueError("HypothesisClaimSet requires hypothesis_scientist_invocation_id")
        if not isinstance(self.independent_variable, DesignVariable):
            raise ValueError(f"Invalid independent_variable: {self.independent_variable!r}")
        if self.independent_variable_direction not in (
            ExpectedDirection.INCREASE,
            ExpectedDirection.DECREASE,
        ):
            raise ValueError(
                "HypothesisClaimSet independent_variable_direction must be INCREASE or DECREASE"
            )
        if not self.claims:
            raise ValueError("HypothesisClaimSet requires at least one claim")
        if any(not isinstance(item, OutcomePrediction) for item in self.claims):
            raise ValueError("HypothesisClaimSet claims must be OutcomePrediction entries")
        for item in self.claims:
            if item.expected_direction not in (ExpectedDirection.INCREASE, ExpectedDirection.DECREASE):
                raise ValueError("HypothesisClaimSet claim directions must be INCREASE or DECREASE")
            if item.outcome == DesignOutcome.SCORE:
                raise ValueError("HypothesisClaimSet does not support SCORE claims")
        outcomes = [item.outcome for item in self.claims]
        if len(set(outcomes)) != len(outcomes):
            raise ValueError("HypothesisClaimSet claims must not repeat outcomes")
        if not isinstance(self.claim_aggregation, HypothesisClaimAggregation):
            raise ValueError(f"Invalid claim_aggregation: {self.claim_aggregation!r}")
        if not self.claim_contract_version:
            raise ValueError("HypothesisClaimSet requires claim_contract_version")
        if not self.ontology_version:
            raise ValueError("HypothesisClaimSet requires ontology_version")
        if not _SHA256_HEX_RE.match(self.ontology_fingerprint):
            raise ValueError("HypothesisClaimSet requires a SHA-256 ontology_fingerprint")
        object.__setattr__(
            self,
            "claims",
            tuple(sorted(self.claims, key=lambda item: item.outcome.value)),
        )


def hypothesis_claim_signature_payload(
    *,
    independent_variable: DesignVariable,
    independent_variable_direction: ExpectedDirection,
    claims: tuple[OutcomePrediction, ...],
    claim_aggregation: HypothesisClaimAggregation,
) -> dict[str, object]:
    ordered_claims = tuple(sorted(claims, key=lambda item: item.outcome.value))
    return {
        "independent_variable": independent_variable.value,
        "independent_variable_direction": independent_variable_direction.value,
        "claims": [
            {
                "outcome": item.outcome.value,
                "expected_direction": item.expected_direction.value,
            }
            for item in ordered_claims
        ],
        "claim_aggregation": claim_aggregation.value,
    }


def compute_hypothesis_claim_signature(
    *,
    independent_variable: DesignVariable,
    independent_variable_direction: ExpectedDirection,
    claims: tuple[OutcomePrediction, ...],
    claim_aggregation: HypothesisClaimAggregation,
) -> str:
    canon = json.dumps(
        hypothesis_claim_signature_payload(
            independent_variable=independent_variable,
            independent_variable_direction=independent_variable_direction,
            claims=claims,
            claim_aggregation=claim_aggregation,
        ),
        sort_keys=True,
        ensure_ascii=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()
