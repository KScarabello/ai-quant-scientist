"""Critic evaluation harness and result models."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from ..models.critic import CriticContext, CriticDecision, CriticDecisionType
from ..services.research_critic import CriticProposalValidator, build_default_constraints


def build_critic_context(case: "CriticEvalCase") -> CriticContext:
    """Canonical eval-case → CriticContext builder shared by all evaluation paths.

    Applies case.allowed_parameters (or default bounds) as allowed_revision_constraints
    so every eval path — deterministic suite, OpenAI live runner, Ollama live runner —
    constructs identical context objects.
    """
    ctx_dict = case.context
    return CriticContext(
        id=ctx_dict.get("id"),
        research_run_id=ctx_dict.get("research_run_id"),
        hypothesis=ctx_dict.get("hypothesis", {}),
        current_spec=ctx_dict.get("current_spec", {}),
        attempt=ctx_dict.get("attempt", {}),
        result=ctx_dict.get("result", {}),
        evaluation=ctx_dict.get("evaluation", {}),
        prior_lineage=ctx_dict.get("prior_lineage", []),
        allowed_revision_constraints=case.allowed_parameters or build_default_constraints(),
    )


@dataclass(frozen=True)
class CriticEvalCase:
    id: str
    title: str
    description: str
    context: Dict[str, Any]
    allowed_parameters: Optional[Dict[str, Any]] = None
    expected_behavior: Optional[Dict[str, Any]] = None
    notes: Optional[str] = None
    eval_set_version: str = "v1"


@dataclass
class CriticEvalResult:
    case_id: str
    critic_name: str
    provider: Optional[str]
    model: Optional[str]
    prompt_version: Optional[str]
    decision: Optional[CriticDecision]
    contract_passed: bool
    hard_failures: List[str]
    deterministic_scores: Dict[str, Optional[float]]
    manual_scores: Dict[str, Optional[float]]
    scientific_score_percent: Optional[float]
    latency_ms: Optional[int] = None
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    notes: Optional[str] = None
    created_at: datetime = field(default_factory=datetime.utcnow)


class CriticEvalSuite:
    def __init__(self, cases: List[CriticEvalCase]):
        self.cases = cases

    def run(self, critic, prompt_version: str = "v1") -> List[CriticEvalResult]:
        results: List[CriticEvalResult] = []
        for case in self.cases:
            try:
                res = self._run_case(case, critic, prompt_version)
            except Exception as e:
                # Infrastructure error: record as hard failure and continue
                res = CriticEvalResult(
                    case_id=case.id,
                    critic_name=getattr(critic, "__class__", type(critic)).__name__,
                    provider=getattr(critic, "provider", None),
                    model=getattr(critic, "model", None),
                    prompt_version=prompt_version,
                    decision=None,
                    contract_passed=False,
                    hard_failures=["INFRASTRUCTURE_ERROR"],
                    deterministic_scores={},
                    manual_scores={},
                    scientific_score_percent=None,
                    notes=str(e),
                )
            results.append(res)
        return results

    def _run_case(self, case: CriticEvalCase, critic, prompt_version: str) -> CriticEvalResult:
        # Build canonical CriticContext (constraints injected deterministically)
        ctx = build_critic_context(case)

        decision: CriticDecision = critic.critique(ctx)

        # Deterministic checks using existing validator
        validator = CriticProposalValidator(case.allowed_parameters or build_default_constraints())
        valid, errors = validator.validate(ctx, decision)

        hard_failures: List[str] = []
        if not valid:
            # map validator errors to hard failure codes
            for k in errors.keys():
                if k.endswith("param_allowed") or k.endswith("param_allowed"):
                    hard_failures.append("UNKNOWN_PARAMETER")
                elif k.endswith("only one change allowed") or k == "changes":
                    hard_failures.append("MULTIPLE_PARAMETER_CHANGES")
                elif "min" in k or "max" in k:
                    hard_failures.append("OUT_OF_RANGE")
                elif k.endswith("same"):
                    hard_failures.append("REPEATS_IDENTICAL_SPEC")
                else:
                    hard_failures.append(f"VALIDATION_{k}")

        # Additional deterministic checks
        # Wrong parent
        if decision.parent_spec_id is not None and decision.parent_spec_id != ctx.current_spec.get("id"):
            hard_failures.append("WRONG_PARENT")

        # Malformed decision
        if decision.decision_type not in (CriticDecisionType.PROPOSE_REVISION, CriticDecisionType.NO_USEFUL_REVISION):
            hard_failures.append("MALFORMED_DECISION")

        contract_passed = (len(hard_failures) == 0) and valid

        # Deterministic scoring dims
        deterministic_scores: Dict[str, Optional[float]] = {
            "contract_compliance": 1.0 if contract_passed else 0.0,
            "prediction_quality": 1.0 if getattr(decision, "prediction", None) else 0.0,
            "experiment_isolation": 1.0 if (decision.changes is None or len(decision.changes) <= 1) else 0.0,
            "lineage_conflict": 0.0,
        }

        # lineage_conflict: deterministic if decision repeats exact prior parameters
        if decision.changes:
            for prior in ctx.prior_lineage:
                prior_params = prior.get("parameters", {})
                for k, v in (decision.changes or {}).items():
                    if k in prior_params and prior_params[k] == v:
                        deterministic_scores["lineage_conflict"] = 1.0

        # scientific_score_percent from deterministic parts only
        available = [v for v in deterministic_scores.values() if v is not None]
        scientific_score_percent = None
        if available:
            scientific_score_percent = round(100.0 * sum(available) / (len(available) * 1.0), 2)

        result = CriticEvalResult(
            case_id=case.id,
            critic_name=getattr(critic, "__class__", type(critic)).__name__,
            provider=getattr(critic, "provider", None),
            model=getattr(critic, "model", None),
            prompt_version=prompt_version,
            decision=decision,
            contract_passed=contract_passed,
            hard_failures=hard_failures,
            deterministic_scores=deterministic_scores,
            manual_scores={},
            scientific_score_percent=scientific_score_percent,
        )
        return result


def load_cases_from_file(path: str) -> List[CriticEvalCase]:
    with open(path, "r") as f:
        payload = json.load(f)
    assert payload.get("version") == "v1"
    cases = []
    ids = set()
    for c in payload.get("cases", []):
        assert c["id"] not in ids, "duplicate case id"
        ids.add(c["id"])
        cases.append(CriticEvalCase(**c))
    return cases
