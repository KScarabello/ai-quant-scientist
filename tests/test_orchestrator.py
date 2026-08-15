from __future__ import annotations

import pytest

from ai_quant_scientist.evaluation.result_evaluator import ResultEvaluator
from ai_quant_scientist.models.evaluation import EvaluationRecommendation, ResultEvaluationPolicy
from ai_quant_scientist.models.enums import ResearchStage
from ai_quant_scientist.orchestrator.orchestrator import ResearchOrchestrator
from ai_quant_scientist.orchestrator.orchestrator import NoNextActionError
from ai_quant_scientist.policies.transitions import ResearchTransitionPolicy
from ai_quant_scientist.services.spec_builder import SpecBuilder
from ai_quant_scientist.storage.sqlite_store import SQLiteStore
from ai_quant_scientist.tools.stub_backtester import StubBacktester


def build_orchestrator(db_path):
    return ResearchOrchestrator(
        store=SQLiteStore(db_path),
        transition_policy=ResearchTransitionPolicy(),
        spec_builder=SpecBuilder(),
        research_tool=StubBacktester(),
        result_evaluator=ResultEvaluator(),
        evaluation_policy=ResultEvaluationPolicy(),
    )


def test_orchestrator_create_step_resume(tmp_path) -> None:
    db_path = tmp_path / "ai_quant_scientist.db"
    orchestrator = build_orchestrator(db_path)
    run = orchestrator.create_research(
        hypothesis_statement="Extreme displacement from intraday fair value may predict short-term mean reversion.",
        rationale="Manually supplied V0 hypothesis",
        parameters={"signal_threshold": 2.0, "lookback": 20},
        max_iterations=3,
    )

    assert run.stage == ResearchStage.IDEA

    run_after_first_step = orchestrator.run_next_step(run.id)
    assert run_after_first_step.stage == ResearchStage.DISCOVERY

    run_after_second_step = orchestrator.run_next_step(run.id)
    assert run_after_second_step.stage == ResearchStage.REPLICATION

    resumed_orchestrator = build_orchestrator(db_path)
    resumed_state = resumed_orchestrator.get_state(run.id)
    assert resumed_state == run_after_second_step

    attempts = resumed_orchestrator.store.get_attempts(run.id)
    assert len(attempts) == 1
    result = resumed_orchestrator.store.get_result_for_attempt(attempts[0].id)
    assert result is not None
    assert result.tool_name == "stub_backtester"

    decisions = resumed_orchestrator.store.list_evaluation_decisions(run.id)
    assert len(decisions) == 1
    assert decisions[0].recommendation == EvaluationRecommendation.PROMOTE
    assert "MINIMUM_TRADE_COUNT_MET" in decisions[0].reason_codes


def test_orchestrator_iteration_and_limit(tmp_path) -> None:
    db_path = tmp_path / "ai_quant_scientist.db"
    orchestrator = build_orchestrator(db_path)
    run = orchestrator.create_research(
        hypothesis_statement="Extreme displacement from intraday fair value may predict short-term mean reversion.",
        rationale="Manually supplied V0 hypothesis",
        parameters={"signal_threshold": 3.0, "lookback": 20},
        max_iterations=1,
    )

    orchestrator.run_next_step(run.id)
    result_state = orchestrator.run_next_step(run.id)
    assert result_state.stage == ResearchStage.REJECTED
    assert result_state.iteration_count == 1

    decisions = orchestrator.store.list_evaluation_decisions(run.id)
    assert len(decisions) == 1
    assert decisions[0].recommendation == EvaluationRecommendation.ITERATE

    with pytest.raises(NoNextActionError):
        orchestrator.run_next_step(run.id)
