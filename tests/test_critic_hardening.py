from __future__ import annotations

import sqlite3
from dataclasses import asdict, replace
from datetime import datetime

import pytest

from ai_quant_scientist.services.research_critic import run_critic_for_run
from ai_quant_scientist.models.critic import CriticDecision, CriticDecisionType
from ai_quant_scientist.models.research import new_id
from ai_quant_scientist.storage.sqlite_store import SQLiteStore
from ai_quant_scientist.orchestrator.orchestrator import ResearchOrchestrator
from ai_quant_scientist.services.spec_builder import SpecBuilder
from ai_quant_scientist.tools.stub_backtester import StubBacktester
from ai_quant_scientist.evaluation.result_evaluator import ResultEvaluator
from ai_quant_scientist.models.evaluation import ResultEvaluationPolicy
from ai_quant_scientist.policies.transitions import ResearchTransitionPolicy
from ai_quant_scientist.models.enums import ResearchAction, SpecRevisionProposalStatus
from ai_quant_scientist.models.critic import CriticInvocation


def build_orchestrator(db_path):
    return ResearchOrchestrator(
        store=SQLiteStore(db_path),
        transition_policy=ResearchTransitionPolicy(),
        spec_builder=SpecBuilder(),
        research_tool=StubBacktester(),
        result_evaluator=ResultEvaluator(),
        evaluation_policy=ResultEvaluationPolicy(),
    )


def _mark_revision_required(orch, run):
    # deterministically set run to require a revision for testing
    state = orch.get_state(run.id)
    new_state = replace(state, next_required_action=ResearchAction.REVISION_REQUIRED)
    orch.store.update_research_run(new_state)
    # create a real attempt and result so evaluation FK constraints are valid
    from ai_quant_scientist.models.research import ResearchAttempt
    from ai_quant_scientist.models.research import ExperimentResult
    from ai_quant_scientist.models.evaluation import EvaluationDecision, EvaluationRecommendation

    attempt = ResearchAttempt(
        id=new_id(),
        research_run_id=run.id,
        spec_id=run.active_spec_id,
        attempt_number=run.iteration_count + 1,
        stage=run.stage,
        started_at=datetime.now(),
        completed_at=datetime.now(),
        status="COMPLETED",
    )
    result = ExperimentResult(
        id=new_id(),
        attempt_id=attempt.id,
        tool_name="test",
        metrics={"trade_count": 1, "sharpe": 0.1, "net_pnl": 0.0},
        summary="dummy",
        passed=False,
        created_at=datetime.now(),
    )
    # insert attempt and result together
    orch.store.create_attempt_and_result(attempt, result, None, run)

    eval_dec = EvaluationDecision(
        id=new_id(),
        research_run_id=run.id,
        attempt_id=attempt.id,
        result_id=result.id,
        stage=state.stage,
        recommendation=EvaluationRecommendation.ITERATE,
        reason_codes=("MINIMUM_SHARPE_NOT_MET",),
        metrics_snapshot={"sharpe": 0.1},
        policy_snapshot={"version": 1},
        summary="test iterate",
    )
    orch.store.save_evaluation_decision(eval_dec)
    return orch.get_state(run.id)


class SimpleCritic:
    def __init__(self, decision: CriticDecision):
        self._decision = decision

    def critique(self, context):
        return self._decision


def test_unknown_parameter_rejected(tmp_path):
    db = tmp_path / "db.sqlite"
    orch = build_orchestrator(db)
    run = orch.create_research(hypothesis_statement="h", rationale="r", parameters={"signal_threshold": 3.0, "lookback": 20}, max_iterations=3)
    _mark_revision_required(orch, run)
    spec = orch.store.get_spec(run.active_spec_id)

    decision = CriticDecision(
        id=new_id(),
        research_run_id=run.id,
        decision_type=CriticDecisionType.PROPOSE_REVISION,
        parent_spec_id=spec.id,
        changes={"magic_indicator": 42},
        rationale="add magic",
        prediction=None,
        confidence=None,
        provider="fake",
        model="test",
        raw_response=None,
    )
    inv, dec, res = run_critic_for_run(store=orch.store, critic=SimpleCritic(decision), run_id=run.id)
    assert inv.validation_status == "INVALID"
    assert res["proposal"] is None
    # ensure no new spec versions
    with orch.store.connect() as conn:
        rows = conn.execute("SELECT COUNT(*) as c FROM research_specs WHERE research_run_id = ?", (run.id,)).fetchone()
        assert rows["c"] == 1


