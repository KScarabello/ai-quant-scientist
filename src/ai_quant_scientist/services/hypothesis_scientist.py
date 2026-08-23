"""Hypothesis Scientist interface, validator, materialization, and fake scientist.

Provider adapters (OpenAI, Ollama) live in separate modules.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Protocol

from ..capabilities.models import (
    AnyRequirement,
    DataRequirement,
    ToolKind,
    ToolRequirement,
    validate_required_field_names,
    validate_required_parameter_names,
)
from ..capabilities.serialization import requirements_from_json, requirements_to_json
from ..models.design import DesignOutcome, DesignVariable, ExpectedDirection, OutcomePrediction
from ..models.hypothesis_scientist import (
    HYPOTHESIS_CLAIM_SET_CONTRACT_VERSION,
    HypothesisScientistDecision,
    HypothesisClaimAggregation,
    HypothesisClaimSet,
    HypothesisScientistDecisionType,
    HypothesisScientistInvocation,
    PriorCandidateSummary,
    ResearchBrief,
)
from ..models.research import new_id
from .hypothesis_claim_ontology import build_hypothesis_claim_ontology_snapshot
from .scientist_requirement_ontology import build_requirement_ontology_snapshot


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


SCIENTIST_SOURCE = "hypothesis_scientist_v1"


# ─── Protocol ─────────────────────────────────────────────────────────────────

class HypothesisScientist(Protocol):
    provider: str
    model: str
    prompt_version: str

    def generate(self, brief: ResearchBrief) -> HypothesisScientistDecision:
        ...


# ─── Validator ────────────────────────────────────────────────────────────────

class HypothesisProposalValidator:
    """Deterministic structural validator. No LLM. Fail closed."""

    def validate(
        self,
        decision: HypothesisScientistDecision,
        brief: ResearchBrief,
    ) -> tuple[bool, dict[str, str]]:
        errors: dict[str, str] = {}

        if decision.decision_type == HypothesisScientistDecisionType.NO_HYPOTHESIS:
            if (
                decision.hypothesis_statement
                or decision.hypothesis_rationale
                or decision.requirements_snapshot
                or decision.independent_variable is not None
                or decision.independent_variable_direction is not None
                or decision.outcome_claims is not None
                or decision.claim_aggregation is not None
            ):
                errors["no_hypothesis_extra"] = "NO_HYPOTHESIS must not include candidate fields"
            if not decision.no_hypothesis_reason or not decision.no_hypothesis_reason.strip():
                errors["no_hypothesis_reason"] = "NO_HYPOTHESIS requires non-empty reason"
            return len(errors) == 0, errors

        if decision.decision_type != HypothesisScientistDecisionType.PROPOSE_HYPOTHESIS:
            errors["decision_type"] = f"Unknown decision type: {decision.decision_type!r}"
            return False, errors

        # PROPOSE_HYPOTHESIS checks
        if not decision.hypothesis_statement or not decision.hypothesis_statement.strip():
            errors["hypothesis_statement"] = "PROPOSE_HYPOTHESIS requires non-empty statement"
        if not decision.hypothesis_rationale or not decision.hypothesis_rationale.strip():
            errors["hypothesis_rationale"] = "PROPOSE_HYPOTHESIS requires non-empty rationale"

        if not decision.requirements_snapshot:
            errors["requirements"] = "PROPOSE_HYPOTHESIS requires non-empty requirements"
        else:
            try:
                reqs = requirements_from_json(decision.requirements_snapshot)
            except Exception as exc:
                errors["requirements_parse"] = f"Requirements JSON invalid: {exc}"
                reqs = ()
            if len(reqs) == 0:
                errors["requirements_empty"] = "PROPOSE_HYPOTHESIS requires at least one requirement"
            for req in reqs:
                if not isinstance(req, (DataRequirement, ToolRequirement)):
                    errors["requirements_type"] = f"Unknown requirement type: {type(req)}"
                    break
                if isinstance(req, DataRequirement):
                    try:
                        validate_required_field_names(req.data_kind, req.required_fields)
                    except ValueError as exc:
                        errors[f"{req.requirement_id}_required_fields"] = str(exc)
                    try:
                        validate_required_parameter_names(req.required_parameters)
                    except ValueError as exc:
                        errors[f"{req.requirement_id}_required_parameters"] = str(exc)
                if isinstance(req, ToolRequirement) and req.tool_kind is None:
                    errors[f"{req.requirement_id}_tool_kind"] = (
                        "PROPOSE_HYPOTHESIS requires canonical tool_kind; legacy tool names are not allowed"
                    )

        if decision.prompt_version == "v4" or any(
            value is not None
            for value in (
                decision.independent_variable,
                decision.independent_variable_direction,
                decision.outcome_claims,
                decision.claim_aggregation,
            )
        ):
            claim_ontology = build_hypothesis_claim_ontology_snapshot()
            if decision.independent_variable is None:
                errors["independent_variable"] = "PROPOSE_HYPOTHESIS requires authoritative independent_variable under V4"
            elif decision.independent_variable.value not in claim_ontology.supported_independent_variables:
                errors["independent_variable"] = "independent_variable is unsupported under the bounded claim ontology"
            if decision.independent_variable_direction is None:
                errors["independent_variable_direction"] = (
                    "PROPOSE_HYPOTHESIS requires authoritative independent_variable_direction under V4"
                )
            elif decision.independent_variable_direction not in (
                ExpectedDirection.INCREASE,
                ExpectedDirection.DECREASE,
            ):
                errors["independent_variable_direction"] = (
                    "independent_variable_direction must be INCREASE or DECREASE under the directional V0.15.1 contract"
                )
            claims = decision.outcome_claims or ()
            if not claims:
                errors["outcome_claims"] = "PROPOSE_HYPOTHESIS requires at least one authoritative directional outcome claim under V4"
            else:
                claim_outcomes: list[str] = []
                for item in claims:
                    if not isinstance(item, OutcomePrediction):
                        errors["outcome_claims"] = "outcome_claims must be structured outcome/direction pairs"
                        break
                    claim_outcomes.append(item.outcome.value)
                    if item.outcome.value not in claim_ontology.supported_outcomes:
                        errors["outcome_claims"] = (
                            f"Unsupported claim outcome under the bounded claim ontology: {item.outcome.value}"
                        )
                    if item.expected_direction not in (
                        ExpectedDirection.INCREASE,
                        ExpectedDirection.DECREASE,
                    ):
                        errors["outcome_claim_directions"] = (
                            "Outcome claims must be INCREASE or DECREASE under the directional V0.15.1 contract"
                        )
                if len(claim_outcomes) != len(set(claim_outcomes)):
                    errors["outcome_claim_duplicates"] = "Outcome claims must not repeat outcomes"
            if decision.claim_aggregation is None:
                errors["claim_aggregation"] = "PROPOSE_HYPOTHESIS requires claim_aggregation under V4"
            elif decision.claim_aggregation != HypothesisClaimAggregation.ALL_CLAIMS_REQUIRED:
                errors["claim_aggregation"] = "Unsupported claim_aggregation under the bounded V0.15.1 contract"

        # AI must not have supplied governance fields (enforced by schema, belt-and-suspenders)
        # The decision model does not even have id/source/created_at for the candidate

        return len(errors) == 0, errors


# ─── Materialization ──────────────────────────────────────────────────────────

def materialize_research_candidate(
    decision: HypothesisScientistDecision,
    brief: ResearchBrief,
):
    """Convert a validated PROPOSE_HYPOTHESIS decision to an authoritative ResearchCandidate.

    Deterministically assigns id, source, and created_at.
    The AI cannot influence these governance fields.
    The resulting candidate remains pre-spec and broad by design.
    """
    from ..capabilities.gate import ResearchCandidate

    requirements = requirements_from_json(decision.requirements_snapshot)
    return ResearchCandidate(
        id=new_id(),
        hypothesis_statement=decision.hypothesis_statement,
        hypothesis_rationale=decision.hypothesis_rationale,
        requirements=requirements,
        source=f"{SCIENTIST_SOURCE}:{decision.provider or 'unknown'}:{decision.model or 'unknown'}",
        created_at=utcnow(),
    )


def materialize_hypothesis_claim_set(
    decision: HypothesisScientistDecision,
    *,
    candidate_id: str,
    hypothesis_scientist_invocation_id: str,
) -> HypothesisClaimSet | None:
    if decision.decision_type != HypothesisScientistDecisionType.PROPOSE_HYPOTHESIS:
        raise ValueError("Only PROPOSE_HYPOTHESIS decisions can materialize a HypothesisClaimSet")
    if decision.independent_variable is None:
        return None
    if decision.independent_variable_direction is None:
        raise ValueError("HypothesisClaimSet requires independent_variable_direction")
    claims = decision.outcome_claims or ()
    if not claims:
        raise ValueError("HypothesisClaimSet requires at least one claim")
    claim_aggregation = decision.claim_aggregation
    if claim_aggregation is None:
        raise ValueError("HypothesisClaimSet requires claim_aggregation")
    ontology = build_hypothesis_claim_ontology_snapshot()
    return HypothesisClaimSet(
        id=new_id(),
        candidate_id=candidate_id,
        hypothesis_scientist_invocation_id=hypothesis_scientist_invocation_id,
        independent_variable=decision.independent_variable,
        independent_variable_direction=decision.independent_variable_direction,
        claims=claims,
        claim_aggregation=claim_aggregation,
        claim_contract_version=HYPOTHESIS_CLAIM_SET_CONTRACT_VERSION,
        ontology_version=ontology.version,
        ontology_fingerprint=ontology.fingerprint,
    )


# ─── Brief serialization ──────────────────────────────────────────────────────

def brief_to_json(brief: ResearchBrief) -> str:
    ontology = build_requirement_ontology_snapshot()
    claim_ontology = build_hypothesis_claim_ontology_snapshot()
    return json.dumps({
        "id": brief.id,
        "research_question": brief.research_question,
        "asset_class_focus": brief.asset_class_focus,
        "instrument_focus": list(brief.instrument_focus) if brief.instrument_focus else None,
        "methodological_constraints": list(brief.methodological_constraints) if brief.methodological_constraints else None,
        "exclusions": list(brief.exclusions) if brief.exclusions else None,
        "prior_candidate_fingerprints": list(brief.prior_candidate_fingerprints) if brief.prior_candidate_fingerprints else None,
        "prior_candidate_summaries": [
            {
                "fingerprint": s.fingerprint,
                "hypothesis_statement": s.hypothesis_statement,
                "hypothesis_rationale_summary": s.hypothesis_rationale_summary,
            }
            for s in (brief.prior_candidate_summaries or ())
        ] or None,
        "requirement_ontology": {
            "version": ontology.version,
            "fingerprint": ontology.fingerprint,
        },
        "hypothesis_claim_ontology": {
            "version": claim_ontology.version,
            "fingerprint": claim_ontology.fingerprint,
            "claim_contract_version": claim_ontology.claim_contract_version,
        },
        "source": brief.source,
        "created_at": brief.created_at.isoformat(),
    }, sort_keys=True)


def brief_to_payload(brief: ResearchBrief) -> dict:
    """Compact structured payload for the AI model input."""
    ontology = build_requirement_ontology_snapshot()
    claim_ontology = build_hypothesis_claim_ontology_snapshot()
    payload = {"research_question": brief.research_question}
    if brief.asset_class_focus:
        payload["asset_class_focus"] = brief.asset_class_focus
    if brief.instrument_focus:
        payload["instrument_focus"] = list(brief.instrument_focus)
    if brief.methodological_constraints:
        payload["methodological_constraints"] = list(brief.methodological_constraints)
    if brief.exclusions:
        payload["exclusions"] = list(brief.exclusions)
    if brief.prior_candidate_summaries:
        payload["prior_candidate_summaries"] = [
            {
                "fingerprint": s.fingerprint,
                "hypothesis_statement": s.hypothesis_statement,
                "hypothesis_rationale_summary": s.hypothesis_rationale_summary,
            }
            for s in brief.prior_candidate_summaries
        ]
    elif brief.prior_candidate_fingerprints:
        payload["prior_candidate_fingerprints"] = list(brief.prior_candidate_fingerprints)
    payload["requirement_ontology"] = ontology.to_payload()
    payload["hypothesis_claim_ontology"] = claim_ontology.to_payload()
    return payload


# ─── High-level workflow ──────────────────────────────────────────────────────

def generate_candidate(
    scientist: HypothesisScientist,
    brief: ResearchBrief,
    store,
) -> tuple[HypothesisScientistInvocation, "Any | None"]:
    """Generate a hypothesis, validate, persist invocation, materialize candidate.

    Returns (invocation, candidate_or_None).
    Fails closed: invalid AI output is recorded but no candidate is created.
    No automatic GovernedResearchIntake submission.
    """
    decision = scientist.generate(brief)

    validator = HypothesisProposalValidator()
    valid, errors = validator.validate(decision, brief)

    candidate = None
    claim_set = None
    candidate_id = None
    claim_set_id = None
    invocation_id = new_id()
    if valid and decision.decision_type == HypothesisScientistDecisionType.PROPOSE_HYPOTHESIS:
        candidate = materialize_research_candidate(decision, brief)
        candidate_id = candidate.id
        claim_set = materialize_hypothesis_claim_set(
            decision,
            candidate_id=candidate.id,
            hypothesis_scientist_invocation_id=invocation_id,
        )
        claim_set_id = None if claim_set is None else claim_set.id

    parsed_payload = {
        "decision_type": decision.decision_type.value,
        "hypothesis_statement": decision.hypothesis_statement,
        "hypothesis_rationale": decision.hypothesis_rationale,
        "requirements_snapshot": decision.requirements_snapshot,
        "no_hypothesis_reason": decision.no_hypothesis_reason,
        "ontology_version": decision.ontology_version,
        "ontology_fingerprint": decision.ontology_fingerprint,
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
    }
    if claim_set is not None:
        parsed_payload["claim_contract_version"] = claim_set.claim_contract_version
        parsed_payload["claim_ontology_version"] = claim_set.ontology_version
        parsed_payload["claim_ontology_fingerprint"] = claim_set.ontology_fingerprint

    inv = HypothesisScientistInvocation(
        id=invocation_id,
        research_brief_id=brief.id,
        research_brief_snapshot=brief_to_json(brief),
        prompt_version=scientist.prompt_version,
        provider=scientist.provider,
        model=scientist.model,
        raw_response=decision.raw_response,
        parsed_decision_json=json.dumps(parsed_payload, sort_keys=True),
        validation_status="VALID" if valid else "INVALID",
        validation_errors_json=json.dumps(errors) if errors else None,
        resulting_candidate_id=candidate_id,
        resulting_claim_set_id=claim_set_id,
    )
    store.save_governed_hypothesis_bundle(
        invocation=inv,
        candidate=candidate,
        claim_set=claim_set,
    )

    return inv, candidate


# ─── FakeHypothesisScientist ──────────────────────────────────────────────────

class FakeHypothesisScientist:
    """Deterministic fake scientist for tests and local development."""

    provider = "fake"
    model = "fake-v1"
    prompt_version = "v4"

    def generate(self, brief: ResearchBrief) -> HypothesisScientistDecision:
        from ..capabilities.models import DataKind, AssetClass, Resolution
        question = brief.research_question.lower()
        ontology = build_requirement_ontology_snapshot()

        if "underspecified" in question or "explore" in question or "general" in question:
            return HypothesisScientistDecision(
                id=new_id(),
                decision_type=HypothesisScientistDecisionType.NO_HYPOTHESIS,
                research_brief_id=brief.id,
                no_hypothesis_reason="Brief is too underspecified to generate a responsible hypothesis.",
                provider=self.provider,
                model=self.model,
                prompt_version=self.prompt_version,
                ontology_version=ontology.version,
                ontology_fingerprint=ontology.fingerprint,
            )

        if "changes sharpe" in question or "changes trade_count" in question:
            return HypothesisScientistDecision(
                id=new_id(),
                decision_type=HypothesisScientistDecisionType.NO_HYPOTHESIS,
                research_brief_id=brief.id,
                no_hypothesis_reason=(
                    "The bounded directional contract requires explicit defensible outcome directions "
                    "for every material supported claim."
                ),
                provider=self.provider,
                model=self.model,
                prompt_version=self.prompt_version,
                ontology_version=ontology.version,
                ontology_fingerprint=ontology.fingerprint,
            )

        reqs = [
            DataRequirement(
                requirement_id="data",
                data_kind=DataKind.SYNTHETIC_PARAMETRIC,
                asset_class=AssetClass.SYNTHETIC,
            ),
            ToolRequirement(
                requirement_id="tool",
                tool_kind=ToolKind.BACKTEST_EXECUTION,
            ),
        ]
        return HypothesisScientistDecision(
            id=new_id(),
            decision_type=HypothesisScientistDecisionType.PROPOSE_HYPOTHESIS,
            research_brief_id=brief.id,
            hypothesis_statement=(
                "For identical synthetic strategy logic, a stricter signal threshold should reduce "
                "trade frequency and improve risk-adjusted performance."
            ),
            hypothesis_rationale=(
                "A stricter threshold should filter weaker signal realizations, lowering trade frequency "
                "while concentrating exposure in stronger observations that can improve risk-adjusted "
                "performance under a bounded deterministic threshold-sensitivity contrast."
            ),
            requirements_snapshot=requirements_to_json(tuple(reqs)),
            independent_variable=DesignVariable.SIGNAL_THRESHOLD,
            independent_variable_direction=ExpectedDirection.INCREASE,
            outcome_claims=(
                OutcomePrediction(
                    outcome=DesignOutcome.TRADE_COUNT,
                    expected_direction=ExpectedDirection.DECREASE,
                ),
                OutcomePrediction(
                    outcome=DesignOutcome.SHARPE,
                    expected_direction=ExpectedDirection.INCREASE,
                ),
            ),
            claim_aggregation=HypothesisClaimAggregation.ALL_CLAIMS_REQUIRED,
            provider=self.provider,
            model=self.model,
            prompt_version=self.prompt_version,
            ontology_version=ontology.version,
            ontology_fingerprint=ontology.fingerprint,
        )
