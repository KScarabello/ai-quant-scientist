"""Governed bounded post-verdict Critic service for V0.16."""

from __future__ import annotations

import json
import re
from dataclasses import is_dataclass
from datetime import datetime
from enum import Enum
from dataclasses import dataclass
from typing import Any, Protocol

from ..models.design import DesignOutcome, DesignVariable, ScientificVerdictStatus
from ..models.hypothesis_scientist import ResearchScope
from ..models.post_verdict_critic import (
    POST_VERDICT_RESEARCH_INTENT_CONTRACT_VERSION,
    PostVerdictCriticContext,
    PostVerdictCriticDecision,
    PostVerdictCriticDecisionType,
    PostVerdictCriticInvocation,
    PostVerdictResearchIntent,
    PostVerdictRevisionKind,
    validate_post_verdict_critic_decision,
)
from ..models.research import new_id, thaw_json_value
from .research_designer import candidate_to_payload, hypothesis_claim_set_to_payload
from .scientific_verdict import validate_prediction_plan_experiment_plan_contrast_linkage


_PARAMETER_ASSIGNMENT_RE = re.compile(
    r"\b(signal_threshold|lookback)\b\s*(=|:)?\s*-?\d+(?:\.\d+)?",
    re.IGNORECASE,
)
_CAPABILITY_ID_RE = re.compile(r"\bstub_backtester_v1\b|\bdeterministic_backtest_execution\b", re.IGNORECASE)
_NEW_HYPOTHESIS_RE = re.compile(
    r"\b(next|future)\s+hypothesis\b|\bwe should hypothesize\b|\bthe next hypothesis\b",
    re.IGNORECASE,
)
_FUTURE_DIRECTION_RE = re.compile(
    r"(\b(next|future|follow-?up|subsequent|another)\b.{0,40}\b(increase|decrease|no change)\b)"
    r"|(\b(increase|decrease|no change)\b.{0,40}\b(next|future|follow-?up|subsequent)\b)",
    re.IGNORECASE,
)
_OUTCOME_TOKEN_RE = re.compile(r"\b(trade_count|sharpe|net_pnl|score)\b", re.IGNORECASE)
_INDEPENDENT_VARIABLE_TOKEN_RE = re.compile(r"\b(signal_threshold|lookback)\b", re.IGNORECASE)
_EXECUTABLE_DESIGN_RE = re.compile(
    r"\b(researchspec|experiment plan|design kind|comparison_intent|analysis_intent|condition\s*[12])\b",
    re.IGNORECASE,
)


class PostVerdictResearchCritic(Protocol):
    provider: str
    model: str
    prompt_version: str

    def critique(self, context: PostVerdictCriticContext) -> PostVerdictCriticDecision:
        ...


@dataclass(frozen=True, slots=True)
class GovernedPostVerdictResearchCriticResult:
    invocation: PostVerdictCriticInvocation
    decision: PostVerdictCriticDecision
    intent: PostVerdictResearchIntent
    reused_existing: bool


def context_to_payload(context: PostVerdictCriticContext) -> dict[str, Any]:
    return {
        "scientific_verdict_id": context.scientific_verdict_id,
        "research_brief_id": context.research_brief_id,
        "research_scope": thaw_json_value(context.research_scope_snapshot),
        "research_brief": thaw_json_value(context.research_brief_snapshot),
        "candidate": thaw_json_value(context.candidate_snapshot),
        "candidate_feasibility": (
            None
            if context.candidate_feasibility_snapshot is None
            else thaw_json_value(context.candidate_feasibility_snapshot)
        ),
        "hypothesis_claim_set": thaw_json_value(context.hypothesis_claim_set_snapshot),
        "research_design_intent": thaw_json_value(context.research_design_intent_snapshot),
        "research_prediction_plan": thaw_json_value(context.research_prediction_plan_snapshot),
        "initial_experiment_plan": thaw_json_value(context.initial_experiment_plan_snapshot),
        "contrast_result": thaw_json_value(context.contrast_result_snapshot),
        "scientific_verdict": thaw_json_value(context.scientific_verdict_snapshot),
        "context_version": context.context_version,
    }