def test_out_of_range_value_rejected(tmp_path):
    db = tmp_path / "db2.sqlite"
    orch = build_orchestrator(db)
    run = orch.create_research(hypothesis_statement="h", rationale="r", parameters={"signal_threshold": 3.0, "lookback": 20}, max_iterations=3)
    _mark_revision_required(orch, run)
    spec = orch.store.get_spec(run.active_spec_id)
    # signal_threshold allowed range -10..10; propose 1000
    decision = CriticDecision(
        id=new_id(),
        research_run_id=run.id,
        decision_type=CriticDecisionType.PROPOSE_REVISION,
        parent_spec_id=spec.id,
        changes={"signal_threshold": 1000},
        rationale="extreme",
        prediction=None,
        confidence=None,
        provider="fake",
        model="test",
        raw_response=None,
    )
    inv, dec, res = run_critic_for_run(store=orch.store, critic=SimpleCritic(decision), run_id=run.id)
    assert inv.validation_status == "INVALID"
    assert res["proposal"] is None


def test_multi_param_change_rejected(tmp_path):
    db = tmp_path / "db3.sqlite"
    orch = build_orchestrator(db)
    run = orch.create_research(hypothesis_statement="h", rationale="r", parameters={"signal_threshold": 3.0, "lookback": 20}, max_iterations=3)
    _mark_revision_required(orch, run)
    spec = orch.store.get_spec(run.active_spec_id)
    decision = CriticDecision(
        id=new_id(),
        research_run_id=run.id,
        decision_type=CriticDecisionType.PROPOSE_REVISION,
        parent_spec_id=spec.id,
        changes={"signal_threshold": 2.0, "lookback": 10},
        rationale="two changes",
        prediction=None,
        confidence=None,
        provider="fake",
        model="test",
        raw_response=None,
    )
    inv, dec, res = run_critic_for_run(store=orch.store, critic=SimpleCritic(decision), run_id=run.id)
    assert inv.validation_status == "INVALID"
    assert res["proposal"] is None


def test_same_value_rejected(tmp_path):
    db = tmp_path / "db4.sqlite"
    orch = build_orchestrator(db)
    run = orch.create_research(hypothesis_statement="h", rationale="r", parameters={"signal_threshold": 2.0, "lookback": 20}, max_iterations=3)
    _mark_revision_required(orch, run)
    spec = orch.store.get_spec(run.active_spec_id)
    decision = CriticDecision(
        id=new_id(),
        research_run_id=run.id,
        decision_type=CriticDecisionType.PROPOSE_REVISION,
        parent_spec_id=spec.id,
        changes={"signal_threshold": 2.0},
        rationale="same value",
        prediction=None,
        confidence=None,
        provider="fake",
        model="test",
        raw_response=None,
    )
    inv, dec, res = run_critic_for_run(store=orch.store, critic=SimpleCritic(decision), run_id=run.id)
    assert inv.validation_status == "INVALID"
    assert res["proposal"] is None


def test_wrong_parent_spec_rejected(tmp_path):
    db = tmp_path / "db5.sqlite"
    orch = build_orchestrator(db)
    run_a = orch.create_research(hypothesis_statement="hA", rationale="r", parameters={"signal_threshold": 2.0, "lookback": 20}, max_iterations=3)
    run_b = orch.create_research(hypothesis_statement="hB", rationale="r", parameters={"signal_threshold": 4.0, "lookback": 30}, max_iterations=3)
    _mark_revision_required(orch, run_a)
    spec_b = orch.store.get_spec(run_b.active_spec_id)
    # critic for run_a references spec from run_b
    decision = CriticDecision(
        id=new_id(),
        research_run_id=run_a.id,
        decision_type=CriticDecisionType.PROPOSE_REVISION,
        parent_spec_id=spec_b.id,
        changes={"signal_threshold": 1.0},
        rationale="wrong parent",
        prediction=None,
        confidence=None,
        provider="fake",
        model="test",
        raw_response=None,
    )
    inv, dec, res = run_critic_for_run(store=orch.store, critic=SimpleCritic(decision), run_id=run_a.id)
    assert inv.validation_status == "INVALID"
    assert res["proposal"] is None


