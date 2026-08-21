"""AI Research Critic service and validator."""
from __future__ import annotations

from typing import Protocol
from dataclasses import asdict
from datetime import datetime

from ..models.critic import CriticContext, CriticDecision, CriticDecisionType, CriticInvocation
from ..models.enums import ResearchAction
from ..models.research import SpecRevisionProposal, new_id
from ..models.enums import SpecRevisionProposalStatus


class ResearchCritic(Protocol):
    def critique(self, context: CriticContext) -> CriticDecision:
        ...


class FakeResearchCritic:
    """Deterministic fake critic used for tests and local development.

    Behavior: if evaluation reason codes include MINIMUM_SHARPE_NOT_MET, propose
    lowering signal_threshold by 0.5 (if present). Otherwise, return
    NO_USEFUL_REVISION.
    """

    provider = "fake"
    model = "fake-v1"

    def critique(self, context: CriticContext) -> CriticDecision:
        eval_reasons = context.evaluation.get("reason_codes", [])
        current_params = context.current_spec.get("parameters", {})
        parent_id = context.current_spec.get("id")
        if "MINIMUM_SHARPE_NOT_MET" in eval_reasons and "signal_threshold" in current_params:
            old = float(current_params["signal_threshold"])
            new = round(old - 0.5, 3)
            decision = CriticDecision(
                id=new_id(),
                research_run_id=context.research_run_id,
                decision_type=CriticDecisionType.PROPOSE_REVISION,
                parent_spec_id=parent_id,
                changes={"signal_threshold": new},
                rationale="Lower threshold to improve trade count and allow more observations.",
                prediction="Trade count expected to increase; net_pnl direction unknown.",
                confidence="low",
                provider=self.provider,
                model=self.model,
                raw_response=None,
            )
            return decision
        return CriticDecision(
            id=new_id(),
            research_run_id=context.research_run_id,
            decision_type=CriticDecisionType.NO_USEFUL_REVISION,
            parent_spec_id=parent_id,
            changes=None,
            rationale="No bounded single-parameter revision appears justified.",
            prediction=None,
            confidence=None,
            provider=self.provider,
            model=self.model,
            raw_response=None,
        )


class CriticProposalValidator:
    """Deterministic validator for AI critic proposals."""

    def __init__(self, constraints: dict[str, dict]):
        self.constraints = constraints

    def validate(self, context: CriticContext, decision: CriticDecision) -> tuple[bool, dict]:
        errors: dict = {}
        if decision.decision_type == CriticDecisionType.NO_USEFUL_REVISION:
            return True, {}
        if decision.decision_type != CriticDecisionType.PROPOSE_REVISION:
            errors["decision_type"] = "unsupported"
            return False, errors
        if decision.parent_spec_id != context.current_spec.get("id"):
            errors["parent_spec_id"] = "must_reference_active_spec"
        changes = decision.changes or {}
        if len(changes.keys()) == 0:
            errors["changes"] = "no changes"
        if len(changes.keys()) > 1:
            errors["changes"] = "only one change allowed"
        for key, val in changes.items():
            if key not in self.constraints:
                errors["param_allowed"] = f"{key} not allowed"
                continue
            c = self.constraints[key]
            t = c.get("type")
            if t == "float":
                try:
                    v = float(val)
                except Exception:
                    errors[f"{key}_type"] = "must_be_float"
                    continue
                if "min" in c and v < c["min"]:
                    errors[f"{key}_min"] = f"{v} < {c['min']}"
                if "max" in c and v > c["max"]:
                    errors[f"{key}_max"] = f"{v} > {c['max']}"
            if t == "int":
                try:
                    v = int(val)
                except Exception:
                    errors[f"{key}_type"] = "must_be_int"
                    continue
                if "min" in c and v < c["min"]:
                    errors[f"{key}_min"] = f"{v} < {c['min']}"
                if "max" in c and v > c["max"]:
                    errors[f"{key}_max"] = f"{v} > {c['max']}"
            # ensure value differs
            current = context.current_spec.get("parameters", {}).get(key)
            if current == val:
                errors[f"{key}_same"] = "value same as current"
        valid = len(errors) == 0
        return valid, errors


def build_default_constraints() -> dict:
    # V1 planning grid: step sizes define the smallest informative perturbation
    return {
        "signal_threshold": {"type": "float", "min": -10.0, "max": 10.0, "step": 0.5},
        "lookback": {"type": "int", "min": 1, "max": 365, "step": 5},
    }


