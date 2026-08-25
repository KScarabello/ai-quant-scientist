"""Bounded post-verdict Critic models for V0.16."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from .research import freeze_json_value, thaw_json_value, new_id, utcnow


POST_VERDICT_CRITIC_CONTEXT_VERSION = "post_verdict_critic_context_v1"
POST_VERDICT_RESEARCH_INTENT_CONTRACT_VERSION = "post_verdict_research_intent_v1"


class PostVerdictCriticDecisionType(str, Enum):
    CONTINUE = "CONTINUE"
    STOP = "STOP"


class PostVerdictRevisionKind(str, Enum):
    SCOPE_PRESERVING_HYPOTHESIS_REVISION = "SCOPE_PRESERVING_HYPOTHESIS_REVISION"
    MECHANISM_REVISION = "MECHANISM_REVISION"
    REPLICATION = "REPLICATION"
    NONE = "NONE"


@dataclass(frozen=True, slots=True)
class PostVerdictCriticContext:
    """Exact immutable governed context for one post-verdict Critic invocation."""

    id: str
    scientific_verdict_id: str
    research_brief_id: str
    research_scope_snapshot: Mapping[str, Any]
    research_brief_snapshot: Mapping[str, Any]
    candidate_snapshot: Mapping[str, Any]
    candidate_feasibility_snapshot: Mapping[str, Any] | None
    hypothesis_claim_set_snapshot: Mapping[str, Any]
    research_design_intent_snapshot: Mapping[str, Any]
    research_prediction_plan_snapshot: Mapping[str, Any]
    initial_experiment_plan_snapshot: Mapping[str, Any]
    contrast_result_snapshot: Mapping[str, Any]
    scientific_verdict_snapshot: Mapping[str, Any]
    context_version: str = POST_VERDICT_CRITIC_CONTEXT_VERSION
    created_at: datetime = field(default_factory=utcnow)

    def __post_init__(self) -> None:
        if not self.scientific_verdict_id:
            raise ValueError("PostVerdictCriticContext requires scientific_verdict_id")
        if not self.research_brief_id:
            raise ValueError("PostVerdictCriticContext requires research_brief_id")
        if not self.context_version:
            raise ValueError("PostVerdictCriticContext requires context_version")
        for field_name in (
            "research_scope_snapshot",
            "research_brief_snapshot",
            "candidate_snapshot",
            "hypothesis_claim_set_snapshot",
            "research_design_intent_snapshot",
            "research_prediction_plan_snapshot",
            "initial_experiment_plan_snapshot",
            "contrast_result_snapshot",
            "scientific_verdict_snapshot",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, Mapping) or not value:
                raise ValueError(f"PostVerdictCriticContext requires non-empty {field_name}")
            object.__setattr__(self, field_name, freeze_json_value(dict(value)))
        if self.candidate_feasibility_snapshot is not None:
            if not isinstance(self.candidate_feasibility_snapshot, Mapping):
                raise ValueError(
                    "PostVerdictCriticContext candidate_feasibility_snapshot must be a mapping when provided"
                )
            object.__setattr__(
                self,
                "candidate_feasibility_snapshot",
                freeze_json_value(dict(self.candidate_feasibility_snapshot)),
            )


@dataclass(frozen=True, slots=True)
class PostVerdictCriticDecision:
    """AI-authored diagnosis and non-executable next-research intent."""

    id: str
    scientific_verdict_id: str
    decision: PostVerdictCriticDecisionType
    revision_kind: PostVerdictRevisionKind
    diagnosis: str
    next_step_rationale: str
    provider: str | None = None
    model: str | None = None
    prompt_version: str | None = None
    raw_response: str | None = None
    created_at: datetime = field(default_factory=utcnow)

    def __post_init__(self) -> None:
        if not self.scientific_verdict_id:
            raise ValueError("PostVerdictCriticDecision requires scientific_verdict_id")
        if not isinstance(self.decision, PostVerdictCriticDecisionType):
            raise ValueError(f"Invalid decision: {self.decision!r}")
        if not isinstance(self.revision_kind, PostVerdictRevisionKind):
            raise ValueError(f"Invalid revision_kind: {self.revision_kind!r}")
        if not self.diagnosis or not self.diagnosis.strip():
            raise ValueError("PostVerdictCriticDecision requires non-empty diagnosis")
        if not self.next_step_rationale or not self.next_step_rationale.strip():
            raise ValueError("PostVerdictCriticDecision requires non-empty next_step_rationale")


def validate_post_verdict_critic_decision(decision: PostVerdictCriticDecision) -> None:
    if decision.decision == PostVerdictCriticDecisionType.CONTINUE:
        if decision.revision_kind == PostVerdictRevisionKind.NONE:
            raise ValueError("CONTINUE requires a non-NONE revision_kind")
        return
    if decision.decision == PostVerdictCriticDecisionType.STOP:
        if decision.revision_kind != PostVerdictRevisionKind.NONE:
            raise ValueError("STOP requires revision_kind NONE")
        return
    raise ValueError(f"Unsupported decision: {decision.decision!r}")


@dataclass(frozen=True, slots=True)
class PostVerdictCriticInvocation:
    """Append-only provenance record for one post-verdict Critic attempt."""

    id: str
    scientific_verdict_id: str
    context_version: str
    prompt_version: str | None
    provider: str | None
    model: str | None
    context_snapshot_json: str
    raw_response: str | None
    parsed_decision_json: str | None
    validation_status: str | None
    validation_errors_json: str | None
    resulting_intent_id: str | None
    created_at: datetime = field(default_factory=utcnow)


@dataclass(frozen=True, slots=True)
class PostVerdictResearchIntent:
    """Immutable bounded diagnosis and next-research recommendation."""

    id: str
    scientific_verdict_id: str
    research_brief_id: str
    hypothesis_claim_set_id: str
    research_design_intent_id: str
    research_prediction_plan_id: str
    contrast_result_id: str
    critic_invocation_id: str
    decision: PostVerdictCriticDecisionType
    revision_kind: PostVerdictRevisionKind
    diagnosis: str
    next_step_rationale: str
    prompt_version: str
    contract_version: str = POST_VERDICT_RESEARCH_INTENT_CONTRACT_VERSION
    provider: str | None = None
    model: str | None = None
    research_scope_snapshot: Mapping[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=utcnow)

    def __post_init__(self) -> None:
        for field_name in (
            "scientific_verdict_id",
            "research_brief_id",
            "hypothesis_claim_set_id",
            "research_design_intent_id",
            "research_prediction_plan_id",
            "contrast_result_id",
            "critic_invocation_id",
            "prompt_version",
            "contract_version",
        ):
            if not getattr(self, field_name):
                raise ValueError(f"PostVerdictResearchIntent requires {field_name}")
        if not isinstance(self.decision, PostVerdictCriticDecisionType):
            raise ValueError(f"Invalid decision: {self.decision!r}")
        if not isinstance(self.revision_kind, PostVerdictRevisionKind):
            raise ValueError(f"Invalid revision_kind: {self.revision_kind!r}")
        if not self.diagnosis or not self.diagnosis.strip():
            raise ValueError("PostVerdictResearchIntent requires non-empty diagnosis")
        if not self.next_step_rationale or not self.next_step_rationale.strip():
            raise ValueError("PostVerdictResearchIntent requires non-empty next_step_rationale")
        validate_post_verdict_critic_decision(
            PostVerdictCriticDecision(
                id=new_id(),
                scientific_verdict_id=self.scientific_verdict_id,
                decision=self.decision,
                revision_kind=self.revision_kind,
                diagnosis=self.diagnosis,
                next_step_rationale=self.next_step_rationale,
            )
        )
        if not isinstance(self.research_scope_snapshot, Mapping) or not self.research_scope_snapshot:
            raise ValueError("PostVerdictResearchIntent requires non-empty research_scope_snapshot")
        object.__setattr__(
            self,
            "research_scope_snapshot",
            freeze_json_value(dict(self.research_scope_snapshot)),
        )

    def research_scope_payload(self) -> dict[str, Any]:
        value = thaw_json_value(self.research_scope_snapshot)
        if not isinstance(value, dict):
            raise ValueError("research_scope_snapshot must thaw to a mapping")
        return value
