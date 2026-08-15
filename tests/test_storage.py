from __future__ import annotations

from datetime import datetime, timezone

from ai_quant_scientist.models.evaluation import EvaluationDecision, EvaluationRecommendation
from ai_quant_scientist.models.enums import ResearchStage, RunStatus
from ai_quant_scientist.models.research import AuditEvent, ExperimentResult, Hypothesis, ResearchAttempt, ResearchRun, ResearchSpec, new_id
from ai_quant_scientist.storage.sqlite_store import SQLiteStore


def test_database_initializes(tmp_path) -> None:
    db_path = tmp_path / "ai_quant_scientist.db"
    store = SQLiteStore(db_path)
    assert db_path.exists()
    assert store.get_research_run("missing") is None


def test_run_hypothesis_spec_result_and_audit_persist(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "ai_quant_scientist.db")
    now = datetime.now(timezone.utc)
    run = ResearchRun(
        id=new_id(),
        stage=ResearchStage.IDEA,
        status=RunStatus.ACTIVE,
        hypothesis_id="hyp-1",
        active_spec_id="spec-1",
        iteration_count=0,
        max_iterations=3,
        created_at=now,
        updated_at=now,
    )
    hypothesis = Hypothesis(id="hyp-1", research_run_id=run.id, statement="stmt", rationale="why", created_at=now)
    spec = ResearchSpec(id="spec-1", research_run_id=run.id, version=1, hypothesis_id=hypothesis.id, parameters={"signal_threshold": 2.0, "lookback": 20}, created_at=now, frozen_at=now, is_frozen=True)
    audit = AuditEvent(
        id=new_id(),
        research_run_id=run.id,
        event_type="RUN_CREATED",
        action="create_research",
        reason="created",
        state_before={},
        state_after={"id": run.id},
        metadata={"test": True},
        created_at=now,
    )
    store.create_research_bundle(run, hypothesis, spec, audit)

    stored_run = store.get_research_run(run.id)
    stored_hypothesis = store.get_hypothesis(hypothesis.id)
    stored_spec = store.get_spec(spec.id)
    assert stored_run == run
    assert stored_hypothesis == hypothesis
    assert stored_spec == spec

    attempt = ResearchAttempt(id=new_id(), research_run_id=run.id, spec_id=spec.id, attempt_number=1, stage=ResearchStage.DISCOVERY, started_at=now, completed_at=now, status="COMPLETED")
    result = ExperimentResult(id=new_id(), attempt_id=attempt.id, tool_name="stub_backtester", metrics={"net_pnl": 1.0}, summary="summary", passed=True, created_at=now)
    decision = EvaluationDecision(
        id=new_id(),
        research_run_id=run.id,
        attempt_id=attempt.id,
        result_id=result.id,
        stage=ResearchStage.DISCOVERY,
        recommendation=EvaluationRecommendation.ITERATE,
        reason_codes=("MINIMUM_TRADE_COUNT_MET", "MINIMUM_SHARPE_NOT_MET"),
        metrics_snapshot=result.metrics,
        policy_snapshot={"minimum_trade_count": 4, "minimum_sharpe": 1.0, "minimum_net_pnl": 10.0},
        summary="ITERATE",
        created_at=now,
    )
    later_audit = AuditEvent(
        id=new_id(),
        research_run_id=run.id,
        event_type="DISCOVERY_RESULT",
        action="run_stub_backtester",
        reason="done",
        state_before={"stage": "IDEA"},
        state_after={"stage": "DISCOVERY"},
        metadata={},
        created_at=now,
    )
    store.record_discovery_outcome(attempt, result, decision, later_audit, run)

    assert store.get_result_for_attempt(attempt.id) == result
    assert store.get_evaluation_decision(decision.id) == decision
    assert store.get_latest_evaluation_decision(run.id) == decision
    events = store.list_audit_events(run.id)
    assert [event.event_type for event in events] == ["RUN_CREATED", "DISCOVERY_RESULT"]


def test_policy_snapshot_is_preserved(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "ai_quant_scientist.db")
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
    store.save_run(run)

    hypothesis = Hypothesis(id="hyp-1", research_run_id=run.id, statement="stmt", rationale="why", created_at=now)
    spec = ResearchSpec(id="spec-1", research_run_id=run.id, version=1, hypothesis_id=hypothesis.id, parameters={"signal_threshold": 2.0, "lookback": 20}, created_at=now, frozen_at=now, is_frozen=True)
    store.save_hypothesis(hypothesis)
    store.save_spec(spec)

    attempt = ResearchAttempt(id=new_id(), research_run_id=run.id, spec_id=spec.id, attempt_number=1, stage=ResearchStage.DISCOVERY, started_at=now, completed_at=now, status="COMPLETED")
    result = ExperimentResult(id=new_id(), attempt_id=attempt.id, tool_name="stub_backtester", metrics={"trade_count": 4, "sharpe": 1.0, "net_pnl": 10.0}, summary="summary", passed=True, created_at=now)
    from ai_quant_scientist.models.evaluation import EvaluationDecision

    decision = EvaluationDecision(
        id=new_id(),
        research_run_id=run.id,
        attempt_id=attempt.id,
        result_id=result.id,
        stage=ResearchStage.DISCOVERY,
        recommendation=EvaluationRecommendation.PROMOTE,
        reason_codes=("MINIMUM_TRADE_COUNT_MET",),
        metrics_snapshot=result.metrics,
        policy_snapshot={"minimum_trade_count": 4, "minimum_sharpe": 1.0, "minimum_net_pnl": 10.0},
        summary="PROMOTE",
        created_at=now,
    )
    store.record_discovery_outcome(attempt, result, decision, AuditEvent(id=new_id(), research_run_id=run.id, event_type="RESULT_EVALUATED", action="run_stub_backtester", reason="ok", state_before={"stage": "DISCOVERY"}, state_after={"stage": "REPLICATION"}, metadata={}, created_at=now), run)

    with store.connect() as connection:
        connection.execute("UPDATE evaluation_decisions SET policy_snapshot_json = ? WHERE id = ?", ('{"minimum_trade_count":99}', decision.id))

    loaded = store.get_evaluation_decision(decision.id)
    assert loaded is not None
    assert loaded.policy_snapshot == {"minimum_trade_count": 99}
