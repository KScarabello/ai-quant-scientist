"""Bounded Research Designer evaluation harness."""

from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from ..capabilities.intake import GovernedResearchIntake
from ..capabilities.models import AssetClass, DataKind, DataRequirement, Resolution, ToolKind, ToolRequirement
from ..capabilities.gate import ResearchCandidate
from ..capabilities.serialization import requirements_from_json
from ..capabilities.v1_registry import build_v1_registry
from ..services.research_design_ontology import build_research_design_ontology_snapshot
from ..services.research_designer import GovernedResearchDesigner
from ..storage.sqlite_store import SQLiteStore


def _design_summary(parsed_decision: dict | None) -> dict | None:
    if parsed_decision is None or parsed_decision.get("decision_type") != "DESIGN":
        return None
    return {
        "design_kind": parsed_decision.get("design_kind"),
        "independent_variables": parsed_decision.get("independent_variables"),
        "dependent_outcomes": parsed_decision.get("dependent_outcomes"),
        "controls": parsed_decision.get("controls"),
        "comparison_intent": parsed_decision.get("comparison_intent"),
        "analysis_intent": parsed_decision.get("analysis_intent"),
    }


@dataclass(frozen=True)
class ResearchDesignerEvalCase:
    id: str
    title: str
    description: str
    candidate: ResearchCandidate
    expected_behavior: str
    notes: str = ""
    eval_set_version: str = "v1"
    expected_decision: str | None = None
    evaluation_focus: str | None = None
    manual_success_criteria: str | None = None


@dataclass
class ResearchDesignerEvalResult:
    case_id: str
    designer_name: str
    provider: str | None
    model: str | None
    prompt_version: str | None
    ontology_version: str | None
    ontology_fingerprint: str | None
    runner_outcome: str
    decision_type: str | None
    contract_passed: bool
    validation_errors: dict
    resulting_design_intent_id: str | None
    design_summary: dict | None
    parsed_decision: dict | None
    expected_decision: str | None = None
    evaluation_focus: str | None = None
    manual_success_criteria: str | None = None
    notes: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


def _parse_requirement(req: dict):
    if req["type"] == "DataRequirement":
        resolution_value = req.get("resolution")
        resolution = None
        if resolution_value is not None:
            resolution = next(item for item in Resolution if item.value == resolution_value)
        return DataRequirement(
            requirement_id=req["requirement_id"],
            data_kind=DataKind[req["data_kind"]],
            asset_class=AssetClass[req["asset_class"]] if req.get("asset_class") else None,
            resolution=resolution,
            required_fields=tuple(req["required_fields"]) if req.get("required_fields") else None,
            instruments=tuple(req["instruments"]) if req.get("instruments") else None,
            point_in_time_required=req.get("point_in_time_required", False),
            required_parameters=tuple(req["required_parameters"]) if req.get("required_parameters") else None,
        )
    return ToolRequirement(
        requirement_id=req["requirement_id"],
        tool_kind=ToolKind(req["tool_kind"]),
        label=req.get("label", ""),
    )


def load_cases_from_file(path: str) -> list[ResearchDesignerEvalCase]:
    with open(path, encoding="utf-8") as handle:
        payload = json.load(handle)
    assert payload.get("version") == "v1"
    cases: list[ResearchDesignerEvalCase] = []
    for item in payload.get("cases", []):
        candidate_payload = item["candidate"]
        candidate = ResearchCandidate.create(
            hypothesis_statement=candidate_payload["hypothesis_statement"],
            hypothesis_rationale=candidate_payload["hypothesis_rationale"],
            requirements=[_parse_requirement(req) for req in candidate_payload["requirements"]],
            source=candidate_payload.get("source", "eval_fixture"),
        )
        cases.append(
            ResearchDesignerEvalCase(
                id=item["id"],
                title=item["title"],
                description=item["description"],
                candidate=candidate,
                expected_behavior=item["expected_behavior"],
                notes=item.get("notes", ""),
                expected_decision=item.get("expected_decision"),
                evaluation_focus=item.get("evaluation_focus"),
                manual_success_criteria=item.get("manual_success_criteria"),
            )
        )
    return cases