def context_to_json(context: PostVerdictCriticContext) -> str:
    return json.dumps(context_to_payload(context), sort_keys=True)


def decision_to_json(decision: PostVerdictCriticDecision) -> str:
    return json.dumps(
        {
            "decision": decision.decision.value,
            "revision_kind": decision.revision_kind.value,
            "diagnosis": decision.diagnosis,
            "next_step_rationale": decision.next_step_rationale,
            "provider": decision.provider,
            "model": decision.model,
            "prompt_version": decision.prompt_version,
        },
        sort_keys=True,
    )


class PostVerdictCriticDecisionValidator:
    """Deterministic validator for the bounded V0.16 Critic contract."""

    def validate(
        self,
        decision: PostVerdictCriticDecision,
        context: PostVerdictCriticContext,
    ) -> tuple[bool, dict[str, str]]:
        errors: dict[str, str] = {}

        if decision.scientific_verdict_id != context.scientific_verdict_id:
            errors["scientific_verdict_id"] = "Decision scientific_verdict_id must match the governed context"

        try:
            validate_post_verdict_critic_decision(decision)
        except ValueError as exc:
            errors["decision_contract"] = str(exc)

        scope = _research_scope_from_context(context)
        allowed_outcomes = {item.value for item in scope.requested_outcomes}
        allowed_independent_variable = scope.independent_variable.value

        for field_name, text in (
            ("diagnosis", decision.diagnosis),
            ("next_step_rationale", decision.next_step_rationale),
        ):
            self._validate_text(
                field_name=field_name,
                text=text,
                allowed_outcomes=allowed_outcomes,
                allowed_independent_variable=allowed_independent_variable,
                errors=errors,
            )

        return len(errors) == 0, errors

    def _validate_text(
        self,
        *,
        field_name: str,
        text: str,
        allowed_outcomes: set[str],
        allowed_independent_variable: str,
        errors: dict[str, str],
    ) -> None:
        stripped = text.strip()
        if not stripped:
            errors[field_name] = f"{field_name} must be non-empty"
            return
        if _PARAMETER_ASSIGNMENT_RE.search(stripped):
            errors[field_name] = f"{field_name} must not encode exact execution parameter values"
            return
        if _CAPABILITY_ID_RE.search(stripped):
            errors[field_name] = f"{field_name} must not mention capability IDs"
            return
        if _EXECUTABLE_DESIGN_RE.search(stripped):
            errors[field_name] = f"{field_name} must remain non-executable"
            return
        if _NEW_HYPOTHESIS_RE.search(stripped):
            errors[field_name] = f"{field_name} must not author the next hypothesis"
            return
        if _FUTURE_DIRECTION_RE.search(stripped):
            errors[field_name] = f"{field_name} must not precommit a future expected direction"
            return

        outcomes = {token.lower() for token in _OUTCOME_TOKEN_RE.findall(stripped)}
        unsupported_outcomes = sorted(outcomes - allowed_outcomes)
        if unsupported_outcomes:
            errors[field_name] = (
                f"{field_name} must not introduce outcomes outside the frozen ResearchScope: {unsupported_outcomes}"
            )
            return

        independent_variables = {token.lower() for token in _INDEPENDENT_VARIABLE_TOKEN_RE.findall(stripped)}
        disallowed_independent_variables = sorted(
            token for token in independent_variables if token != allowed_independent_variable
        )
        if disallowed_independent_variables:
            errors[field_name] = (
                f"{field_name} must not introduce a new independent variable under the frozen ResearchScope: "
                f"{disallowed_independent_variables}"
            )


