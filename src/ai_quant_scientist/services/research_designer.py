"""Bounded Research Designer service, validator, and deterministic fake."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol

from ..capabilities.gate import GateDecision, ResearchCandidate
from ..capabilities.serialization import compute_candidate_fingerprint, requirements_to_json
from ..models.design import (
    AnalysisIntent,
    ComparisonIntent,
    DesignOutcome,
    DesignVariable,
    ExpectedDirection,
    OutcomePrediction,
    PredictionAggregationRule,
    ResearchDesignIntent,
    ResearchDesignKind,
    ResearchPredictionPlan,
)
from ..models.hypothesis_scientist import HypothesisClaimAggregation, HypothesisClaimSet
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
    RESEARCH_PREDICTION_PLAN_CONTRACT_VERSION,
    build_current_research_design_ontology_snapshot,
    build_research_design_ontology_snapshot,
)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


_RESEARCH_DESIGNER_SOURCES = {
    "v1": "research_designer_v1",
    "v2": "research_designer_v2",
    "v3": "research_designer_v3",
}
_PARAMETER_ASSIGNMENT_RE = re.compile(
    r"\b(signal_threshold|lookback)\b\s*(=|:)?\s*-?\d+(?:\.\d+)?",
    re.IGNORECASE,
)
_ORDERING_RE = re.compile(
    r"\b(baseline|comparator|condition\s*[12]|first condition|second condition)\b",
    re.IGNORECASE,
)
_RESULT_LEAKAGE_RE = re.compile(
    r"\b(observed|actual result|results showed|after seeing results|measured outcome)\b",
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
    prediction_plan: ResearchPredictionPlan | None


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


def hypothesis_claim_set_to_payload(claim_set: HypothesisClaimSet | None) -> dict[str, Any] | None:
    if claim_set is None:
        return None
    return {
        "hypothesis_claim_set_id": claim_set.id,
        "independent_variable": claim_set.independent_variable.value,
        "independent_variable_direction": claim_set.independent_variable_direction.value,
        "claim_aggregation": claim_set.claim_aggregation.value,
        "claim_contract_version": claim_set.claim_contract_version,
        "ontology_version": claim_set.ontology_version,
        "ontology_fingerprint": claim_set.ontology_fingerprint,
        "claims": [
            {
                "outcome": item.outcome.value,
                "expected_direction": item.expected_direction.value,
            }
            for item in claim_set.claims
        ],
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
        "hypothesis_claim_set": hypothesis_claim_set_to_payload(context.hypothesis_claim_set),
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
    hypothesis_claim_set: HypothesisClaimSet | None = None,
    candidate_feasibility_decision_id: str,
    ontology: ResearchDesignOntologySnapshot | None = None,
) -> ResearchDesignerContext:
    ontology = ontology or build_research_design_ontology_snapshot()
    return ResearchDesignerContext(
        candidate=candidate,
        hypothesis_claim_set=hypothesis_claim_set,
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

        if ontology.prediction_authority == "DETERMINISTIC_FROM_CLAIM_SET" and context.hypothesis_claim_set is None:
            errors["hypothesis_claim_set"] = (
                "The bounded V3 design contract requires an authoritative HypothesisClaimSet"
            )

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
            if len(set(dependent_outcomes)) != len(dependent_outcomes):
                errors["dependent_outcomes_duplicate"] = "Dependent outcomes must not repeat outcomes"
            allowed_outcomes = set(ontology.supported_dependent_outcomes)
            if any(not hasattr(outcome, "value") for outcome in dependent_outcomes):
                errors["dependent_outcomes"] = "Dependent outcomes must use legal ontology enum values"
                allowed_outcomes = set()
            unsupported = sorted(
                outcome.value for outcome in dependent_outcomes if outcome.value not in allowed_outcomes
            )
            if unsupported:
                errors["dependent_outcomes"] = f"Unsupported dependent outcomes: {unsupported}"

        predictions = decision.predictions or ()
        if ontology.prediction_authority == "DETERMINISTIC_FROM_CLAIM_SET":
            if predictions:
                errors["predictions"] = "Predictions are constructed deterministically from the authoritative claim set under V3"
        elif ontology.prediction_contract_version is None:
            if predictions:
                errors["predictions"] = "Predictions are unsupported under the supplied ontology"
        else:
            if not predictions:
                errors["predictions"] = (
                    "DESIGN requires exactly one directional prediction for every selected dependent outcome"
                )
            else:
                allowed_directions = set(ontology.supported_expected_directions or ())
                seen_prediction_outcomes: list[str] = []
                unsupported_prediction_outcomes: list[str] = []
                unsupported_directions: list[str] = []
                for item in predictions:
                    if not isinstance(item, OutcomePrediction):
                        errors["predictions"] = "Predictions must be structured outcome/direction pairs"
                        break
                    seen_prediction_outcomes.append(item.outcome.value)
                    if item.outcome.value not in allowed_outcomes:
                        unsupported_prediction_outcomes.append(item.outcome.value)
                    if item.expected_direction.value not in allowed_directions:
                        unsupported_directions.append(item.expected_direction.value)
                if len(seen_prediction_outcomes) != len(set(seen_prediction_outcomes)):
                    errors["predictions_duplicate"] = "Predictions must not repeat outcomes"
                if unsupported_prediction_outcomes:
                    errors["predictions_outcomes"] = (
                        f"Predictions must target only supported dependent outcomes: {sorted(set(unsupported_prediction_outcomes))}"
                    )
                if unsupported_directions:
                    errors["predictions_directions"] = (
                        f"Predictions use unsupported directions: {sorted(set(unsupported_directions))}"
                    )
                if dependent_outcomes:
                    missing = sorted(set(item.value for item in dependent_outcomes) - set(seen_prediction_outcomes))
                    extra = sorted(set(seen_prediction_outcomes) - set(item.value for item in dependent_outcomes))
                    if missing:
                        errors["predictions_missing"] = (
                            f"Predictions missing required dependent outcomes: {missing}"
                        )
                    if extra:
                        errors["predictions_extra"] = (
                            f"Predictions must not target unselected dependent outcomes: {extra}"
                        )

        if context.hypothesis_claim_set is not None:
            claim_set = context.hypothesis_claim_set
            if independent_variables:
                if independent_variables[0] != claim_set.independent_variable:
                    errors["claim_set_independent_variable"] = (
                        "DESIGN independent variable must match the authoritative HypothesisClaimSet"
                    )
            if dependent_outcomes:
                claim_outcomes = tuple(sorted((item.outcome for item in claim_set.claims), key=lambda item: item.value))
                decision_outcomes = tuple(sorted(dependent_outcomes, key=lambda item: item.value))
                if claim_outcomes != decision_outcomes:
                    missing = sorted(set(item.value for item in claim_outcomes) - set(item.value for item in decision_outcomes))
                    extra = sorted(set(item.value for item in decision_outcomes) - set(item.value for item in claim_outcomes))
                    if missing:
                        errors["claim_set_outcomes_missing"] = (
                            f"DESIGN must cover every authoritative claim outcome: missing {missing}"
                        )
                    if extra:
                        errors["claim_set_outcomes_extra"] = (
                            f"DESIGN must not add scientific outcomes outside the authoritative claim set: {extra}"
                        )

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
            return
        if _RESULT_LEAKAGE_RE.search(stripped):
            errors[field_name] = f"{field_name} must not use post-result language"


def materialize_research_design_intent(
    decision: ResearchDesignerDecision,
    *,
    candidate_id: str,
) -> ResearchDesignIntent:
    if decision.decision_type != ResearchDesignerDecisionType.DESIGN:
        raise ValueError("Only DESIGN decisions can materialize an authoritative ResearchDesignIntent")
    source_name = _RESEARCH_DESIGNER_SOURCES.get(
        decision.prompt_version or "",
        "research_designer_unknown",
    )
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
        source=f"{source_name}:{decision.provider or 'unknown'}:{decision.model or 'unknown'}",
        provider=decision.provider,
        model=decision.model,
        prompt_version=decision.prompt_version,
        ontology_version=decision.ontology_version,
        ontology_fingerprint=decision.ontology_fingerprint,
    )


def materialize_research_prediction_plan(
    claim_set: HypothesisClaimSet,
    design_intent: ResearchDesignIntent,
    *,
    research_designer_invocation_id: str,
    prediction_contract_version: str,
    ontology_version: str,
    ontology_fingerprint: str,
    include_claim_set_id: bool = True,
) -> ResearchPredictionPlan | None:
    claim_outcomes = tuple(sorted((item.outcome for item in claim_set.claims), key=lambda item: item.value))
    design_outcomes = tuple(sorted(design_intent.dependent_outcomes, key=lambda item: item.value))
    if claim_set.candidate_id != design_intent.candidate_id:
        raise ValueError("HypothesisClaimSet candidate_id must match the authoritative ResearchDesignIntent")
    if claim_set.independent_variable not in design_intent.independent_variables:
        raise ValueError("HypothesisClaimSet independent_variable must be covered by the authoritative ResearchDesignIntent")
    if claim_outcomes != design_outcomes:
        raise ValueError(
            "ResearchDesignIntent dependent_outcomes must match the authoritative HypothesisClaimSet outcomes exactly"
        )
    if claim_set.claim_aggregation != HypothesisClaimAggregation.ALL_CLAIMS_REQUIRED:
        raise ValueError("Unsupported HypothesisClaimSet claim_aggregation")
    return ResearchPredictionPlan(
        id=new_id(),
        candidate_id=claim_set.candidate_id,
        design_intent_id=design_intent.id,
        research_designer_invocation_id=research_designer_invocation_id,
        prediction_contract_version=prediction_contract_version,
        ontology_version=ontology_version,
        ontology_fingerprint=ontology_fingerprint,
        independent_variable=claim_set.independent_variable,
        predictions=claim_set.claims,
        hypothesis_claim_set_id=claim_set.id if include_claim_set_id else None,
        prediction_aggregation_rule=PredictionAggregationRule.ALL_PREDICTIONS_REQUIRED,
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
            "predictions": (
                [
                    {
                        "outcome": item.outcome.value,
                        "expected_direction": item.expected_direction.value,
                    }
                    for item in decision.predictions
                ]
                if decision.predictions is not None
                else None
            ),
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
        self._ontology = ontology or build_current_research_design_ontology_snapshot()
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
            hypothesis_claim_set=self._store.get_hypothesis_claim_set_by_candidate_id(candidate.id),
            candidate_feasibility_decision_id=candidate_feasibility_decision_id,
            ontology=self._ontology,
        )

        try:
            decision = self._designer.design(context)
        except Exception as exc:
            invocation = ResearchDesignerInvocation(
                id=new_id(),
                candidate_id=candidate_id,
                hypothesis_claim_set_id=context.hypothesis_claim_set_id,
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
        prediction_plan = None
        design_intent_id = None
        invocation_id = new_id()
        if valid and decision.decision_type == ResearchDesignerDecisionType.DESIGN:
            design_intent = materialize_research_design_intent(decision, candidate_id=candidate_id)
            design_intent_id = design_intent.id
            if self._ontology.prediction_contract_version is not None:
                prediction_contract_version = (
                    self._ontology.prediction_contract_version
                    or RESEARCH_PREDICTION_PLAN_CONTRACT_VERSION
                )
                if context.hypothesis_claim_set is not None:
                    prediction_plan = materialize_research_prediction_plan(
                        context.hypothesis_claim_set,
                        design_intent,
                        research_designer_invocation_id=invocation_id,
                        prediction_contract_version=prediction_contract_version,
                        ontology_version=self._ontology.version,
                        ontology_fingerprint=self._ontology.fingerprint,
                    )
                elif decision.predictions:
                    prediction_plan = materialize_research_prediction_plan(
                        HypothesisClaimSet(
                            id=new_id(),
                            candidate_id=candidate_id,
                            hypothesis_scientist_invocation_id="historical-v2",
                            independent_variable=(decision.independent_variables or ())[0],
                            independent_variable_direction=ExpectedDirection.INCREASE,
                            claims=decision.predictions or (),
                            claim_aggregation=HypothesisClaimAggregation.ALL_CLAIMS_REQUIRED,
                            claim_contract_version="historical_v2_prediction_bridge",
                            ontology_version=self._ontology.version,
                            ontology_fingerprint=self._ontology.fingerprint,
                        ),
                        design_intent,
                        research_designer_invocation_id=invocation_id,
                        prediction_contract_version=prediction_contract_version,
                        ontology_version=decision.ontology_version or "",
                        ontology_fingerprint=decision.ontology_fingerprint or "",
                        include_claim_set_id=False,
                    )

        invocation = ResearchDesignerInvocation(
            id=invocation_id,
            candidate_id=candidate_id,
            hypothesis_claim_set_id=context.hypothesis_claim_set_id,
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
        self._store.save_governed_research_design_bundle(
            invocation=invocation,
            design_intent=design_intent,
            prediction_plan=prediction_plan,
        )
        return GovernedResearchDesignerResult(
            invocation=invocation,
            decision=decision,
            design_intent=design_intent,
            prediction_plan=prediction_plan,
        )


class FakeResearchDesigner:
    """Deterministic fake designer for tests and local eval harnesses."""

    provider = "fake"
    model = "fake-v1"

    def __init__(self, prompt_version: str = "v2") -> None:
        self.prompt_version = prompt_version

    def design(self, context: ResearchDesignerContext) -> ResearchDesignerDecision:
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

        ontology_is_v2 = context.design_ontology_version == "research_design_ontology_v2"
        ontology_is_v3 = context.design_ontology_version == "research_design_ontology_v3"
        if ontology_is_v3:
            claim_set = context.hypothesis_claim_set
            if claim_set is None:
                return ResearchDesignerDecision(
                    id=new_id(),
                    candidate_id=context.candidate_id,
                    decision_type=ResearchDesignerDecisionType.NO_VALID_DESIGN,
                    no_valid_design_reason=(
                        "The bounded V3 contract requires an authoritative HypothesisClaimSet."
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
                independent_variables=(claim_set.independent_variable,),
                dependent_outcomes=tuple(item.outcome for item in claim_set.claims),
                controls=(DesignVariable.LOOKBACK,),
                comparison_intent=ComparisonIntent.CONTRAST_PARAMETER_LEVELS,
                analysis_intent=AnalysisIntent.SENSITIVITY_ANALYSIS,
                falsification_condition=(
                    "The hypothesis is falsified if tightening the signal threshold fails to produce "
                    "the complete claimed directional outcome pattern under fixed controls."
                ),
                rationale=(
                    "Translate the authoritative directional claim set into one bounded parameter-sensitivity "
                    "design that varies signal_threshold while holding lookback fixed."
                ),
                provider=self.provider,
                model=self.model,
                prompt_version=self.prompt_version,
                ontology_version=context.design_ontology_version,
                ontology_fingerprint=context.design_ontology_fingerprint,
            )
        if ontology_is_v2:
            trade_decrease = any(
                token in text
                for token in (
                    "lower trade frequency",
                    "reduce trade frequency",
                    "reduces trade frequency",
                    "fewer trades",
                    "decrease trade count",
                    "decreases trade count",
                )
            )
            sharpe_increase = any(
                token in text
                for token in (
                    "higher risk-adjusted performance",
                    "improve risk-adjusted performance",
                    "improves risk-adjusted performance",
                    "increase sharpe",
                    "increases sharpe",
                    "higher sharpe",
                )
            )
            if not (trade_decrease and sharpe_increase):
                return ResearchDesignerDecision(
                    id=new_id(),
                    candidate_id=context.candidate_id,
                    decision_type=ResearchDesignerDecisionType.NO_VALID_DESIGN,
                    no_valid_design_reason=(
                        "The bounded V2 contract requires explicit defensible directional predictions "
                        "for every selected dependent outcome."
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
                    DesignOutcome.SHARPE,
                    DesignOutcome.TRADE_COUNT,
                ),
                controls=(DesignVariable.LOOKBACK,),
                comparison_intent=ComparisonIntent.CONTRAST_PARAMETER_LEVELS,
                analysis_intent=AnalysisIntent.SENSITIVITY_ANALYSIS,
                predictions=(
                    OutcomePrediction(
                        outcome=DesignOutcome.TRADE_COUNT,
                        expected_direction=ExpectedDirection.DECREASE,
                    ),
                    OutcomePrediction(
                        outcome=DesignOutcome.SHARPE,
                        expected_direction=ExpectedDirection.INCREASE,
                    ),
                ),
                falsification_condition=(
                    "The hypothesis is falsified if a stricter signal threshold does not yield "
                    "lower trade frequency and higher risk-adjusted performance under fixed controls."
                ),
                rationale=(
                    "Use a bounded parameter-sensitivity design that varies signal threshold while "
                    "holding lookback fixed, and precommit directional expectations for trade_count "
                    "and sharpe before deterministic materialization."
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