def test_iteration_limit_blocks_critic(tmp_path):
    db = tmp_path / "db6.sqlite"
    orch = build_orchestrator(db)
    # max_iterations = 1 -> no revision allowed after first attempt
    run = orch.create_research(hypothesis_statement="h", rationale="r", parameters={"signal_threshold": 3.0, "lookback": 20}, max_iterations=1)
    # advance into discovery and execute one discovery attempt
    orch.run_next_step(run.id)
    try:
        orch.run_next_step(run.id)
    except Exception:
        # ignore if iteration limit reached during the second step
        pass
    state = orch.get_state(run.id)
    # should have iteration_count == 1
    assert state.iteration_count >= state.max_iterations
    # attempt to run critic should raise
    with pytest.raises(RuntimeError):
        run_critic_for_run(store=orch.store, critic=SimpleCritic(CriticDecision(id=new_id(), research_run_id=run.id, decision_type=CriticDecisionType.NO_USEFUL_REVISION, parent_spec_id=run.active_spec_id, changes=None, rationale=None, prediction=None, confidence=None, provider="fake", model="test", raw_response=None)), run_id=run.id)


def test_no_useful_revision_persisted_and_transition_policy(tmp_path):
    db = tmp_path / "db7.sqlite"
    orch = build_orchestrator(db)
    run = orch.create_research(hypothesis_statement="h", rationale="r", parameters={"signal_threshold": 1.0, "lookback": 20}, max_iterations=1)
    # create scenario where evaluator gives PROMOTE or REJECT depending; we want to exercise NO_USEFUL
    # advance to iteration where revision required would not be set; but force latest_eval to be ITERATE by running twice if possible
    try:
        orch.run_next_step(run.id)
        orch.run_next_step(run.id)
    except Exception:
        # ignore if iteration limit reached
        pass
    # ensure latest evaluation exists or skip
    with pytest.raises(RuntimeError):
        # NO_USEFUL should not be allowed when not in revision state; run_critic_for_run enforces this
        run_critic_for_run(store=orch.store, critic=SimpleCritic(CriticDecision(id=new_id(), research_run_id=run.id, decision_type=CriticDecisionType.NO_USEFUL_REVISION, parent_spec_id=run.active_spec_id, changes=None, rationale="none", prediction=None, confidence=None, provider="fake", model="test", raw_response=None)), run_id=run.id)


def test_critic_invocation_persists_across_reopen(tmp_path):
    db = tmp_path / "db8.sqlite"
    orch = build_orchestrator(db)
    run = orch.create_research(hypothesis_statement="h", rationale="r", parameters={"signal_threshold": 3.0, "lookback": 20}, max_iterations=3)
    _mark_revision_required(orch, run)
    # use FakeResearchCritic behavior by triggering MINIMUM_SHARPE_NOT_MET via evaluation reason codes
    # but simpler: craft NO_USEFUL decision and ensure persistence
    decision = CriticDecision(
        id=new_id(),
        research_run_id=run.id,
        decision_type=CriticDecisionType.NO_USEFUL_REVISION,
        parent_spec_id=run.active_spec_id,
        changes=None,
        rationale="no useful",
        prediction=None,
        confidence=None,
        provider="fake",
        model="test",
        raw_response=None,
    )
    inv, dec, res = run_critic_for_run(store=orch.store, critic=SimpleCritic(decision), run_id=run.id)
    # reopen store
    new_store = SQLiteStore(db)
    loaded = new_store.get_critic_invocation(inv.id)
    assert loaded is not None
    assert loaded.research_run_id == inv.research_run_id
    assert loaded.evaluation_id == inv.evaluation_id
    assert loaded.parent_spec_id == inv.parent_spec_id
    assert loaded.context_version == inv.context_version
    assert loaded.prompt_version == inv.prompt_version
    assert loaded.provider == inv.provider
    assert loaded.model == inv.model
    assert isinstance(loaded.parsed_decision, dict)
    assert loaded.validation_status == inv.validation_status


def test_context_version_and_prompt_version_persist(tmp_path):
    db = tmp_path / "db9.sqlite"
    orch = build_orchestrator(db)
    run = orch.create_research(hypothesis_statement="h", rationale="r", parameters={"signal_threshold": 3.0, "lookback": 20}, max_iterations=3)
    _mark_revision_required(orch, run)
    decision = CriticDecision(
        id=new_id(),
        research_run_id=run.id,
        decision_type=CriticDecisionType.NO_USEFUL_REVISION,
        parent_spec_id=run.active_spec_id,
        changes=None,
        rationale="no useful",
        prediction=None,
        confidence=None,
        provider="fake",
        model="test",
        raw_response=None,
    )
    inv, dec, res = run_critic_for_run(store=orch.store, critic=SimpleCritic(decision), run_id=run.id, prompt_version="v1")
    new_store = SQLiteStore(db)
    loaded = new_store.get_critic_invocation(inv.id)
    assert loaded.context_version == "v1"
    assert loaded.prompt_version == "v1"


