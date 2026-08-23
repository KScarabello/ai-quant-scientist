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
from ..models.hypothesis_scientist import (
    HypothesisScientistDecision,
    HypothesisScientistDecisionType,
    HypothesisScientistInvocation,
    PriorCandidateSummary,
    ResearchBrief,
)
from ..models.research import new_id
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
            if decision.hypothesis_statement or decision.hypothesis_rationale or decision.requirements_snapshot:
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


# ─── Brief serialization ──────────────────────────────────────────────────────

def brief_to_json(brief: ResearchBrief) -> str:
    ontology = build_requirement_ontology_snapshot()
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
        "source": brief.source,
        "created_at": brief.created_at.isoformat(),
    }, sort_keys=True)


def brief_to_payload(brief: ResearchBrief) -> dict:
    """Compact structured payload for the AI model input."""
    ontology = build_requirement_ontology_snapshot()
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
    candidate_id = None
    if valid and decision.decision_type == HypothesisScientistDecisionType.PROPOSE_HYPOTHESIS:
        candidate = materialize_research_candidate(decision, brief)
        candidate_id = candidate.id

    inv = HypothesisScientistInvocation(
        id=new_id(),
        research_brief_id=brief.id,
        research_brief_snapshot=brief_to_json(brief),
        prompt_version=scientist.prompt_version,
        provider=scientist.provider,
        model=scientist.model,
        raw_response=decision.raw_response,
        parsed_decision_json=json.dumps({
            "decision_type": decision.decision_type.value,
            "hypothesis_statement": decision.hypothesis_statement,
            "hypothesis_rationale": decision.hypothesis_rationale,
            "requirements_snapshot": decision.requirements_snapshot,
            "no_hypothesis_reason": decision.no_hypothesis_reason,
            "ontology_version": decision.ontology_version,
            "ontology_fingerprint": decision.ontology_fingerprint,
        }, sort_keys=True),
        validation_status="VALID" if valid else "INVALID",
        validation_errors_json=json.dumps(errors) if errors else None,
        resulting_candidate_id=candidate_id,
    )
    store.save_hypothesis_scientist_invocation(inv)

    return inv, candidate


# ─── FakeHypothesisScientist ──────────────────────────────────────────────────

class FakeHypothesisScientist:
    """Deterministic fake scientist for tests and local development."""

    provider = "fake"
    model = "fake-v1"
    prompt_version = "v3"

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
            provider=self.provider,
            model=self.model,
            prompt_version=self.prompt_version,
            ontology_version=ontology.version,
            ontology_fingerprint=ontology.fingerprint,
        )