class GovernedPostVerdictResearchCritic:
    """One bounded post-verdict Critic invocation per exact scientific verdict."""

    def __init__(
        self,
        *,
        store,
        critic: PostVerdictResearchCritic,
        validator: PostVerdictCriticDecisionValidator | None = None,
    ) -> None:
        self._store = store
        self._critic = critic
        self._validator = validator or PostVerdictCriticDecisionValidator()

    def critique(self, scientific_verdict_id: str) -> GovernedPostVerdictResearchCriticResult:
        existing_intent = self._store.get_post_verdict_research_intent_by_scientific_verdict_id(
            scientific_verdict_id
        )
        if existing_intent is not None:
            invocation = self._store.get_post_verdict_critic_invocation(existing_intent.critic_invocation_id)
            if invocation is None:
                raise RuntimeError("Existing PostVerdictResearchIntent has no authoritative Critic invocation")
            decision = PostVerdictCriticDecision(
                id=new_id(),
                scientific_verdict_id=existing_intent.scientific_verdict_id,
                decision=existing_intent.decision,
                revision_kind=existing_intent.revision_kind,
                diagnosis=existing_intent.diagnosis,
                next_step_rationale=existing_intent.next_step_rationale,
                provider=existing_intent.provider,
                model=existing_intent.model,
                prompt_version=existing_intent.prompt_version,
                raw_response=invocation.raw_response,
            )
            return GovernedPostVerdictResearchCriticResult(
                invocation=invocation,
                decision=decision,
                intent=existing_intent,
                reused_existing=True,
            )

        chain = _load_validated_chain(self._store, scientific_verdict_id)
        if chain["scientific_verdict"].overall_status != ScientificVerdictStatus.FALSIFIED:
            raise RuntimeError("Post-verdict Critic is only applicable to FALSIFIED scientific verdicts")
        context = _build_context(chain)
        reservation = PostVerdictCriticInvocation(
            id=new_id(),
            scientific_verdict_id=scientific_verdict_id,
            context_version=context.context_version,
            prompt_version=getattr(self._critic, "prompt_version", None),
            provider=getattr(self._critic, "provider", None),
            model=getattr(self._critic, "model", None),
            context_snapshot_json=context_to_json(context),
            raw_response=None,
            parsed_decision_json=None,
            validation_status="IN_PROGRESS",
            validation_errors_json=None,
            resulting_intent_id=None,
        )
        if not self._store.try_create_post_verdict_critic_invocation(reservation):
            existing_intent = self._store.get_post_verdict_research_intent_by_scientific_verdict_id(
                scientific_verdict_id
            )
            if existing_intent is not None:
                invocation = self._store.get_post_verdict_critic_invocation(existing_intent.critic_invocation_id)
                if invocation is None:
                    raise RuntimeError(
                        "Existing PostVerdictResearchIntent has no authoritative Critic invocation"
                    )
                decision = PostVerdictCriticDecision(
                    id=new_id(),
                    scientific_verdict_id=existing_intent.scientific_verdict_id,
                    decision=existing_intent.decision,
                    revision_kind=existing_intent.revision_kind,
                    diagnosis=existing_intent.diagnosis,
                    next_step_rationale=existing_intent.next_step_rationale,
                    provider=existing_intent.provider,
                    model=existing_intent.model,
                    prompt_version=existing_intent.prompt_version,
                    raw_response=invocation.raw_response,
                )
                return GovernedPostVerdictResearchCriticResult(
                    invocation=invocation,
                    decision=decision,
                    intent=existing_intent,
                    reused_existing=True,
                )
            raise RuntimeError(
                "Post-verdict Critic invocation budget already consumed for this scientific verdict"
            )

        try:
            decision = self._critic.critique(context)
        except Exception as exc:
            self._store.update_post_verdict_critic_invocation(
                PostVerdictCriticInvocation(
                    id=reservation.id,
                    scientific_verdict_id=reservation.scientific_verdict_id,
                    context_version=reservation.context_version,
                    prompt_version=reservation.prompt_version,
                    provider=reservation.provider,
                    model=reservation.model,
                    context_snapshot_json=reservation.context_snapshot_json,
                    raw_response=None,
                    parsed_decision_json=None,
                    validation_status="ERROR",
                    validation_errors_json=json.dumps(
                        {"provider_error": f"{type(exc).__name__}: {exc}"},
                        sort_keys=True,
                    ),
                    resulting_intent_id=None,
                    created_at=reservation.created_at,
                )
            )
            raise

        valid, errors = self._validator.validate(decision, context)
        invocation = PostVerdictCriticInvocation(
            id=reservation.id,
            scientific_verdict_id=reservation.scientific_verdict_id,
            context_version=reservation.context_version,
            prompt_version=reservation.prompt_version,
            provider=reservation.provider,
            model=reservation.model,
            context_snapshot_json=reservation.context_snapshot_json,
            raw_response=decision.raw_response,
            parsed_decision_json=decision_to_json(decision),
            validation_status="VALID" if valid else "INVALID",
            validation_errors_json=json.dumps(errors, sort_keys=True) if errors else None,
            resulting_intent_id=None,
            created_at=reservation.created_at,
        )
        if not valid:
            self._store.update_post_verdict_critic_invocation(invocation)
            raise ValueError(f"Invalid post-verdict Critic decision: {json.dumps(errors, sort_keys=True)}")

        intent = PostVerdictResearchIntent(
            id=new_id(),
            scientific_verdict_id=scientific_verdict_id,
            research_brief_id=context.research_brief_id,
            hypothesis_claim_set_id=chain["claim_set"].id,
            research_design_intent_id=chain["design_intent"].id,
            research_prediction_plan_id=chain["prediction_plan"].id,
            contrast_result_id=chain["contrast_result"].id,
            critic_invocation_id=reservation.id,
            decision=decision.decision,
            revision_kind=decision.revision_kind,
            diagnosis=decision.diagnosis,
            next_step_rationale=decision.next_step_rationale,
            prompt_version=getattr(self._critic, "prompt_version", "unknown"),
            provider=getattr(self._critic, "provider", None),
            model=getattr(self._critic, "model", None),
            research_scope_snapshot=context.research_scope_snapshot,
        )
        invocation = PostVerdictCriticInvocation(
            id=invocation.id,
            scientific_verdict_id=invocation.scientific_verdict_id,
            context_version=invocation.context_version,
            prompt_version=invocation.prompt_version,
            provider=invocation.provider,
            model=invocation.model,
            context_snapshot_json=invocation.context_snapshot_json,
            raw_response=invocation.raw_response,
            parsed_decision_json=invocation.parsed_decision_json,
            validation_status=invocation.validation_status,
            validation_errors_json=invocation.validation_errors_json,
            resulting_intent_id=intent.id,
            created_at=invocation.created_at,
        )
        self._store.save_post_verdict_bundle(invocation=invocation, intent=intent)
        return GovernedPostVerdictResearchCriticResult(
            invocation=invocation,
            decision=decision,
            intent=intent,
            reused_existing=False,
        )


