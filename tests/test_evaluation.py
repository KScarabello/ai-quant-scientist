from __future__ import annotations

from datetime import datetime, timezone

from ai_quant_scientist.evaluation.result_evaluator import ResultEvaluator
from ai_quant_scientist.models.evaluation import EvaluationRecommendation, ResultEvaluationPolicy
from ai_quant_scientist.models.enums import ResearchStage, RunStatus
from ai_quant_scientist.models.research import ExperimentResult, ResearchAttempt, ResearchRun, new_id


def make_context(metrics: dict[str, object]) -> tuple[ResearchRun, ResearchAttempt, ExperimentResult]:
    now = datetime.now(timezone.utc)
    run = ResearchRun(
        id=new_id(),
        stage=ResearchStage.DISCOVERY,
        status=RunStatus.ACTIVE,
        hypothesis_id="hyp-1",
        active_spec_id="spec-1",
        iteration_count=0,
        max_iterations=3,
        created_at=now,
        updated_at=now,
    )
    attempt = ResearchAttempt(
        id=new_id(),
        research_run_id=run.id,
        spec_id="spec-1",
        attempt_number=1,
        stage=ResearchStage.DISCOVERY,
        started_at=now,
        completed_at=now,
        status="COMPLETED",
    )
    result = ExperimentResult(
        id=new_id(),
        attempt_id=attempt.id,
        tool_name="stub_backtester",
        metrics=metrics,
        summary="summary",
        passed=True,
        created_at=now,
    )
    return run, attempt, result


def test_evaluator_is_deterministic() -> None:
    evaluator = ResultEvaluator()
    policy = ResultEvaluationPolicy()
    run, attempt, result = make_context({"trade_count": 4, "sharpe": 1.0, "net_pnl": 10.0})

    decision_one = evaluator.evaluate(run=run, attempt=attempt, result=result, policy=policy)
    decision_two = evaluator.evaluate(run=run, attempt=attempt, result=result, policy=policy)

    assert decision_one.recommendation == decision_two.recommendation
    assert decision_one.reason_codes == decision_two.reason_codes
    assert decision_one.metrics_snapshot == decision_two.metrics_snapshot
    assert decision_one.policy_snapshot == decision_two.policy_snapshot


def test_promotion_boundary_conditions() -> None:
    evaluator = ResultEvaluator()
    policy = ResultEvaluationPolicy(minimum_trade_count=4, minimum_sharpe=1.0, minimum_net_pnl=10.0)
    run, attempt, result = make_context({"trade_count": 4, "sharpe": 1.0, "net_pnl": 10.0})

    decision = evaluator.evaluate(run=run, attempt=attempt, result=result, policy=policy)

    assert decision.recommendation == EvaluationRecommendation.PROMOTE
    assert "MINIMUM_TRADE_COUNT_MET" in decision.reason_codes
    assert "MINIMUM_SHARPE_MET" in decision.reason_codes
    assert "MINIMUM_NET_PNL_MET" in decision.reason_codes


def test_iteration_boundary_conditions() -> None:
    evaluator = ResultEvaluator()
    policy = ResultEvaluationPolicy(minimum_trade_count=4, minimum_sharpe=1.0, minimum_net_pnl=10.0)
    run, attempt, result = make_context({"trade_count": 4, "sharpe": 0.5, "net_pnl": 6.25})

    decision = evaluator.evaluate(run=run, attempt=attempt, result=result, policy=policy)

    assert decision.recommendation == EvaluationRecommendation.ITERATE
    assert "MINIMUM_TRADE_COUNT_MET" in decision.reason_codes
    assert "MINIMUM_SHARPE_NOT_MET" in decision.reason_codes
    assert "MINIMUM_NET_PNL_NOT_MET" in decision.reason_codes


def test_rejection_boundary_conditions() -> None:
    evaluator = ResultEvaluator()
    policy = ResultEvaluationPolicy()
    run, attempt, result = make_context({"trade_count": 4, "sharpe": -0.2, "net_pnl": -1.0})

    decision = evaluator.evaluate(run=run, attempt=attempt, result=result, policy=policy)

    assert decision.recommendation == EvaluationRecommendation.REJECT
    assert "NET_PNL_BELOW_ZERO_HARD_FAIL" in decision.reason_codes
