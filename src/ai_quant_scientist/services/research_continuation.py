"""Governed adaptive hypothesis continuation for V0.17."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol

from ..capabilities.serialization import compute_candidate_fingerprint
from ..models.hypothesis_scientist import (
    HypothesisScientistDecision,
    HypothesisScientistDecisionType,
    PriorCandidateSummary,
    ResearchBrief,
    ResearchScope,
    compute_hypothesis_claim_signature,
)
from ..models.post_verdict_critic import PostVerdictCriticDecisionType, PostVerdictRevisionKind
from ..models.research import new_id, thaw_json_value, utcnow
from ..models.research_continuation import (
    AdaptiveHypothesisLineage,
    ResearchContinuationAttemptStatus,
    ResearchContinuationAuthorization,
    ResearchContinuationAuthorizationStatus,
    ResearchContinuationContext,
    ResearchContinuationInvocation,
    ResearchContinuationOrigin,
    compute_research_scope_fingerprint,
)
from .hypothesis_scientist import (
    HypothesisProposalValidator,
    brief_to_json,
    materialize_hypothesis_claim_set,
    materialize_research_candidate,
)
from .post_verdict_research_critic import _require_present
from .research_designer import hypothesis_claim_set_to_payload


class ContinuationHypothesisScientist(Protocol):
    provider: str
    model: str
    prompt_version: str

    def generate(self, context: ResearchContinuationContext) -> HypothesisScientistDecision:
        ...


@dataclass(frozen=True, slots=True)
class GovernedResearchContinuationResult:
    authorization: ResearchContinuationAuthorization
    invocation: ResearchContinuationInvocation | None
    candidate: Any | None
    claim_set: Any | None
    status: ResearchContinuationAttemptStatus | ResearchContinuationAuthorizationStatus
    reused_existing: bool


def continuation_context_to_payload(context: ResearchContinuationContext) -> dict[str, Any]:
    return {
        "continuation_authorization_id": context.continuation_authorization_id,
        "post_verdict_research_intent_id": context.post_verdict_research_intent_id,
        "parent_scientific_verdict_id": context.parent_scientific_verdict_id,
        "generation_number": context.generation_number,
        "origin": context.origin.value,
        "research_scope": context.research_scope_payload(),
        "parent_hypothesis_claim_set": thaw_json_value(context.parent_hypothesis_claim_set_snapshot),
        "parent_candidate_summary": {
            "fingerprint": context.parent_candidate_summary.fingerprint,
            "hypothesis_statement": context.parent_candidate_summary.hypothesis_statement,
            "hypothesis_rationale_summary": context.parent_candidate_summary.hypothesis_rationale_summary,
        },
        "parent_verdict_status": context.parent_verdict_status,
        "critic_decision": context.critic_decision.value,
        "critic_revision_kind": context.critic_revision_kind.value,
        "critic_diagnosis": context.critic_diagnosis,
        "critic_next_step_rationale": context.critic_next_step_rationale,
        "context_version": context.context_version,
    }


def continuation_context_to_json(context: ResearchContinuationContext) -> str:
    return json.dumps(continuation_context_to_payload(context), sort_keys=True)


def continuation_decision_to_json(decision: HypothesisScientistDecision) -> str:
    return json.dumps(
        {
            "decision_type": decision.decision_type.value,
            "hypothesis_statement": decision.hypothesis_statement,
            "hypothesis_rationale": decision.hypothesis_rationale,
            "requirements_snapshot": decision.requirements_snapshot,
            "no_hypothesis_reason": decision.no_hypothesis_reason,
            "independent_variable": (
                None if decision.independent_variable is None else decision.independent_variable.value
            ),
            "independent_variable_direction": (
                None
                if decision.independent_variable_direction is None
                else decision.independent_variable_direction.value
            ),
            "outcome_claims": (
                None
                if decision.outcome_claims is None
                else [
                    {
                        "outcome": item.outcome.value,
                        "expected_direction": item.expected_direction.value,
                    }
                    for item in decision.outcome_claims
                ]
            ),
            "claim_aggregation": (
                None if decision.claim_aggregation is None else decision.claim_aggregation.value
            ),
            "provider": decision.provider,
            "model": decision.model,
            "prompt_version": decision.prompt_version,
            "ontology_version": decision.ontology_version,
            "ontology_fingerprint": decision.ontology_fingerprint,
        },
        sort_keys=True,
    )


class ContinuationHypothesisProposalValidator:
    """Deterministic V0.17 validator for one adaptive continuation attempt."""

    def __init__(self) -> None:
        self._base_validator = HypothesisProposalValidator()

    def validate(
        self,
        decision: HypothesisScientistDecision,
        context: ResearchContinuationContext,
    ) -> tuple[bool, dict[str, str]]:
        brief = ResearchBrief.create(
            research_question="Adaptive continuation under frozen post-verdict scope.",
            research_scope=context.research_scope_payload(),
            prior_candidate_summaries=[
                {
                    "fingerprint": context.parent_candidate_summary.fingerprint,
                    "hypothesis_statement": context.parent_candidate_summary.hypothesis_statement,
                    "hypothesis_rationale_summary": context.parent_candidate_summary.hypothesis_rationale_summary,
                }
            ],
            source="research_continuation_v1",
        )
        errors: dict[str, str] = {}

        if decision.research_brief_id != context.continuation_authorization_id:
            errors["research_brief_id"] = (
                "Continuation Hypothesis Scientist decision research_brief_id must match the continuation authorization id"
            )

        valid, base_errors = self._base_validator.validate(decision, brief)
        errors.update(base_errors)
        if not valid:
            return False, errors

        if decision.decision_type == HypothesisScientistDecisionType.NO_HYPOTHESIS:
            return len(errors) == 0, errors

        parent_claim_set = _parent_claim_set_from_context(context)
        parent_signature = compute_hypothesis_claim_signature(
            independent_variable=parent_claim_set.independent_variable,
            independent_variable_direction=parent_claim_set.independent_variable_direction,
            claims=parent_claim_set.claims,
            claim_aggregation=parent_claim_set.claim_aggregation,
        )
        child_signature = compute_hypothesis_claim_signature(
            independent_variable=decision.independent_variable,
            independent_variable_direction=decision.independent_variable_direction,
            claims=decision.outcome_claims or (),
            claim_aggregation=decision.claim_aggregation,
        )
        if context.critic_revision_kind == PostVerdictRevisionKind.MECHANISM_REVISION:
            if child_signature == parent_signature:
                errors["continuation_novelty"] = (
                    "MECHANISM_REVISION requires a child canonical claim signature different from the parent hypothesis"
                )
        return len(errors) == 0, errors


@dataclass(frozen=True, slots=True)
class _ParentLineage:
    candidate: Any
    claim_set: Any
    verdict: Any
    intent: Any


def _parent_claim_set_from_context(context: ResearchContinuationContext):
    payload = context.parent_hypothesis_claim_set_snapshot
    from ..models.design import DesignOutcome, DesignVariable, ExpectedDirection, OutcomePrediction
    from ..models.hypothesis_scientist import HypothesisClaimAggregation, HypothesisClaimSet

    return HypothesisClaimSet(
        id="parent",
        candidate_id="parent",
        hypothesis_scientist_invocation_id="parent",
        independent_variable=DesignVariable(payload["independent_variable"]),
        independent_variable_direction=ExpectedDirection(payload["independent_variable_direction"]),
        claims=tuple(
            OutcomePrediction(
                outcome=DesignOutcome(item["outcome"]),
                expected_direction=ExpectedDirection(item["expected_direction"]),
            )
            for item in payload["claims"]
        ),
        claim_aggregation=HypothesisClaimAggregation(payload["claim_aggregation"]),
        claim_contract_version=payload["claim_contract_version"],
        ontology_version=payload["ontology_version"],
        ontology_fingerprint=payload["ontology_fingerprint"],
    )


class GovernedResearchContinuation:
    """Explicitly authorized one-attempt adaptive hypothesis continuation."""

    def __init__(
        self,
        *,
        store,
        scientist: ContinuationHypothesisScientist,
        validator: ContinuationHypothesisProposalValidator | None = None,
    ) -> None:
        self._store = store
        self._scientist = scientist
        self._validator = validator or ContinuationHypothesisProposalValidator()

    def prepare(self, post_verdict_research_intent_id: str) -> ResearchContinuationAuthorization:
        existing = self._store.get_research_continuation_authorization_by_post_verdict_research_intent_id(
            post_verdict_research_intent_id
        )
        if existing is not None:
            return existing
        parent = _load_parent_lineage(self._store, post_verdict_research_intent_id)
        intent = parent.intent
        if intent.decision != PostVerdictCriticDecisionType.CONTINUE:
            raise RuntimeError("Research continuation requires PostVerdictResearchIntent decision CONTINUE")
        if intent.revision_kind != PostVerdictRevisionKind.MECHANISM_REVISION:
            raise RuntimeError("V0.17 supports only MECHANISM_REVISION continuation authorizations")
        scope_payload = intent.research_scope_payload()
        authorization = ResearchContinuationAuthorization(
            id=new_id(),
            post_verdict_research_intent_id=intent.id,
            parent_scientific_verdict_id=intent.scientific_verdict_id,
            parent_hypothesis_claim_set_id=intent.hypothesis_claim_set_id,
            parent_candidate_id=parent.claim_set.candidate_id,
            research_scope_snapshot=scope_payload,
            research_scope_fingerprint=compute_research_scope_fingerprint(scope_payload),
            allowed_revision_kind=intent.revision_kind,
            generation_number=2,
            origin=ResearchContinuationOrigin.POST_VERDICT_ADAPTIVE,
            authorization_status=ResearchContinuationAuthorizationStatus.PENDING,
        )
        self._store.save_research_continuation_authorization(authorization)
        return authorization

    def authorize(self, continuation_authorization_id: str) -> ResearchContinuationAuthorization:
        authorization = _require_present(
            self._store.get_research_continuation_authorization(continuation_authorization_id),
            f"ResearchContinuationAuthorization not found: {continuation_authorization_id!r}",
        )
        if authorization.authorization_status == ResearchContinuationAuthorizationStatus.PENDING:
            authorization = ResearchContinuationAuthorization(
                id=authorization.id,
                post_verdict_research_intent_id=authorization.post_verdict_research_intent_id,
                parent_scientific_verdict_id=authorization.parent_scientific_verdict_id,
                parent_hypothesis_claim_set_id=authorization.parent_hypothesis_claim_set_id,
                parent_candidate_id=authorization.parent_candidate_id,
                research_scope_snapshot=authorization.research_scope_payload(),
                research_scope_fingerprint=authorization.research_scope_fingerprint,
                allowed_revision_kind=authorization.allowed_revision_kind,
                generation_number=authorization.generation_number,
                origin=authorization.origin,
                authorization_status=ResearchContinuationAuthorizationStatus.AUTHORIZED,
                contract_version=authorization.contract_version,
                created_at=authorization.created_at,
                authorized_at=utcnow() if authorization.authorized_at is None else authorization.authorized_at,
            )
            self._store.update_research_continuation_authorization(authorization)
        return authorization

    def generate_hypothesis(
        self,
        continuation_authorization_id: str,
    ) -> GovernedResearchContinuationResult:
        authorization = _require_present(
            self._store.get_research_continuation_authorization(continuation_authorization_id),
            f"ResearchContinuationAuthorization not found: {continuation_authorization_id!r}",
        )
        if authorization.authorization_status == ResearchContinuationAuthorizationStatus.PENDING:
            raise RuntimeError("Research continuation is awaiting explicit continuation authorization")
        existing_invocation = self._store.get_research_continuation_invocation_by_authorization_id(
            continuation_authorization_id
        )
        if existing_invocation is not None:
            return _result_from_existing(self._store, authorization, existing_invocation)
        parent = _load_parent_lineage(self._store, authorization.post_verdict_research_intent_id)
        context = _build_context(authorization=authorization, parent=parent)
        reservation = ResearchContinuationInvocation(
            id=new_id(),
            continuation_authorization_id=authorization.id,
            post_verdict_research_intent_id=authorization.post_verdict_research_intent_id,
            parent_scientific_verdict_id=authorization.parent_scientific_verdict_id,
            context_version=context.context_version,
            prompt_version=getattr(self._scientist, "prompt_version", None),
            provider=getattr(self._scientist, "provider", None),
            model=getattr(self._scientist, "model", None),
            context_snapshot_json=continuation_context_to_json(context),
            raw_response=None,
            parsed_decision_json=None,
            attempt_status=ResearchContinuationAttemptStatus.IN_PROGRESS,
            validation_errors_json=None,
            resulting_candidate_id=None,
            resulting_claim_set_id=None,
        )
        if not self._store.try_reserve_research_continuation_invocation(reservation):
            existing_invocation = self._store.get_research_continuation_invocation_by_authorization_id(
                continuation_authorization_id
            )
            if existing_invocation is None:
                authorization = _require_present(
                    self._store.get_research_continuation_authorization(continuation_authorization_id),
                    f"ResearchContinuationAuthorization not found: {continuation_authorization_id!r}",
                )
                return GovernedResearchContinuationResult(
                    authorization=authorization,
                    invocation=None,
                    candidate=None,
                    claim_set=None,
                    status=authorization.authorization_status,
                    reused_existing=True,
                )
            authorization = _require_present(
                self._store.get_research_continuation_authorization(continuation_authorization_id),
                f"ResearchContinuationAuthorization not found: {continuation_authorization_id!r}",
            )
            return _result_from_existing(self._store, authorization, existing_invocation)

        authorization = _require_present(
            self._store.get_research_continuation_authorization(continuation_authorization_id),
            f"ResearchContinuationAuthorization not found: {continuation_authorization_id!r}",
        )
        try:
            decision = self._scientist.generate(context)
        except Exception as exc:
            invocation = ResearchContinuationInvocation(
                id=reservation.id,
                continuation_authorization_id=reservation.continuation_authorization_id,
                post_verdict_research_intent_id=reservation.post_verdict_research_intent_id,
                parent_scientific_verdict_id=reservation.parent_scientific_verdict_id,
                context_version=reservation.context_version,
                prompt_version=reservation.prompt_version,
                provider=reservation.provider,
                model=reservation.model,
                context_snapshot_json=reservation.context_snapshot_json,
                raw_response=None,
                parsed_decision_json=None,
                attempt_status=ResearchContinuationAttemptStatus.PROVIDER_ERROR,
                validation_errors_json=json.dumps(
                    {"provider_error": f"{type(exc).__name__}: {exc}"},
                    sort_keys=True,
                ),
                resulting_candidate_id=None,
                resulting_claim_set_id=None,
                created_at=reservation.created_at,
            )
            self._store.update_research_continuation_invocation(invocation)
            return GovernedResearchContinuationResult(
                authorization=authorization,
                invocation=invocation,
                candidate=None,
                claim_set=None,
                status=invocation.attempt_status,
                reused_existing=False,
            )

        valid, errors = self._validator.validate(decision, context)
        parsed_decision_json = continuation_decision_to_json(decision)
        if decision.decision_type == HypothesisScientistDecisionType.NO_HYPOTHESIS:
            invocation = ResearchContinuationInvocation(
                id=reservation.id,
                continuation_authorization_id=reservation.continuation_authorization_id,
                post_verdict_research_intent_id=reservation.post_verdict_research_intent_id,
                parent_scientific_verdict_id=reservation.parent_scientific_verdict_id,
                context_version=reservation.context_version,
                prompt_version=reservation.prompt_version,
                provider=reservation.provider,
                model=reservation.model,
                context_snapshot_json=reservation.context_snapshot_json,
                raw_response=decision.raw_response,
                parsed_decision_json=parsed_decision_json,
                attempt_status=ResearchContinuationAttemptStatus.NO_HYPOTHESIS,
                validation_errors_json=json.dumps(errors, sort_keys=True) if errors else None,
                resulting_candidate_id=None,
                resulting_claim_set_id=None,
                created_at=reservation.created_at,
            )
            self._store.update_research_continuation_invocation(invocation)
            return GovernedResearchContinuationResult(
                authorization=authorization,
                invocation=invocation,
                candidate=None,
                claim_set=None,
                status=invocation.attempt_status,
                reused_existing=False,
            )

        if not valid:
            invocation = ResearchContinuationInvocation(
                id=reservation.id,
                continuation_authorization_id=reservation.continuation_authorization_id,
                post_verdict_research_intent_id=reservation.post_verdict_research_intent_id,
                parent_scientific_verdict_id=reservation.parent_scientific_verdict_id,
                context_version=reservation.context_version,
                prompt_version=reservation.prompt_version,
                provider=reservation.provider,
                model=reservation.model,
                context_snapshot_json=reservation.context_snapshot_json,
                raw_response=decision.raw_response,
                parsed_decision_json=parsed_decision_json,
                attempt_status=ResearchContinuationAttemptStatus.INVALID_ATTEMPT,
                validation_errors_json=json.dumps(errors, sort_keys=True),
                resulting_candidate_id=None,
                resulting_claim_set_id=None,
                created_at=reservation.created_at,
            )
            self._store.update_research_continuation_invocation(invocation)
            return GovernedResearchContinuationResult(
                authorization=authorization,
                invocation=invocation,
                candidate=None,
                claim_set=None,
                status=invocation.attempt_status,
                reused_existing=False,
            )

        synthetic_brief = ResearchBrief.create(
            research_question="Adaptive continuation under frozen post-verdict scope.",
            research_scope=context.research_scope_payload(),
            prior_candidate_summaries=[
                {
                    "fingerprint": context.parent_candidate_summary.fingerprint,
                    "hypothesis_statement": context.parent_candidate_summary.hypothesis_statement,
                    "hypothesis_rationale_summary": context.parent_candidate_summary.hypothesis_rationale_summary,
                }
            ],
            source="research_continuation_v1",
        )
        candidate = materialize_research_candidate(decision, synthetic_brief)
        claim_set = materialize_hypothesis_claim_set(
            decision,
            candidate_id=candidate.id,
            hypothesis_scientist_invocation_id=reservation.id,
        )
        invocation = ResearchContinuationInvocation(
            id=reservation.id,
            continuation_authorization_id=reservation.continuation_authorization_id,
            post_verdict_research_intent_id=reservation.post_verdict_research_intent_id,
            parent_scientific_verdict_id=reservation.parent_scientific_verdict_id,
            context_version=reservation.context_version,
            prompt_version=reservation.prompt_version,
            provider=reservation.provider,
            model=reservation.model,
            context_snapshot_json=reservation.context_snapshot_json,
            raw_response=decision.raw_response,
            parsed_decision_json=parsed_decision_json,
            attempt_status=ResearchContinuationAttemptStatus.GENERATED_ADAPTIVE_HYPOTHESIS,
            validation_errors_json=None,
            resulting_candidate_id=candidate.id,
            resulting_claim_set_id=None if claim_set is None else claim_set.id,
            created_at=reservation.created_at,
        )
        lineage = AdaptiveHypothesisLineage(
            id=new_id(),
            candidate_id=candidate.id,
            hypothesis_claim_set_id=claim_set.id,
            continuation_authorization_id=authorization.id,
            post_verdict_research_intent_id=authorization.post_verdict_research_intent_id,
            parent_scientific_verdict_id=authorization.parent_scientific_verdict_id,
            parent_hypothesis_claim_set_id=authorization.parent_hypothesis_claim_set_id,
            parent_candidate_id=authorization.parent_candidate_id,
            origin=authorization.origin,
            generation_number=authorization.generation_number,
            research_scope_snapshot=authorization.research_scope_payload(),
            research_scope_fingerprint=authorization.research_scope_fingerprint,
        )
        self._store.save_research_continuation_success_bundle(
            invocation=invocation,
            candidate=candidate,
            claim_set=claim_set,
            lineage=lineage,
        )
        return GovernedResearchContinuationResult(
            authorization=authorization,
            invocation=invocation,
            candidate=candidate,
            claim_set=claim_set,
            status=invocation.attempt_status,
            reused_existing=False,
        )


def _load_parent_lineage(store, post_verdict_research_intent_id: str) -> _ParentLineage:
    intent = _require_present(
        store.get_post_verdict_research_intent(post_verdict_research_intent_id),
        f"PostVerdictResearchIntent not found: {post_verdict_research_intent_id!r}",
    )
    verdict = _require_present(
        store.get_scientific_verdict(intent.scientific_verdict_id),
        f"ScientificVerdict not found: {intent.scientific_verdict_id!r}",
    )
    if verdict.overall_status.value != "FALSIFIED":
        raise RuntimeError("Research continuation requires a FALSIFIED parent ScientificVerdict")
    claim_set = _require_present(
        store.get_hypothesis_claim_set(intent.hypothesis_claim_set_id),
        f"HypothesisClaimSet not found: {intent.hypothesis_claim_set_id!r}",
    )
    prediction_plan = _require_present(
        store.get_research_prediction_plan(intent.research_prediction_plan_id),
        f"ResearchPredictionPlan not found: {intent.research_prediction_plan_id!r}",
    )
    contrast_result = _require_present(
        store.get_parameter_sensitivity_contrast_result_by_id(intent.contrast_result_id),
        f"ParameterSensitivityContrastResult not found: {intent.contrast_result_id!r}",
    )
    candidate = _require_present(
        store.get_research_candidate(claim_set.candidate_id),
        f"ResearchCandidate not found: {claim_set.candidate_id!r}",
    )
    if claim_set.id != intent.hypothesis_claim_set_id:
        raise ValueError("PostVerdictResearchIntent parent claim set lineage is inconsistent")
    if verdict.id != intent.scientific_verdict_id:
        raise ValueError("PostVerdictResearchIntent parent scientific verdict lineage is inconsistent")
    if prediction_plan.id != verdict.prediction_plan_id or prediction_plan.id != intent.research_prediction_plan_id:
        raise ValueError("PostVerdictResearchIntent parent prediction-plan lineage is inconsistent")
    if prediction_plan.hypothesis_claim_set_id != claim_set.id:
        raise ValueError("PostVerdictResearchIntent parent claim set does not match prediction-plan lineage")
    if verdict.design_intent_id != intent.research_design_intent_id:
        raise ValueError("PostVerdictResearchIntent parent design-intent lineage is inconsistent")
    if verdict.contrast_result_id != contrast_result.id or verdict.contrast_result_id != intent.contrast_result_id:
        raise ValueError("PostVerdictResearchIntent parent contrast lineage is inconsistent")
    return _ParentLineage(candidate=candidate, claim_set=claim_set, verdict=verdict, intent=intent)


def _build_context(
    *,
    authorization: ResearchContinuationAuthorization,
    parent: _ParentLineage,
) -> ResearchContinuationContext:
    candidate_fingerprint = compute_candidate_fingerprint(
        parent.candidate.hypothesis_statement,
        parent.candidate.hypothesis_rationale,
        parent.candidate.requirements,
    )
    return ResearchContinuationContext(
        id=new_id(),
        continuation_authorization_id=authorization.id,
        post_verdict_research_intent_id=authorization.post_verdict_research_intent_id,
        parent_scientific_verdict_id=authorization.parent_scientific_verdict_id,
        generation_number=authorization.generation_number,
        origin=authorization.origin,
        research_scope_snapshot=authorization.research_scope_payload(),
        parent_hypothesis_claim_set_snapshot=hypothesis_claim_set_to_payload(parent.claim_set) or {},
        parent_candidate_summary=PriorCandidateSummary(
            fingerprint=candidate_fingerprint,
            hypothesis_statement=parent.candidate.hypothesis_statement,
            hypothesis_rationale_summary=parent.candidate.hypothesis_rationale,
        ),
        parent_verdict_status=parent.verdict.overall_status.value,
        critic_decision=parent.intent.decision,
        critic_revision_kind=parent.intent.revision_kind,
        critic_diagnosis=parent.intent.diagnosis,
        critic_next_step_rationale=parent.intent.next_step_rationale,
    )


def _result_from_existing(
    store,
    authorization: ResearchContinuationAuthorization,
    invocation: ResearchContinuationInvocation,
) -> GovernedResearchContinuationResult:
    candidate = (
        None
        if invocation.resulting_candidate_id is None
        else store.get_research_candidate(invocation.resulting_candidate_id)
    )
    claim_set = (
        None
        if invocation.resulting_claim_set_id is None
        else store.get_hypothesis_claim_set(invocation.resulting_claim_set_id)
    )
    return GovernedResearchContinuationResult(
        authorization=authorization,
        invocation=invocation,
        candidate=candidate,
        claim_set=claim_set,
        status=invocation.attempt_status,
        reused_existing=True,
    )
