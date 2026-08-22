"""Bounded Research Designer service, validator, and deterministic fake."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol

from ..capabilities.gate import GateDecision, ResearchCandidate
from ..capabilities.serialization import compute_candidate_fingerprint, requirements_to_json
from ..models.design import ResearchDesignIntent
from ..models.research import new_id
from ..models.research_designer import (
    RESEARCH_DESIGN_INTENT_CONTRACT_VERSION,
    ResearchDesignerContext,
    ResearchDesignerDecision,
    ResearchDesignerDecisionType,
    ResearchDesignerInvocation,
)
from .research_design_ontology import (
    ResearchDesignOntologySnapshot,
    build_research_design_ontology_snapshot,
)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


RESEARCH_DESIGNER_SOURCE = "research_designer_v1"
_PARAMETER_ASSIGNMENT_RE = re.compile(
    r"\b(signal_threshold|lookback)\b\s*(=|:)?\s*-?\d+(?:\.\d+)?",
    re.IGNORECASE,
)
_ORDERING_RE = re.compile(
    r"\b(baseline|comparator|condition\s*[12]|first condition|second condition)\b",
    re.IGNORECASE,
)


class ResearchDesigner(Protocol):
    provider: str
    model: str
    prompt_version: str

    def design(self, context: ResearchDesignerContext) -> ResearchDesignerDecision:
        ...


@dataclass(frozen=True, slots=True)
class GovernedResearchDesignerResult:
    invocation: ResearchDesignerInvocation
    decision: ResearchDesignerDecision | None
    design_intent: ResearchDesignIntent | None


def candidate_to_payload(candidate: ResearchCandidate) -> dict[str, Any]:
    return {
        "candidate_id": candidate.id,
        "hypothesis_statement": candidate.hypothesis_statement,
        "hypothesis_rationale": candidate.hypothesis_rationale,
        "requirements": json.loads(requirements_to_json(candidate.requirements)),
        "source": candidate.source,
        "created_at": candidate.created_at.isoformat(),
        "candidate_fingerprint": compute_candidate_fingerprint(
            candidate.hypothesis_statement,
            candidate.hypothesis_rationale,
            candidate.requirements,
        ),
    }


def candidate_to_json(candidate: ResearchCandidate) -> str:
    return json.dumps(candidate_to_payload(candidate), sort_keys=True)


def context_to_payload(
    context: ResearchDesignerContext,
) -> dict[str, Any]:
    candidate_payload = candidate_to_payload(context.candidate)
    return {
        "candidate_id": context.candidate_id,
        "hypothesis_statement": candidate_payload["hypothesis_statement"],
        "hypothesis_rationale": candidate_payload["hypothesis_rationale"],
        "candidate_requirements": candidate_payload["requirements"],
        "candidate_feasibility_authorization": {
            "id": context.candidate_feasibility_decision_id,
            "decision": GateDecision.READY_FOR_SPEC.value,
        },
        "research_design_ontology": context.design_ontology_payload,
        "intent_contract_version": context.intent_contract_version,
    }


def context_to_json(
    context: ResearchDesignerContext,
) -> str:
    return json.dumps(context_to_payload(context), sort_keys=True)


def build_research_designer_context(
    *,
    candidate: ResearchCandidate,
    candidate_feasibility_decision_id: str,
    ontology: ResearchDesignOntologySnapshot | None = None,
) -> ResearchDesignerContext:
    ontology = ontology or build_research_design_ontology_snapshot()
    return ResearchDesignerContext(
        candidate=candidate,
        candidate_feasibility_decision_id=candidate_feasibility_decision_id,
        design_ontology_version=ontology.version,
        design_ontology_fingerprint=ontology.fingerprint,
        design_ontology_payload_json=json.dumps(
            ontology.to_payload(),
            sort_keys=True,
            ensure_ascii=True,
            separators=(",", ":"),
        ),
        intent_contract_version=ontology.intent_contract_version,
    )


class ResearchDesignProposalValidator:
    """Deterministic structural validator for AI-authored design proposals."""

    def __init__(self, *, capability_id_tokens: tuple[str, ...] = ()) -> None:
        self._capability_id_tokens = tuple(sorted(set(capability_id_tokens)))

    def validate(
        self,
        decision: ResearchDesignerDecision,
        context: ResearchDesignerContext,
        ontology: ResearchDesignOntologySnapshot,
    ) -> tuple[bool, dict[str, str]]:
        errors: dict[str, str] = {}

        if decision.candidate_id != context.candidate_id:
            errors["candidate_id"] = "Decision candidate_id must match the authorized context"

        if decision.ontology_version != ontology.version:
            errors["ontology_version"] = "Decision ontology_version must match the supplied ontology"
        if decision.ontology_fingerprint != ontology.fingerprint:
            errors["ontology_fingerprint"] = "Decision ontology_fingerprint must match the supplied ontology"

        if decision.decision_type == ResearchDesignerDecisionType.NO_VALID_DESIGN:
            if self._design_fields_present(decision):
                errors["no_valid_design_extra"] = "NO_VALID_DESIGN must not include design fields"
            if not decision.no_valid_design_reason or not decision.no_valid_design_reason.strip():
                errors["no_valid_design_reason"] = "NO_VALID_DESIGN requires a non-empty reason"
            else:
                self._validate_freeform_text(
                    field_name="no_valid_design_reason",
                    text=decision.no_valid_design_reason,
                    errors=errors,
                )
            return len(errors) == 0, errors

        if decision.decision_type != ResearchDesignerDecisionType.DESIGN:
            errors["decision_type"] = f"Unsupported decision type: {decision.decision_type!r}"
            return False, errors

        if decision.design_kind is None:
            errors["design_kind"] = "DESIGN requires design_kind"
        elif not hasattr(decision.design_kind, "value") or decision.design_kind.value not in ontology.supported_design_kinds:
            errors["design_kind"] = "DESIGN uses unsupported design_kind"

        independent_variables = decision.independent_variables or ()
        if len(independent_variables) != 1:
            errors["independent_variables"] = "DESIGN requires exactly one independent variable"
        else:
            if not hasattr(independent_variables[0], "value") or independent_variables[0].value not in ontology.design_variables:
                errors["independent_variables"] = "Independent variable must use legal ontology enum values"
            elif decision.design_kind is not None and hasattr(decision.design_kind, "value"):
                allowed_independent_variables = set(
                    ontology.eligible_independent_variables_by_design_kind.get(decision.design_kind.value, ())
                )
                if independent_variables[0].value not in allowed_independent_variables:
                    errors["independent_variables"] = "Independent variable is unsupported for the bounded V1 design"

        controls = decision.controls or ()
        if not controls:
            errors["controls"] = "DESIGN requires controls"
        elif decision.design_kind is not None and hasattr(decision.design_kind, "value"):
            required_controls = tuple(
                ontology.required_controls_by_design_kind.get(decision.design_kind.value, ())
            )
            if any(not hasattr(item, "value") for item in controls):
                errors["controls"] = "Controls must use legal ontology enum values"
            elif tuple(item.value for item in controls) != required_controls:
                errors["controls"] = "Controls must match the bounded deterministic V1 contract exactly"

        if independent_variables and controls and independent_variables[0] in controls:
            errors["controls_overlap"] = "Controls must be separate from the independent variable"

        dependent_outcomes = decision.dependent_outcomes or ()
        if not dependent_outcomes:
            errors["dependent_outcomes"] = "DESIGN requires at least one dependent outcome"
        else:
            allowed_outcomes = set(ontology.supported_dependent_outcomes)
            if any(not hasattr(outcome, "value") for outcome in dependent_outcomes):
                errors["dependent_outcomes"] = "Dependent outcomes must use legal ontology enum values"
                allowed_outcomes = set()
            unsupported = sorted(
                outcome.value for outcome in dependent_outcomes if outcome.value not in allowed_outcomes
            )
            if unsupported:
                errors["dependent_outcomes"] = f"Unsupported dependent outcomes: {unsupported}"

        if decision.comparison_intent is None:
            errors["comparison_intent"] = "DESIGN requires comparison_intent"
        elif not hasattr(decision.comparison_intent, "value") or decision.comparison_intent.value not in ontology.comparison_intents:
            errors["comparison_intent"] = "Unsupported comparison_intent"

        if decision.analysis_intent is None:
            errors["analysis_intent"] = "DESIGN requires analysis_intent"
        elif not hasattr(decision.analysis_intent, "value") or decision.analysis_intent.value not in ontology.analysis_intents:
            errors["analysis_intent"] = "Unsupported analysis_intent"

        if not decision.rationale or not decision.rationale.strip():
            errors["rationale"] = "DESIGN requires non-empty rationale"
        else:
            self._validate_freeform_text("rationale", decision.rationale, errors)

        if not decision.falsification_condition or not decision.falsification_condition.strip():
            errors["falsification_condition"] = "DESIGN requires non-empty falsification_condition"
        else:
            self._validate_freeform_text(
                "falsification_condition",
                decision.falsification_condition,
                errors,
            )

        return len(errors) == 0, errors

    @staticmethod
    def _design_fields_present(decision: ResearchDesignerDecision) -> bool:
        return any(
            value is not None
            for value in (
                decision.design_kind,
                decision.independent_variables,
                decision.dependent_outcomes,
                decision.controls,
                decision.comparison_intent,
                decision.analysis_intent,
                decision.falsification_condition,
                decision.rationale,
            )
        )

    def _validate_freeform_text(self, field_name: str, text: str, errors: dict[str, str]) -> None:
        stripped = text.strip()
        if not stripped:
            errors[field_name] = f"{field_name} must be non-empty"
            return
        leaking_capability = next(
            (token for token in self._capability_id_tokens if token in stripped),
            None,
        )
        if leaking_capability is not None:
            errors[field_name] = f"{field_name} must not leak capability IDs"
            return
        if _PARAMETER_ASSIGNMENT_RE.search(stripped):
            errors[field_name] = f"{field_name} must not encode exact execution parameter values"
            return
        if _ORDERING_RE.search(stripped):
            errors[field_name] = f"{field_name} must not choose condition ordering or roles"


def materialize_research_design_intent(
    decision: ResearchDesignerDecision,
    *,
    candidate_id: str,
) -> ResearchDesignIntent:
    if decision.decision_type != ResearchDesignerDecisionType.DESIGN:
        raise ValueError("Only DESIGN decisions can materialize an authoritative ResearchDesignIntent")
    return ResearchDesignIntent.create(
        candidate_id=candidate_id,
        design_kind=decision.design_kind,
        independent_variables=decision.independent_variables or (),
        dependent_outcomes=decision.dependent_outcomes or (),
        controls=decision.controls or (),
        comparison_intent=decision.comparison_intent,
        analysis_intent=decision.analysis_intent,
        falsification_condition=decision.falsification_condition or "",
        rationale=decision.rationale or "",
        source=f"{RESEARCH_DESIGNER_SOURCE}:{decision.provider or 'unknown'}:{decision.model or 'unknown'}",
        provider=decision.provider,
        model=decision.model,
        prompt_version=decision.prompt_version,
        ontology_version=decision.ontology_version,
        ontology_fingerprint=decision.ontology_fingerprint,
    )


def _decision_to_json(decision: ResearchDesignerDecision | None) -> str | None:
    if decision is None:
        return None
    return json.dumps(
        {
            "decision_type": decision.decision_type.value,
            "design_kind": decision.design_kind.value if decision.design_kind else None,
            "independent_variables": (
                [item.value for item in decision.independent_variables]
                if decision.independent_variables is not None
                else None
            ),
            "dependent_outcomes": (
                [item.value for item in decision.dependent_outcomes]
                if decision.dependent_outcomes is not None
                else None
            ),
            "controls": (
                [item.value for item in decision.controls]
                if decision.controls is not None
                else None
            ),
            "comparison_intent": decision.comparison_intent.value if decision.comparison_intent else None,
            "analysis_intent": decision.analysis_intent.value if decision.analysis_intent else None,
            "falsification_condition": decision.falsification_condition,
            "rationale": decision.rationale,
            "no_valid_design_reason": decision.no_valid_design_reason,
            "provider": decision.provider,
            "model": decision.model,
            "prompt_version": decision.prompt_version,
            "ontology_version": decision.ontology_version,
            "ontology_fingerprint": decision.ontology_fingerprint,
        },
        sort_keys=True,
    )


class GovernedResearchDesigner:
    """Governed bounded AI step between candidate feasibility and deterministic materialization."""

    def __init__(
        self,
        *,
        store,
        registry,
        designer: ResearchDesigner,
        validator: ResearchDesignProposalValidator | None = None,
        ontology: ResearchDesignOntologySnapshot | None = None,
    ) -> None:
        self._store = store
        self._registry = registry
        self._designer = designer
        self._ontology = ontology or build_research_design_ontology_snapshot()
        self._validator = validator or ResearchDesignProposalValidator(
            capability_id_tokens=tuple(
                capability.capability_id for capability in registry.list_capabilities()
            )
        )

    def generate_design_intent(
        self,
        *,
        candidate_id: str,
        candidate_feasibility_decision_id: str,
    ) -> GovernedResearchDesignerResult:
        candidate = self._store.get_research_candidate(candidate_id)
        if candidate is None:
            raise KeyError(f"Research candidate not found: {candidate_id!r}")
        feasibility_decision = self._store.get_feasibility_decision(candidate_feasibility_decision_id)
        if feasibility_decision is None:
            raise KeyError(f"Feasibility decision not found: {candidate_feasibility_decision_id!r}")
        if feasibility_decision.candidate_id != candidate_id:
            raise RuntimeError("Feasibility decision does not belong to the requested candidate")
        if feasibility_decision.gate_decision != GateDecision.READY_FOR_SPEC:
            raise RuntimeError("Research Designer requires an explicit READY_FOR_SPEC authorization")

        context = build_research_designer_context(
            candidate=candidate,
            candidate_feasibility_decision_id=candidate_feasibility_decision_id,
            ontology=self._ontology,
        )

        try:
            decision = self._designer.design(context)
        except Exception as exc:
            invocation = ResearchDesignerInvocation(
                id=new_id(),
                candidate_id=candidate_id,
                candidate_snapshot_json=candidate_to_json(candidate),
                candidate_feasibility_decision_id=candidate_feasibility_decision_id,
                prompt_version=getattr(self._designer, "prompt_version", "unknown"),
                ontology_version=self._ontology.version,
                ontology_fingerprint=self._ontology.fingerprint,
                intent_contract_version=self._ontology.intent_contract_version,
                provider=getattr(self._designer, "provider", None),
                model=getattr(self._designer, "model", None),
                raw_response=None,
                parsed_decision_json=None,
                validation_status="ERROR",
                validation_errors_json=json.dumps({"infrastructure_error": str(exc)}, sort_keys=True),
                resulting_design_intent_id=None,
            )
            self._store.save_research_designer_invocation(invocation)
            raise

        valid, errors = self._validator.validate(decision, context, self._ontology)
        design_intent = None
        design_intent_id = None
        if valid and decision.decision_type == ResearchDesignerDecisionType.DESIGN:
            design_intent = materialize_research_design_intent(decision, candidate_id=candidate_id)
            design_intent_id = design_intent.id
            self._store.save_research_design_intent(design_intent)

        invocation = ResearchDesignerInvocation(
            id=new_id(),
            candidate_id=candidate_id,
            candidate_snapshot_json=candidate_to_json(candidate),
            candidate_feasibility_decision_id=candidate_feasibility_decision_id,
            prompt_version=self._designer.prompt_version,
            ontology_version=self._ontology.version,
            ontology_fingerprint=self._ontology.fingerprint,
            intent_contract_version=self._ontology.intent_contract_version,
            provider=getattr(self._designer, "provider", None),
            model=getattr(self._designer, "model", None),
            raw_response=decision.raw_response,
            parsed_decision_json=_decision_to_json(decision),
            validation_status="VALID" if valid else "INVALID",
            validation_errors_json=json.dumps(errors, sort_keys=True) if errors else None,
            resulting_design_intent_id=design_intent_id,
        )
        self._store.save_research_designer_invocation(invocation)
        return GovernedResearchDesignerResult(
            invocation=invocation,
            decision=decision,
            design_intent=design_intent,
        )


class FakeResearchDesigner:
    """Deterministic fake designer for tests and local eval harnesses."""

    provider = "fake"
    model = "fake-v1"
    prompt_version = "v1"

    def design(self, context: ResearchDesignerContext) -> ResearchDesignerDecision:
        from ..models.design import (
            AnalysisIntent,
            ComparisonIntent,
            DesignOutcome,
            DesignVariable,
            ResearchDesignKind,
        )

        text = (
            f"{context.candidate.hypothesis_statement} {context.candidate.hypothesis_rationale}"
        ).lower()

        if "underspecified" in text or "general explore" in text:
            return ResearchDesignerDecision(
                id=new_id(),
                candidate_id=context.candidate_id,
                decision_type=ResearchDesignerDecisionType.NO_VALID_DESIGN,
                no_valid_design_reason=(
                    "The candidate is too underspecified to express a bounded V1 design "
                    "without inventing unsupported scientific structure."
                ),
                provider=self.provider,
                model=self.model,
                prompt_version=self.prompt_version,
                ontology_version=context.design_ontology_version,
                ontology_fingerprint=context.design_ontology_fingerprint,
            )

        if (
            "lookback" in text
            and "signal threshold" not in text
            and "signal_threshold" not in text
            and "signal-threshold" not in text
        ):
            return ResearchDesignerDecision(
                id=new_id(),
                candidate_id=context.candidate_id,
                decision_type=ResearchDesignerDecisionType.NO_VALID_DESIGN,
                no_valid_design_reason=(
                    "The bounded V1 contract does not support lookback as the independent variable."
                ),
                provider=self.provider,
                model=self.model,
                prompt_version=self.prompt_version,
                ontology_version=context.design_ontology_version,
                ontology_fingerprint=context.design_ontology_fingerprint,
            )

        return ResearchDesignerDecision(
            id=new_id(),
            candidate_id=context.candidate_id,
            decision_type=ResearchDesignerDecisionType.DESIGN,
            design_kind=ResearchDesignKind.PARAMETER_SENSITIVITY,
            independent_variables=(DesignVariable.SIGNAL_THRESHOLD,),
            dependent_outcomes=(
                DesignOutcome.TRADE_COUNT,
                DesignOutcome.NET_PNL,
                DesignOutcome.SHARPE,
            ),
            controls=(DesignVariable.LOOKBACK,),
            comparison_intent=ComparisonIntent.CONTRAST_PARAMETER_LEVELS,
            analysis_intent=AnalysisIntent.SENSITIVITY_ANALYSIS,
            falsification_condition=(
                "If changing signal threshold does not change trade_count or risk-adjusted "
                "performance while lookback remains fixed, the hypothesis is weakened."
            ),
            rationale=(
                "Use a bounded parameter-sensitivity design that varies signal threshold while "
                "holding lookback fixed so deterministic software can later construct the exact comparison."
            ),
            provider=self.provider,
            model=self.model,
            prompt_version=self.prompt_version,
            ontology_version=context.design_ontology_version,
            ontology_fingerprint=context.design_ontology_fingerprint,
        )
