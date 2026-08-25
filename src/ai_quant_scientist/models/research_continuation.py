"""Governed adaptive hypothesis-continuation models for V0.17."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from .hypothesis_scientist import PriorCandidateSummary
from .post_verdict_critic import PostVerdictCriticDecisionType, PostVerdictRevisionKind
from .research import freeze_json_value, thaw_json_value, utcnow


RESEARCH_CONTINUATION_AUTHORIZATION_CONTRACT_VERSION = "research_continuation_authorization_v1"
RESEARCH_CONTINUATION_CONTEXT_VERSION = "research_continuation_context_v1"
ADAPTIVE_HYPOTHESIS_LINEAGE_CONTRACT_VERSION = "adaptive_hypothesis_lineage_v1"
_SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")


class ResearchContinuationOrigin(str, Enum):
    POST_VERDICT_ADAPTIVE = "POST_VERDICT_ADAPTIVE"


class ResearchContinuationAuthorizationStatus(str, Enum):
    PENDING = "PENDING"
    AUTHORIZED = "AUTHORIZED"
    CONSUMED = "CONSUMED"


class ResearchContinuationAttemptStatus(str, Enum):
    IN_PROGRESS = "IN_PROGRESS"
    GENERATED_ADAPTIVE_HYPOTHESIS = "GENERATED_ADAPTIVE_HYPOTHESIS"
    NO_HYPOTHESIS = "NO_HYPOTHESIS"
    INVALID_ATTEMPT = "INVALID_ATTEMPT"
    PROVIDER_ERROR = "PROVIDER_ERROR"


def compute_research_scope_fingerprint(scope_payload: Mapping[str, Any]) -> str:
    canon = json.dumps(dict(scope_payload), sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    import hashlib

    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class ResearchContinuationAuthorization:
    """Explicit governed authorization to spend one adaptive Scientist attempt."""

    id: str
    post_verdict_research_intent_id: str
    parent_scientific_verdict_id: str
    parent_hypothesis_claim_set_id: str
    parent_candidate_id: str
    research_scope_snapshot: Mapping[str, Any]
    research_scope_fingerprint: str
    allowed_revision_kind: PostVerdictRevisionKind
    generation_number: int
    origin: ResearchContinuationOrigin
    authorization_status: ResearchContinuationAuthorizationStatus
    contract_version: str = RESEARCH_CONTINUATION_AUTHORIZATION_CONTRACT_VERSION
    created_at: datetime = field(default_factory=utcnow)
    authorized_at: datetime | None = None

    def __post_init__(self) -> None:
        for field_name in (
            "post_verdict_research_intent_id",
            "parent_scientific_verdict_id",
            "parent_hypothesis_claim_set_id",
            "parent_candidate_id",
            "contract_version",
        ):
            if not getattr(self, field_name):
                raise ValueError(f"ResearchContinuationAuthorization requires {field_name}")
        if not isinstance(self.allowed_revision_kind, PostVerdictRevisionKind):
            raise ValueError(f"Invalid allowed_revision_kind: {self.allowed_revision_kind!r}")
        if not isinstance(self.origin, ResearchContinuationOrigin):
            raise ValueError(f"Invalid origin: {self.origin!r}")
        if not isinstance(self.authorization_status, ResearchContinuationAuthorizationStatus):
            raise ValueError(f"Invalid authorization_status: {self.authorization_status!r}")
        if self.generation_number < 2:
            raise ValueError("ResearchContinuationAuthorization generation_number must be >= 2")
        if not isinstance(self.research_scope_snapshot, Mapping) or not self.research_scope_snapshot:
            raise ValueError("ResearchContinuationAuthorization requires non-empty research_scope_snapshot")
        if not _SHA256_HEX_RE.match(self.research_scope_fingerprint):
            raise ValueError("ResearchContinuationAuthorization requires a SHA-256 research_scope_fingerprint")
        frozen_scope = freeze_json_value(dict(self.research_scope_snapshot))
        object.__setattr__(self, "research_scope_snapshot", frozen_scope)
        if compute_research_scope_fingerprint(thaw_json_value(frozen_scope)) != self.research_scope_fingerprint:
            raise ValueError(
                "ResearchContinuationAuthorization research_scope_fingerprint must match the semantic scope payload"
            )
        if self.authorization_status == ResearchContinuationAuthorizationStatus.PENDING and self.authorized_at is not None:
            raise ValueError("Pending continuation authorization must not carry authorized_at")
        if self.authorization_status in (
            ResearchContinuationAuthorizationStatus.AUTHORIZED,
            ResearchContinuationAuthorizationStatus.CONSUMED,
        ) and self.authorized_at is None:
            raise ValueError("Authorized or consumed continuation authorization requires authorized_at")

    def research_scope_payload(self) -> dict[str, Any]:
        value = thaw_json_value(self.research_scope_snapshot)
        if not isinstance(value, dict):
            raise ValueError("research_scope_snapshot must thaw to a mapping")
        return value


@dataclass(frozen=True, slots=True)
class ResearchContinuationContext:
    """Bounded adaptive Scientist context constructed from frozen post-verdict evidence."""

    id: str
    continuation_authorization_id: str
    post_verdict_research_intent_id: str
    parent_scientific_verdict_id: str
    generation_number: int
    origin: ResearchContinuationOrigin
    research_scope_snapshot: Mapping[str, Any]
    parent_hypothesis_claim_set_snapshot: Mapping[str, Any]
    parent_candidate_summary: PriorCandidateSummary
    parent_verdict_status: str
    critic_decision: PostVerdictCriticDecisionType
    critic_revision_kind: PostVerdictRevisionKind
    critic_diagnosis: str
    critic_next_step_rationale: str
    context_version: str = RESEARCH_CONTINUATION_CONTEXT_VERSION
    created_at: datetime = field(default_factory=utcnow)

    def __post_init__(self) -> None:
        for field_name in (
            "continuation_authorization_id",
            "post_verdict_research_intent_id",
            "parent_scientific_verdict_id",
            "parent_verdict_status",
            "critic_diagnosis",
            "critic_next_step_rationale",
            "context_version",
        ):
            if not getattr(self, field_name):
                raise ValueError(f"ResearchContinuationContext requires {field_name}")
        if not isinstance(self.origin, ResearchContinuationOrigin):
            raise ValueError(f"Invalid origin: {self.origin!r}")
        if not isinstance(self.critic_decision, PostVerdictCriticDecisionType):
            raise ValueError(f"Invalid critic_decision: {self.critic_decision!r}")
        if not isinstance(self.critic_revision_kind, PostVerdictRevisionKind):
            raise ValueError(f"Invalid critic_revision_kind: {self.critic_revision_kind!r}")
        if self.generation_number < 2:
            raise ValueError("ResearchContinuationContext generation_number must be >= 2")
        for field_name in ("research_scope_snapshot", "parent_hypothesis_claim_set_snapshot"):
            value = getattr(self, field_name)
            if not isinstance(value, Mapping) or not value:
                raise ValueError(f"ResearchContinuationContext requires non-empty {field_name}")
            object.__setattr__(self, field_name, freeze_json_value(dict(value)))

    def research_scope_payload(self) -> dict[str, Any]:
        value = thaw_json_value(self.research_scope_snapshot)
        if not isinstance(value, dict):
            raise ValueError("research_scope_snapshot must thaw to a mapping")
        return value


@dataclass(frozen=True, slots=True)
class ResearchContinuationInvocation:
    """Append-only provenance for one continuation Scientist attempt."""

    id: str
    continuation_authorization_id: str
    post_verdict_research_intent_id: str
    parent_scientific_verdict_id: str
    context_version: str
    prompt_version: str | None
    provider: str | None
    model: str | None
    context_snapshot_json: str
    raw_response: str | None
    parsed_decision_json: str | None
    attempt_status: ResearchContinuationAttemptStatus
    validation_errors_json: str | None
    resulting_candidate_id: str | None
    resulting_claim_set_id: str | None
    created_at: datetime = field(default_factory=utcnow)

    def __post_init__(self) -> None:
        if not self.continuation_authorization_id:
            raise ValueError("ResearchContinuationInvocation requires continuation_authorization_id")
        if not self.post_verdict_research_intent_id:
            raise ValueError("ResearchContinuationInvocation requires post_verdict_research_intent_id")
        if not self.parent_scientific_verdict_id:
            raise ValueError("ResearchContinuationInvocation requires parent_scientific_verdict_id")
        if not self.context_version:
            raise ValueError("ResearchContinuationInvocation requires context_version")
        if not isinstance(self.attempt_status, ResearchContinuationAttemptStatus):
            raise ValueError(f"Invalid attempt_status: {self.attempt_status!r}")


@dataclass(frozen=True, slots=True)
class AdaptiveHypothesisLineage:
    """Immutable lineage binding an adaptive child hypothesis to its parent evidence."""

    id: str
    candidate_id: str
    hypothesis_claim_set_id: str
    continuation_authorization_id: str
    post_verdict_research_intent_id: str
    parent_scientific_verdict_id: str
    parent_hypothesis_claim_set_id: str
    parent_candidate_id: str
    origin: ResearchContinuationOrigin
    generation_number: int
    research_scope_snapshot: Mapping[str, Any]
    research_scope_fingerprint: str
    contract_version: str = ADAPTIVE_HYPOTHESIS_LINEAGE_CONTRACT_VERSION
    created_at: datetime = field(default_factory=utcnow)

    def __post_init__(self) -> None:
        for field_name in (
            "candidate_id",
            "hypothesis_claim_set_id",
            "continuation_authorization_id",
            "post_verdict_research_intent_id",
            "parent_scientific_verdict_id",
            "parent_hypothesis_claim_set_id",
            "parent_candidate_id",
            "contract_version",
        ):
            if not getattr(self, field_name):
                raise ValueError(f"AdaptiveHypothesisLineage requires {field_name}")
        if not isinstance(self.origin, ResearchContinuationOrigin):
            raise ValueError(f"Invalid origin: {self.origin!r}")
        if self.generation_number < 2:
            raise ValueError("AdaptiveHypothesisLineage generation_number must be >= 2")
        if not isinstance(self.research_scope_snapshot, Mapping) or not self.research_scope_snapshot:
            raise ValueError("AdaptiveHypothesisLineage requires non-empty research_scope_snapshot")
        if not _SHA256_HEX_RE.match(self.research_scope_fingerprint):
            raise ValueError("AdaptiveHypothesisLineage requires a SHA-256 research_scope_fingerprint")
        frozen_scope = freeze_json_value(dict(self.research_scope_snapshot))
        object.__setattr__(self, "research_scope_snapshot", frozen_scope)
        if compute_research_scope_fingerprint(thaw_json_value(frozen_scope)) != self.research_scope_fingerprint:
            raise ValueError("AdaptiveHypothesisLineage fingerprint must match the semantic scope payload")

    def research_scope_payload(self) -> dict[str, Any]:
        value = thaw_json_value(self.research_scope_snapshot)
        if not isinstance(value, dict):
            raise ValueError("research_scope_snapshot must thaw to a mapping")
        return value
