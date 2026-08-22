"""Hypothesis Scientist evaluation harness."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from ..capabilities.models import DataRequirement, ToolRequirement
from ..capabilities.serialization import requirements_from_json
from ..models.hypothesis_scientist import ResearchBrief, HypothesisScientistDecisionType
from ..services.hypothesis_scientist import HypothesisProposalValidator


def _req_to_dict(req) -> dict:
    """Serialize a requirement to a type-tagged dict for the eval artifact."""
    if isinstance(req, DataRequirement):
        return {
            "type": "DataRequirement",
            "requirement_id": req.requirement_id,
            "data_kind": req.data_kind.value,
            "asset_class": req.asset_class.value if req.asset_class else None,
            "resolution": req.resolution.value if req.resolution else None,
            "required_fields": list(req.required_fields) if req.required_fields else None,
            "instruments": list(req.instruments) if req.instruments else None,
            "point_in_time_required": req.point_in_time_required,
            "required_parameters": list(req.required_parameters) if req.required_parameters else None,
            "label": req.label,
        }
    return {
        "type": "ToolRequirement",
        "requirement_id": req.requirement_id,
        "tool_kind": req.tool_kind.value if req.tool_kind is not None else None,
        "legacy_tool_name": req.legacy_tool_name,
        "label": req.label,
    }


def _serialise_decision_for_eval(decision) -> dict:
    """Full scientific decision serialization for manual grading."""
    reqs = []
    if decision.requirements_snapshot:
        try:
            for req in requirements_from_json(decision.requirements_snapshot):
                reqs.append(_req_to_dict(req))
        except Exception:
            pass
    compact_prov = None
    if decision.raw_response:
        try:
            compact_prov = json.loads(decision.raw_response)
        except Exception:
            compact_prov = None
    return {
        "hypothesis_statement": decision.hypothesis_statement,
        "hypothesis_rationale": decision.hypothesis_rationale,
        "requirements": reqs,
        "no_hypothesis_reason": decision.no_hypothesis_reason,
        "provider": decision.provider,
        "model": decision.model,
        "prompt_version": decision.prompt_version,
        "compact_provenance": compact_prov,
    }


@dataclass(frozen=True)
class ScientistEvalCase:
    id: str
    title: str
    description: str
    brief: ResearchBrief
    expected_behavior: str
    notes: str = ""
    eval_set_version: str = "v1"
    # Human-grading metadata — NOT sent to the model
    expected_decision: str | None = None
    evaluation_focus: str | None = None
    manual_success_criteria: str | None = None


@dataclass
class ScientistEvalResult:
    case_id: str
    scientist_name: str
    provider: str | None
    model: str | None
    prompt_version: str | None
    decision_type: str | None
    contract_passed: bool
    validation_errors: dict
    has_requirements: bool
    requirement_count: int
    hypothesis_statement: str | None
    # Full serialized decision for manual grading
    parsed_decision: dict | None = None
    # Eval fixture metadata (for artifact context; never in model input)
    expected_decision: str | None = None
    evaluation_focus: str | None = None
    notes: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


def load_cases_from_file(path: str) -> list[ScientistEvalCase]:
    with open(path) as f:
        payload = json.load(f)
    assert payload.get("version") == "v1"
    cases = []
    for c in payload.get("cases", []):
        brief_data = c["brief"]
        brief = ResearchBrief.create(
            research_question=brief_data["research_question"],
            asset_class_focus=brief_data.get("asset_class_focus"),
            instrument_focus=brief_data.get("instrument_focus"),
            methodological_constraints=brief_data.get("methodological_constraints"),
            exclusions=brief_data.get("exclusions"),
            prior_candidate_fingerprints=brief_data.get("prior_candidate_fingerprints"),
            prior_candidate_summaries=brief_data.get("prior_candidate_summaries"),
        )
        cases.append(ScientistEvalCase(
            id=c["id"],
            title=c["title"],
            description=c["description"],
            brief=brief,
            expected_behavior=c["expected_behavior"],
            notes=c.get("notes", ""),
            expected_decision=c.get("expected_decision"),
            evaluation_focus=c.get("evaluation_focus"),
            manual_success_criteria=c.get("manual_success_criteria"),
        ))
    return cases


class ScientistEvalSuite:
    def __init__(self, cases: list[ScientistEvalCase]) -> None:
        self.cases = cases

    def run(self, scientist, prompt_version: str | None = None) -> list[ScientistEvalResult]:
        results = []
        validator = HypothesisProposalValidator()
        effective_prompt_version = prompt_version or getattr(scientist, "prompt_version", None)
        for case in self.cases:
            try:
                decision = scientist.generate(case.brief)
                valid, errors = validator.validate(decision, case.brief)
                req_count = 0
                has_reqs = False
                if decision.requirements_snapshot:
                    try:
                        reqs = requirements_from_json(decision.requirements_snapshot)
                        req_count = len(reqs)
                        has_reqs = req_count > 0
                    except Exception:
                        pass
                results.append(ScientistEvalResult(
                    case_id=case.id,
                    scientist_name=type(scientist).__name__,
                    provider=getattr(scientist, "provider", None),
                    model=getattr(scientist, "model", None),
                    prompt_version=effective_prompt_version,
                    decision_type=decision.decision_type.value if decision.decision_type else None,
                    contract_passed=valid,
                    validation_errors=errors,
                    has_requirements=has_reqs,
                    requirement_count=req_count,
                    hypothesis_statement=decision.hypothesis_statement,
                    parsed_decision=_serialise_decision_for_eval(decision),
                    expected_decision=case.expected_decision,
                    evaluation_focus=case.evaluation_focus,
                ))
            except Exception as exc:
                results.append(ScientistEvalResult(
                    case_id=case.id,
                    scientist_name=type(scientist).__name__,
                    provider=getattr(scientist, "provider", None),
                    model=getattr(scientist, "model", None),
                    prompt_version=effective_prompt_version,
                    decision_type=None,
                    contract_passed=False,
                    validation_errors={"infrastructure_error": str(exc)},
                    has_requirements=False,
                    requirement_count=0,
                    hypothesis_statement=None,
                    parsed_decision=None,
                    expected_decision=case.expected_decision,
                    evaluation_focus=case.evaluation_focus,
                    notes=str(exc),
                ))
        return results