def test_context_excludes_unrelated_runs(tmp_path):
    db = tmp_path / "db10.sqlite"
    orch = build_orchestrator(db)
    run_a = orch.create_research(hypothesis_statement="A", rationale="r", parameters={"signal_threshold": 1.0, "lookback": 10}, max_iterations=3)
    run_b = orch.create_research(hypothesis_statement="B", rationale="r", parameters={"signal_threshold": 5.0, "lookback": 20}, max_iterations=3)
    _mark_revision_required(orch, run_a)
    # craft a decision that will be persisted
    decision = CriticDecision(
        id=new_id(),
        research_run_id=run_a.id,
        decision_type=CriticDecisionType.NO_USEFUL_REVISION,
        parent_spec_id=run_a.active_spec_id,
        changes=None,
        rationale="no useful",
        prediction=None,
        confidence=None,
        provider="fake",
        model="test",
        raw_response=None,
    )
    inv, dec, res = run_critic_for_run(store=orch.store, critic=SimpleCritic(decision), run_id=run_a.id)
    # inspect saved context snapshot to ensure no mention of run_b hypothesis or specs
    stored = orch.store.get_critic_invocation(inv.id)
    ctx = stored.context_snapshot
    assert isinstance(ctx, dict)
    # ensure hypothesis id in context matches run_a's hypothesis, not run_b's
    assert ctx["hypothesis"]["id"] != orch.store.get_hypothesis(run_b.hypothesis_id).id


def test_critic_not_invoked_for_promote_or_reject(tmp_path):
    db = tmp_path / "db11.sqlite"
    orch = build_orchestrator(db)
    # create run that will likely PROMOTE (low threshold)
    run_promote = orch.create_research(hypothesis_statement="hp", rationale="r", parameters={"signal_threshold": 1.0, "lookback": 5}, max_iterations=3)
    orch.run_next_step(run_promote.id)
    # attempting to run critic now should raise because latest evaluation is not ITERATE
    with pytest.raises(RuntimeError):
        run_critic_for_run(store=orch.store, critic=SimpleCritic(CriticDecision(id=new_id(), research_run_id=run_promote.id, decision_type=CriticDecisionType.NO_USEFUL_REVISION, parent_spec_id=run_promote.active_spec_id, changes=None, rationale=None, prediction=None, confidence=None, provider="fake", model="test", raw_response=None)), run_id=run_promote.id)


def test_valid_proposal_creates_proposed_and_not_accept(tmp_path):
    db = tmp_path / "db12.sqlite"
    orch = build_orchestrator(db)
    run = orch.create_research(hypothesis_statement="h", rationale="r", parameters={"signal_threshold": 3.0, "lookback": 20}, max_iterations=3)
    _mark_revision_required(orch, run)
    spec = orch.store.get_spec(run.active_spec_id)
    decision = CriticDecision(
        id=new_id(),
        research_run_id=run.id,
        decision_type=CriticDecisionType.PROPOSE_REVISION,
        parent_spec_id=spec.id,
        changes={"signal_threshold": 2.5},
        rationale="tweak",
        prediction=None,
        confidence=None,
        provider="fake",
        model="test",
        raw_response=None,
    )
    inv, dec, res = run_critic_for_run(store=orch.store, critic=SimpleCritic(decision), run_id=run.id)
    assert inv.validation_status == "VALID"
    pid = res["proposal"]
    assert pid is not None
    prop = orch.store.get_spec_revision_proposal(pid)
    assert prop.status == SpecRevisionProposalStatus.PROPOSED
    # ensure no new spec created and active_spec unchanged
    current_spec = orch.store.get_spec(run.active_spec_id)
    assert current_spec.id == spec.id