def _build_context(chain: dict[str, Any]) -> PostVerdictCriticContext:
    hypothesis_invocation = chain["hypothesis_invocation"]
    brief_snapshot = _loads_object(hypothesis_invocation.research_brief_snapshot, "HypothesisScientistInvocation")
    scope_payload = brief_snapshot.get("research_scope")
    if not isinstance(scope_payload, dict):
        raise ValueError("Post-verdict Critic requires an authoritative ResearchScope snapshot")
    scope = ResearchScope.create(**scope_payload)
    return PostVerdictCriticContext(
        id=new_id(),
        scientific_verdict_id=chain["scientific_verdict"].id,
        research_brief_id=hypothesis_invocation.research_brief_id,
        research_scope_snapshot=scope.to_payload(),
        research_brief_snapshot=brief_snapshot,
        candidate_snapshot=candidate_to_payload(chain["candidate"]),
        candidate_feasibility_snapshot=(
            None
            if chain["candidate_feasibility"] is None
            else _candidate_feasibility_to_payload(chain["candidate_feasibility"])
        ),
        hypothesis_claim_set_snapshot=hypothesis_claim_set_to_payload(chain["claim_set"]) or {},
        research_design_intent_snapshot=_json_ready(chain["design_intent"]),
        research_prediction_plan_snapshot=_json_ready(chain["prediction_plan"]),
        initial_experiment_plan_snapshot=_json_ready(chain["plan"]),
        contrast_result_snapshot=_json_ready(chain["contrast_result"]),
        scientific_verdict_snapshot=_json_ready(chain["scientific_verdict"]),
    )