class ResearchDesignerEvalSuite:
    def __init__(self, cases: list[ResearchDesignerEvalCase]) -> None:
        self.cases = cases

    def run(self, designer) -> list[ResearchDesignerEvalResult]:
        results: list[ResearchDesignerEvalResult] = []
        ontology = build_research_design_ontology_snapshot()
        for case in self.cases:
            with tempfile.TemporaryDirectory(prefix=f"aqs_research_designer_{case.id}_") as tmp_dir:
                store = SQLiteStore(Path(tmp_dir) / "eval.db")
                registry = build_v1_registry()
                intake = GovernedResearchIntake(store, registry)
                intake_result = intake.submit(case.candidate)
                latest = store.get_latest_feasibility_decision(case.candidate.id)
                assert latest is not None

                if intake_result.is_blocked:
                    results.append(
                        ResearchDesignerEvalResult(
                            case_id=case.id,
                            designer_name=type(designer).__name__,
                            provider=getattr(designer, "provider", None),
                            model=getattr(designer, "model", None),
                            prompt_version=getattr(designer, "prompt_version", None),
                            ontology_version=ontology.version,
                            ontology_fingerprint=ontology.fingerprint,
                            runner_outcome="BLOCKED_PRE_CALL",
                            decision_type=None,
                            contract_passed=False,
                            validation_errors={
                                "eligibility": "Candidate is BLOCKED_CAPABILITY and must not reach the provider"
                            },
                            resulting_design_intent_id=None,
                            design_summary=None,
                            parsed_decision=None,
                            expected_decision=case.expected_decision,
                            evaluation_focus=case.evaluation_focus,
                            manual_success_criteria=case.manual_success_criteria,
                            notes=case.notes,
                        )
                    )
                    continue

                governed = GovernedResearchDesigner(
                    store=store,
                    registry=registry,
                    designer=designer,
                    ontology=ontology,
                )
                try:
                    result = governed.generate_design_intent(
                        candidate_id=case.candidate.id,
                        candidate_feasibility_decision_id=latest.id,
                    )
                    parsed_decision = (
                        json.loads(result.invocation.parsed_decision_json)
                        if result.invocation.parsed_decision_json
                        else None
                    )
                    runner_outcome = (
                        result.decision.decision_type.value
                        if result.decision is not None and result.invocation.validation_status == "VALID"
                        else "INVALID"
                    )
                    results.append(
                        ResearchDesignerEvalResult(
                            case_id=case.id,
                            designer_name=type(designer).__name__,
                            provider=getattr(designer, "provider", None),
                            model=getattr(designer, "model", None),
                            prompt_version=getattr(designer, "prompt_version", None),
                            ontology_version=ontology.version,
                            ontology_fingerprint=ontology.fingerprint,
                            runner_outcome=runner_outcome,
                            decision_type=(
                                result.decision.decision_type.value
                                if result.decision is not None
                                else None
                            ),
                            contract_passed=result.invocation.validation_status == "VALID",
                            validation_errors=(
                                json.loads(result.invocation.validation_errors_json)
                                if result.invocation.validation_errors_json
                                else {}
                            ),
                            resulting_design_intent_id=result.invocation.resulting_design_intent_id,
                            design_summary=_design_summary(parsed_decision),
                            parsed_decision=parsed_decision,
                            expected_decision=case.expected_decision,
                            evaluation_focus=case.evaluation_focus,
                            manual_success_criteria=case.manual_success_criteria,
                            notes=case.notes,
                        )
                    )
                except Exception as exc:
                    results.append(
                        ResearchDesignerEvalResult(
                            case_id=case.id,
                            designer_name=type(designer).__name__,
                            provider=getattr(designer, "provider", None),
                            model=getattr(designer, "model", None),
                            prompt_version=getattr(designer, "prompt_version", None),
                            ontology_version=ontology.version,
                            ontology_fingerprint=ontology.fingerprint,
                            runner_outcome="ERROR",
                            decision_type=None,
                            contract_passed=False,
                            validation_errors={"infrastructure_error": str(exc)},
                            resulting_design_intent_id=None,
                            design_summary=None,
                            parsed_decision=None,
                            expected_decision=case.expected_decision,
                            evaluation_focus=case.evaluation_focus,
                            manual_success_criteria=case.manual_success_criteria,
                            notes=str(exc),
                        )
                    )
        return results