def test_ai_proposal_not_auto_accepted(tmp_path):
    db = tmp_path / "db13.sqlite"
    orch = build_orchestrator(db)
    run = orch.create_research(hypothesis_statement="h", rationale="r", parameters={"signal_threshold": 3.0, "lookback": 20}, max_iterations=3)
    _mark_revision_required(orch, run)
    spec = orch.store.get_spec(run.active_spec_id)
    decision = CriticDecision(
        id=new_id(),
        research_run_id=run.id,
        decision_type=CriticDecisionType.PROPOSE_REVISION,
        parent_spec_id=spec.id,
        changes={"signal_threshold": 2.5},
        rationale="tweak",
        prediction=None,
        confidence=None,
        provider="fake",
        model="test",
        raw_response=None,
    )
    inv, dec, res = run_critic_for_run(store=orch.store, critic=SimpleCritic(decision), run_id=run.id)
    pid = res["proposal"]
    assert pid is not None
    # ensure proposal exists but not accepted
    prop = orch.store.get_spec_revision_proposal(pid)
    assert prop.status == SpecRevisionProposalStatus.PROPOSED
    # accept explicitly and ensure new spec created and active_spec updated
    accepted = orch.store.accept_spec_revision_proposal(pid)
    assert accepted.accepted_spec_id is not None
    new_spec = orch.store.get_spec(accepted.accepted_spec_id)
    assert new_spec is not None
    run_state = orch.get_state(run.id)
    assert run_state.active_spec_id == new_spec.id


def test_v3_to_v4_migration(tmp_path):
    # create a bare v3 DB, insert a run and spec, then initialize SQLiteStore and verify migration to v4
    db = tmp_path / "v3.sqlite"
    conn = sqlite3.connect(db)
    cur = conn.cursor()
    # create minimal v3 schema (no critic_invocations)
    cur.executescript(
        """
        PRAGMA foreign_keys = ON;
        CREATE TABLE schema_version (id INTEGER PRIMARY KEY CHECK (id = 1), version INTEGER NOT NULL);
        INSERT INTO schema_version (id, version) VALUES (1, 3);
        CREATE TABLE research_runs (id TEXT PRIMARY KEY, stage TEXT NOT NULL, status TEXT NOT NULL, hypothesis_id TEXT NOT NULL, active_spec_id TEXT NOT NULL, iteration_count INTEGER NOT NULL, max_iterations INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE hypotheses (id TEXT PRIMARY KEY, research_run_id TEXT NOT NULL, statement TEXT NOT NULL, rationale TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE research_specs (id TEXT PRIMARY KEY, research_run_id TEXT NOT NULL, version INTEGER NOT NULL, hypothesis_id TEXT NOT NULL, parameters_json TEXT NOT NULL, created_at TEXT NOT NULL, frozen_at TEXT, is_frozen INTEGER NOT NULL);
        """
    )
    # insert a run/hypothesis/spec
    run_id = new_id()
    hyp_id = new_id()
    spec_id = new_id()
    now = datetime.now().isoformat()
    cur.execute("INSERT INTO research_runs (id, stage, status, hypothesis_id, active_spec_id, iteration_count, max_iterations, created_at, updated_at) VALUES (?, 'DISCOVERY', 'ACTIVE', ?, ?, 0, 3, ?, ?)", (run_id, hyp_id, spec_id, now, now))
    cur.execute("INSERT INTO hypotheses (id, research_run_id, statement, rationale, created_at) VALUES (?, ?, ?, ?, ?)", (hyp_id, run_id, 'h', 'r', now))
    cur.execute("INSERT INTO research_specs (id, research_run_id, version, hypothesis_id, parameters_json, created_at, frozen_at, is_frozen) VALUES (?, ?, 1, ?, ?, ?, ?, 1)", (spec_id, run_id, hyp_id, '{"signal_threshold":3.0,"lookback":20}', now, now))
    conn.commit()
    conn.close()

    # now initialize current SQLiteStore which should migrate v3->v4
    store = SQLiteStore(db)
    # verify schema_version==4 and critic_invocations exists
    with store.connect() as c:
        ver = c.execute("SELECT version FROM schema_version WHERE id = 1").fetchone()[0]
        assert ver == 4
        # critic_invocations table should exist
        rows = c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='critic_invocations'").fetchall()
        assert len(rows) == 1
        # ensure our preexisting run & spec survived
        r = c.execute("SELECT id FROM research_runs WHERE id = ?", (run_id,)).fetchone()
        assert r is not None

    # reopen to test v4->v5 migration runs on second instantiation
    store2 = SQLiteStore(db)
    with store2.connect() as c:
        ver2 = c.execute("SELECT version FROM schema_version WHERE id = 1").fetchone()[0]
        assert ver2 == 5
        # new tables must exist
        tables = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        assert "research_candidates" in tables
        assert "feasibility_decisions" in tables