def _load_validated_chain(store, scientific_verdict_id: str) -> dict[str, Any]:
    scientific_verdict = _require_present(
        store.get_scientific_verdict(scientific_verdict_id),
        f"ScientificVerdict not found: {scientific_verdict_id!r}",
    )
    prediction_plan = _require_present(
        store.get_research_prediction_plan(scientific_verdict.prediction_plan_id),
        f"ResearchPredictionPlan not found: {scientific_verdict.prediction_plan_id!r}",
    )
    design_intent = _require_present(
        store.get_research_design_intent(scientific_verdict.design_intent_id),
        f"ResearchDesignIntent not found: {scientific_verdict.design_intent_id!r}",
    )
    plan = _require_present(
        store.get_initial_experiment_plan(scientific_verdict.experiment_plan_id),
        f"InitialExperimentPlan not found: {scientific_verdict.experiment_plan_id!r}",
    )
    contrast_result = _require_present(
        store.get_parameter_sensitivity_contrast_result_by_id(scientific_verdict.contrast_result_id),
        f"ParameterSensitivityContrastResult not found: {scientific_verdict.contrast_result_id!r}",
    )
    claim_set = _require_present(
        store.get_hypothesis_claim_set(prediction_plan.hypothesis_claim_set_id or ""),
        "ResearchPredictionPlan must carry an authoritative HypothesisClaimSet for the V0.16 path",
    )
    candidate = _require_present(
        store.get_research_candidate(prediction_plan.candidate_id),
        f"ResearchCandidate not found: {prediction_plan.candidate_id!r}",
    )
    hypothesis_invocation = _require_present(
        store.get_hypothesis_scientist_invocation(claim_set.hypothesis_scientist_invocation_id),
        (
            "HypothesisScientistInvocation not found for authoritative HypothesisClaimSet: "
            f"{claim_set.hypothesis_scientist_invocation_id!r}"
        ),
    )
    candidate_feasibility = store.get_feasibility_decision(plan.candidate_feasibility_decision_id)

    validate_prediction_plan_experiment_plan_contrast_linkage(
        prediction_plan=prediction_plan,
        experiment_plan=plan,
        contrast_result=contrast_result,
    )
    _validate_extended_chain(
        scientific_verdict=scientific_verdict,
        prediction_plan=prediction_plan,
        design_intent=design_intent,
        plan=plan,
        contrast_result=contrast_result,
        claim_set=claim_set,
        candidate=candidate,
        hypothesis_invocation=hypothesis_invocation,
    )

    return {
        "scientific_verdict": scientific_verdict,
        "prediction_plan": prediction_plan,
        "design_intent": design_intent,
        "plan": plan,
        "contrast_result": contrast_result,
        "claim_set": claim_set,
        "candidate": candidate,
        "hypothesis_invocation": hypothesis_invocation,
        "candidate_feasibility": candidate_feasibility,
    }