def run_critic_for_run(*, store, critic: ResearchCritic, run_id: str, prompt_version: str = "v1") -> tuple[CriticInvocation, CriticDecision, dict]:
    """Build context, run critic, validate, persist invocation and any valid proposal.

    Returns (invocation, decision, validation_result)
    """
    # build context from store
    run = store.get_research_run(run_id)
    if run is None:
        raise KeyError("Unknown run")
    # require the run to be in a revision-required state and have iteration budget
    if run.next_required_action != ResearchAction.REVISION_REQUIRED:
        raise RuntimeError("Critic may only run when a revision is required")
    if run.iteration_count >= run.max_iterations:
        raise RuntimeError("Critic may not run when iteration budget is exhausted")

    latest_eval = store.get_latest_evaluation_decision(run_id)
    if latest_eval is None or latest_eval.recommendation.name != "ITERATE":
        raise RuntimeError("Critic may only run when latest evaluation is ITERATE")

    # current spec
    spec = store.get_spec(run.active_spec_id)
    attempts = store.get_attempts(run_id)
    # attempt = last attempt
    attempt = attempts[-1] if attempts else None
    result = store.get_result_for_attempt(attempt.id) if attempt else None

    # prior lineage
    lineage = []
    # collect all specs by scanning research_specs is heavy; use attempts' spec ids
    for a in attempts:
        s = store.get_spec(a.spec_id)
        if s is not None:
            lineage.append({"id": s.id, "version": s.version, "parameters": s.parameters})

    context = CriticContext(
        id=new_id(),
        research_run_id=run_id,
        hypothesis={"id": run.hypothesis_id, "statement": store.get_hypothesis(run.hypothesis_id).statement, "rationale": store.get_hypothesis(run.hypothesis_id).rationale},
        current_spec={"id": spec.id, "version": spec.version, "parameters": spec.parameters},
        attempt={"id": attempt.id, "attempt_number": attempt.attempt_number} if attempt else {},
        result={"id": result.id, "metrics": result.metrics, "summary": result.summary} if result else {},
        evaluation={"id": latest_eval.id, "recommendation": latest_eval.recommendation.value, "reason_codes": list(latest_eval.reason_codes), "policy_snapshot": latest_eval.policy_snapshot, "summary": latest_eval.summary},
        prior_lineage=lineage,
        allowed_revision_constraints=build_default_constraints(),
    )

    # run critic
    decision = critic.critique(context)

    def _serialize(obj):
        if isinstance(obj, dict):
            return {k: _serialize(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [_serialize(x) for x in obj]
        if isinstance(obj, datetime):
            return obj.isoformat()
        return obj

    # persist invocation
    inv = CriticInvocation(
        id=new_id(),
        research_run_id=run_id,
        evaluation_id=latest_eval.id,
        parent_spec_id=spec.id,
        context_version=context.context_version,
        prompt_version=prompt_version,
        provider=getattr(critic, "provider", None),
        model=getattr(critic, "model", None),
        context_snapshot=_serialize(asdict(context)),
        raw_response=decision.raw_response,
        parsed_decision=_serialize(asdict(decision)),
        validation_status=None,
        validation_errors=None,
        resulting_proposal_id=None,
    )
    store.save_critic_invocation(inv)

    # validate
    validator = CriticProposalValidator(build_default_constraints())
    valid, errors = validator.validate(context, decision)
    # update invocation with validation
    inv = CriticInvocation(
        id=inv.id,
        research_run_id=inv.research_run_id,
        evaluation_id=inv.evaluation_id,
        parent_spec_id=inv.parent_spec_id,
        context_version=inv.context_version,
        prompt_version=inv.prompt_version,
        provider=inv.provider,
        model=inv.model,
        context_snapshot=inv.context_snapshot,
        raw_response=inv.raw_response,
        parsed_decision=inv.parsed_decision,
        validation_status=("VALID" if valid else "INVALID"),
        validation_errors=errors if errors else None,
        resulting_proposal_id=None,
        created_at=inv.created_at,
        completed_at=datetime.now(),
    )
    store.save_critic_invocation(inv)

    resulting_proposal = None
    if valid and decision.decision_type == CriticDecisionType.PROPOSE_REVISION:
        # create a SpecRevisionProposal but do not accept it
        proposal = SpecRevisionProposal(
            id=new_id(),
            research_run_id=run_id,
            parent_spec_id=decision.parent_spec_id,
            trigger_evaluation_id=latest_eval.id,
            proposed_parameters=decision.changes or {},
            change_summary=decision.rationale or "AI suggested revision",
            reason=decision.rationale or "ai",
            change_record={k: {"before": spec.parameters.get(k), "after": v} for k, v in (decision.changes or {}).items()},
            status=SpecRevisionProposalStatus.PROPOSED,
        )
        store.create_spec_revision_proposal(proposal)
        resulting_proposal = proposal
        # update invocation with resulting proposal
        inv = CriticInvocation(
            id=inv.id,
            research_run_id=inv.research_run_id,
            evaluation_id=inv.evaluation_id,
            parent_spec_id=inv.parent_spec_id,
            context_version=inv.context_version,
            prompt_version=inv.prompt_version,
            provider=inv.provider,
            model=inv.model,
            context_snapshot=inv.context_snapshot,
            raw_response=inv.raw_response,
            parsed_decision=inv.parsed_decision,
            validation_status=inv.validation_status,
            validation_errors=inv.validation_errors,
            resulting_proposal_id=proposal.id,
            created_at=inv.created_at,
            completed_at=datetime.now(),
        )
        store.save_critic_invocation(inv)

    return inv, decision, {"valid": valid, "errors": errors, "proposal": (resulting_proposal.id if resulting_proposal else None)}