"""Hypothesis Scientist evaluation harness."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from ..capabilities.serialization import requirements_from_json
from ..models.hypothesis_scientist import ResearchBrief, HypothesisScientistDecisionType
from ..services.hypothesis_scientist import HypothesisProposalValidator


@dataclass(frozen=True)
class ScientistEvalCase:
    id: str
    title: str
    description: str
    brief: ResearchBrief
    expected_behavior: str
    notes: str = ""
    eval_set_version: str = "v1"


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
        )
        cases.append(ScientistEvalCase(
            id=c["id"],
            title=c["title"],
            description=c["description"],
            brief=brief,
            expected_behavior=c["expected_behavior"],
            notes=c.get("notes", ""),
        ))
    return cases


class ScientistEvalSuite:
    def __init__(self, cases: list[ScientistEvalCase]) -> None:
        self.cases = cases

    def run(self, scientist, prompt_version: str = "v1") -> list[ScientistEvalResult]:
        results = []
        validator = HypothesisProposalValidator()
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
                    prompt_version=prompt_version,
                    decision_type=decision.decision_type.value if decision.decision_type else None,
                    contract_passed=valid,
                    validation_errors=errors,
                    has_requirements=has_reqs,
                    requirement_count=req_count,
                    hypothesis_statement=decision.hypothesis_statement,
                ))
            except Exception as exc:
                results.append(ScientistEvalResult(
                    case_id=case.id,
                    scientist_name=type(scientist).__name__,
                    provider=getattr(scientist, "provider", None),
                    model=getattr(scientist, "model", None),
                    prompt_version=prompt_version,
                    decision_type=None,
                    contract_passed=False,
                    validation_errors={"infrastructure_error": str(exc)},
                    has_requirements=False,
                    requirement_count=0,
                    hypothesis_statement=None,
                    notes=str(exc),
                ))
        return results