def _validate_extended_chain(
    *,
    scientific_verdict,
    prediction_plan,
    design_intent,
    plan,
    contrast_result,
    claim_set,
    candidate,
    hypothesis_invocation,
) -> None:
    if scientific_verdict.prediction_plan_id != prediction_plan.id:
        raise ValueError("ScientificVerdict does not belong to the requested ResearchPredictionPlan")
    if scientific_verdict.design_intent_id != design_intent.id:
        raise ValueError("ScientificVerdict does not belong to the requested ResearchDesignIntent")
    if scientific_verdict.experiment_plan_id != plan.id:
        raise ValueError("ScientificVerdict does not belong to the requested InitialExperimentPlan")
    if scientific_verdict.contrast_result_id != contrast_result.id:
        raise ValueError("ScientificVerdict does not belong to the requested ContrastResult")
    if design_intent.id != prediction_plan.design_intent_id:
        raise ValueError("ResearchPredictionPlan does not belong to the requested ResearchDesignIntent")
    if design_intent.id != plan.design_intent_id:
        raise ValueError("InitialExperimentPlan does not belong to the requested ResearchDesignIntent")
    if claim_set.candidate_id != candidate.id:
        raise ValueError("HypothesisClaimSet does not belong to the authoritative ResearchCandidate")
    if prediction_plan.candidate_id != candidate.id:
        raise ValueError("ResearchPredictionPlan does not belong to the authoritative ResearchCandidate")
    if design_intent.candidate_id != candidate.id:
        raise ValueError("ResearchDesignIntent does not belong to the authoritative ResearchCandidate")
    if plan.candidate_id != candidate.id:
        raise ValueError("InitialExperimentPlan does not belong to the authoritative ResearchCandidate")
    brief_snapshot = _loads_object(hypothesis_invocation.research_brief_snapshot, "HypothesisScientistInvocation")
    scope_payload = brief_snapshot.get("research_scope")
    if not isinstance(scope_payload, dict):
        raise ValueError("Post-verdict Critic requires a frozen ResearchScope in the originating ResearchBrief")
    research_scope = ResearchScope.create(**scope_payload)
    claim_outcomes = {item.outcome for item in claim_set.claims}
    prediction_outcomes = {item.outcome for item in prediction_plan.predictions}
    contrast_outcomes = {item.outcome for item in contrast_result.outcomes}
    verdict_outcomes = {item.outcome for item in scientific_verdict.per_outcome_verdicts}
    scope_outcomes = set(research_scope.requested_outcomes)
    design_outcomes = set(design_intent.dependent_outcomes)
    plan_outcomes = set(plan.dependent_outcomes)
    if not (
        claim_outcomes
        == prediction_outcomes
        == contrast_outcomes
        == verdict_outcomes
        == scope_outcomes
        == design_outcomes
        == plan_outcomes
    ):
        raise ValueError("The authoritative post-verdict outcome chain is not internally coherent")
    if research_scope.independent_variable != claim_set.independent_variable:
        raise ValueError("ResearchScope independent variable does not match the authoritative HypothesisClaimSet")
    if research_scope.independent_variable != prediction_plan.independent_variable:
        raise ValueError("ResearchScope independent variable does not match the ResearchPredictionPlan")
    if research_scope.independent_variable != plan.independent_variable:
        raise ValueError("ResearchScope independent variable does not match the InitialExperimentPlan")
    if hypothesis_invocation.resulting_candidate_id != candidate.id:
        raise ValueError("HypothesisScientistInvocation does not belong to the authoritative ResearchCandidate")
    if hypothesis_invocation.resulting_claim_set_id != claim_set.id:
        raise ValueError("HypothesisScientistInvocation does not point to the authoritative HypothesisClaimSet")
    if brief_snapshot.get("id") != hypothesis_invocation.research_brief_id:
        raise ValueError("HypothesisScientistInvocation brief snapshot does not match research_brief_id")


def _candidate_feasibility_to_payload(feasibility) -> dict[str, Any]:
    return {
        "id": feasibility.id,
        "candidate_id": feasibility.candidate_id,
        "status": feasibility.gate_decision.value,
        "registry_version": feasibility.registry_version,
        "registry_fingerprint": feasibility.registry_fingerprint,
        "satisfied_ids": list(feasibility.satisfied_ids),
        "unsatisfied_ids": list(feasibility.unsatisfied_ids),
        "reason_codes": list(feasibility.reason_codes),
    }


def _research_scope_from_context(context: PostVerdictCriticContext) -> ResearchScope:
    return ResearchScope.create(**dict(context.research_scope_snapshot))


def _loads_object(payload_json: str, label: str) -> dict[str, Any]:
    payload = json.loads(payload_json)
    if not isinstance(payload, dict):
        raise ValueError(f"{label} snapshot must decode to an object")
    return payload


def _require_present(value, message: str):
    if value is None:
        raise KeyError(message)
    return value


def _json_ready(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_ready(item) for item in value]
    if is_dataclass(value):
        return {
            field_name: _json_ready(getattr(value, field_name))
            for field_name in value.__dataclass_fields__
        }
    return value
