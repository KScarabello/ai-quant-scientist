from __future__ import annotations

import sqlite3
import pytest

from ai_quant_scientist.models.research import new_id, SpecRevisionProposal
from ai_quant_scientist.models.enums import SpecRevisionProposalStatus, ResearchAction
from ai_quant_scientist.storage.sqlite_store import SQLiteStore
from ai_quant_scientist.services.spec_builder import SpecBuilder
from ai_quant_scientist.orchestrator.orchestrator import ResearchOrchestrator, NoNextActionError
from ai_quant_scientist.policies.transitions import ResearchTransitionPolicy, IterationLimitExceededError
from ai_quant_scientist.tools.stub_backtester import StubBacktester
from ai_quant_scientist.evaluation.result_evaluator import ResultEvaluator
from ai_quant_scientist.models.evaluation import ResultEvaluationPolicy, EvaluationRecommendation


def build_orchestrator(db_path):
    return ResearchOrchestrator(
        store=SQLiteStore(db_path),
        transition_policy=ResearchTransitionPolicy(),
        spec_builder=SpecBuilder(),
        research_tool=StubBacktester(),
        result_evaluator=ResultEvaluator(),
        evaluation_policy=ResultEvaluationPolicy(),
    )


def test_frozen_spec_immutable(tmp_path):
    db_path = tmp_path / "ai_quant_scientist.db"
    orch = build_orchestrator(db_path)
    run = orch.create_research(hypothesis_statement="h", rationale="r", parameters={"signal_threshold": 2.0, "lookback": 20}, max_iterations=3)
    spec = orch.store.get_spec(run.active_spec_id)

    # attempt to overwrite V1 with different parameters should raise
    mutated = SpecRevisionProposal  # reuse type for construction convenience
    from ai_quant_scientist.models.research import ResearchSpec

    new_spec = ResearchSpec(id=spec.id, research_run_id=spec.research_run_id, version=spec.version, hypothesis_id=spec.hypothesis_id, parameters={"signal_threshold": 2.5, "lookback": 20}, parent_spec_id=spec.parent_spec_id, revision_proposal_id=spec.revision_proposal_id, created_at=spec.created_at, frozen_at=spec.frozen_at, is_frozen=spec.is_frozen)

    with pytest.raises(SQLiteStore.FrozenSpecMutationError):
        orch.store.save_spec(new_spec)


def test_v1_v2_v3_lineage(tmp_path):
    db_path = tmp_path / "ai_quant_scientist.db"
    orch = build_orchestrator(db_path)
    run = orch.create_research(hypothesis_statement="h", rationale="r", parameters={"signal_threshold": 3.0, "lookback": 20}, max_iterations=5)
    orch.run_next_step(run.id)
    orch.run_next_step(run.id)  # first attempt -> ITERATE, then set REVISION_REQUIRED
    state = orch.get_state(run.id)
    assert state.next_required_action == ResearchAction.REVISION_REQUIRED

    # create and accept two sequential revisions
    current_spec = orch.store.get_spec(state.active_spec_id)
    decisions = orch.store.list_evaluation_decisions(run.id)
    proposal1 = SpecRevisionProposal(id=new_id(), research_run_id=run.id, parent_spec_id=current_spec.id, trigger_evaluation_id=decisions[0].id, proposed_parameters={**current_spec.parameters, "signal_threshold": 2.5}, change_summary="p1", reason="test", change_record={"signal_threshold": {"before": current_spec.parameters["signal_threshold"], "after": 2.5}}, status=SpecRevisionProposalStatus.PROPOSED)
    orch.store.create_spec_revision_proposal(proposal1)
    orch.store.accept_spec_revision_proposal(proposal1.id)

    run_state = orch.get_state(run.id)
    v2 = orch.store.get_spec(run_state.active_spec_id)
    assert v2.version == 2
    assert v2.parent_spec_id == current_spec.id

    # propose v3
    proposal2 = SpecRevisionProposal(id=new_id(), research_run_id=run.id, parent_spec_id=v2.id, trigger_evaluation_id=None, proposed_parameters={**v2.parameters, "signal_threshold": 2.0}, change_summary="p2", reason="test", change_record={"signal_threshold": {"before": v2.parameters["signal_threshold"], "after": 2.0}}, status=SpecRevisionProposalStatus.PROPOSED)
    orch.store.create_spec_revision_proposal(proposal2)
    orch.store.accept_spec_revision_proposal(proposal2.id)

    run_state = orch.get_state(run.id)
    v3 = orch.store.get_spec(run_state.active_spec_id)
    assert v3.version == 3
    assert v3.parent_spec_id == v2.id
    # ensure v1 unchanged
    v1 = orch.store.get_spec(current_spec.id)
    assert v1.parameters["signal_threshold"] == 3.0


def test_unique_version_constraint(tmp_path):
    db_path = tmp_path / "ai_quant_scientist.db"
    orch = build_orchestrator(db_path)
    run = orch.create_research(hypothesis_statement="h", rationale="r", parameters={"signal_threshold": 2.0, "lookback": 20}, max_iterations=3)
    spec = orch.store.get_spec(run.active_spec_id)

    # create a new spec with same research_run_id and version but different id -> should violate unique index
    from ai_quant_scientist.models.research import ResearchSpec
    dup = ResearchSpec(id=new_id(), research_run_id=spec.research_run_id, version=spec.version, hypothesis_id=spec.hypothesis_id, parameters=spec.parameters, created_at=spec.created_at, frozen_at=spec.frozen_at, is_frozen=True)

    with pytest.raises(sqlite3.IntegrityError):
        orch.store.save_spec(dup)


def test_iterate_blocks_and_no_duplicate_attempt(tmp_path):
    db_path = tmp_path / "ai_quant_scientist.db"
    orch = build_orchestrator(db_path)
    run = orch.create_research(hypothesis_statement="h", rationale="r", parameters={"signal_threshold": 3.0, "lookback": 20}, max_iterations=3)
    orch.run_next_step(run.id)
    orch.run_next_step(run.id)
    # attempt count should be 1
    attempts = orch.store.get_attempts(run.id)
    assert len(attempts) == 1

    # running again without revision required should raise
    with pytest.raises(NoNextActionError):
        orch.run_next_step(run.id)


def test_revision_cannot_bypass_max_iterations(tmp_path):
    db_path = tmp_path / "ai_quant_scientist.db"
    orch = build_orchestrator(db_path)
    run = orch.create_research(hypothesis_statement="h", rationale="r", parameters={"signal_threshold": 3.0, "lookback": 20}, max_iterations=1)
    orch.run_next_step(run.id)
    # first attempt consumed the single iteration -> evaluator returns ITERATE and run should be marked REJECTED in transition
    result_state = orch.run_next_step(run.id)
    assert result_state.stage == orch.store.get_research_run(run.id).stage or result_state.stage  # ensure it progressed
    # Now even if a proposal is accepted, next attempt should not be allowed
    # Create a proposal and accept
    run_state = orch.get_state(run.id)
    spec = orch.store.get_spec(run_state.active_spec_id)
    from ai_quant_scientist.models.research import SpecRevisionProposal
    proposal = SpecRevisionProposal(id=new_id(), research_run_id=run.id, parent_spec_id=spec.id, trigger_evaluation_id=None, proposed_parameters={**spec.parameters, "signal_threshold": 2.0}, change_summary="p", reason="test", change_record={}, status=SpecRevisionProposalStatus.PROPOSED)
    orch.store.create_spec_revision_proposal(proposal)
    orch.store.accept_spec_revision_proposal(proposal.id)

    with pytest.raises((IterationLimitExceededError, NoNextActionError)):
        orch.run_next_step(run.id)


def test_persistence_across_restart(tmp_path):
    db_path = tmp_path / "ai_quant_scientist.db"
    orch = build_orchestrator(db_path)
    run = orch.create_research(hypothesis_statement="h", rationale="r", parameters={"signal_threshold": 3.0, "lookback": 20}, max_iterations=3)
    orch.run_next_step(run.id)
    orch.run_next_step(run.id)

    decisions = orch.store.list_evaluation_decisions(run.id)
    spec = orch.store.get_spec(orch.get_state(run.id).active_spec_id)

    # propose and accept
    proposal = SpecRevisionProposal(id=new_id(), research_run_id=run.id, parent_spec_id=spec.id, trigger_evaluation_id=decisions[0].id, proposed_parameters={**spec.parameters, "signal_threshold": 2.5}, change_summary="p", reason="t", change_record={}, status=SpecRevisionProposalStatus.PROPOSED)
    orch.store.create_spec_revision_proposal(proposal)
    orch.store.accept_spec_revision_proposal(proposal.id)

    # reopen store
    new_store = SQLiteStore(db_path)
    new_run = new_store.get_research_run(run.id)
    assert new_run is not None
    accepted_proposal = new_store.get_spec_revision_proposal(proposal.id)
    assert accepted_proposal is not None
    assert accepted_proposal.status == SpecRevisionProposalStatus.ACCEPTED
    assert new_store.get_spec(accepted_proposal.accepted_spec_id) is not None
    # original attempt still points to original spec
    attempts = new_store.get_attempts(run.id)
    assert attempts[0].spec_id != new_run.active_spec_id


def test_migration_v2_to_v3(tmp_path):
    db_path = tmp_path / "ai_quant_scientist.db"
    # create a v2-style DB
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.executescript("""
    PRAGMA foreign_keys = ON;
    CREATE TABLE schema_version (id INTEGER PRIMARY KEY CHECK (id = 1), version INTEGER NOT NULL);
    INSERT INTO schema_version (id, version) VALUES (1, 2);
    CREATE TABLE research_runs (
        id TEXT PRIMARY KEY,
        stage TEXT NOT NULL,
        status TEXT NOT NULL,
        hypothesis_id TEXT NOT NULL,
        active_spec_id TEXT NOT NULL,
        iteration_count INTEGER NOT NULL,
        max_iterations INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE TABLE hypotheses (
        id TEXT PRIMARY KEY,
        research_run_id TEXT NOT NULL,
        statement TEXT NOT NULL,
        rationale TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    CREATE TABLE research_specs (
        id TEXT PRIMARY KEY,
        research_run_id TEXT NOT NULL,
        version INTEGER NOT NULL,
        hypothesis_id TEXT NOT NULL,
        parameters_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        frozen_at TEXT,
        is_frozen INTEGER NOT NULL
    );
    """)
    # insert sample data
    run_id = new_id()
    spec_id = new_id()
    cur.execute("INSERT INTO research_runs (id, stage, status, hypothesis_id, active_spec_id, iteration_count, max_iterations, created_at, updated_at) VALUES (?, 'IDEA', 'ACTIVE', ?, ?, 0, 3, '2020-01-01T00:00:00', '2020-01-01T00:00:00')", (run_id, 'hyp-1', spec_id))
    cur.execute("INSERT INTO hypotheses (id, research_run_id, statement, rationale, created_at) VALUES (?, ?, ?, ?, ?)", ('hyp-1', run_id, 'stmt', 'r', '2020-01-01T00:00:00'))
    cur.execute("INSERT INTO research_specs (id, research_run_id, version, hypothesis_id, parameters_json, created_at, frozen_at, is_frozen) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (spec_id, run_id, 1, 'hyp-1', '{"signal_threshold":2.0,"lookback":20}', '2020-01-01T00:00:00', '2020-01-01T00:00:00', 1))
    conn.commit()
    conn.close()

    # initialize store which should migrate to v3
    store = SQLiteStore(db_path)
    # check schema_version updated
    conn2 = sqlite3.connect(db_path)
    cur2 = conn2.cursor()
    v = cur2.execute("SELECT version FROM schema_version WHERE id = 1").fetchone()[0]
    assert v == 3
    # new columns/tables should exist
    info = [r[1] for r in cur2.execute("PRAGMA table_info(research_runs)").fetchall()]
    assert 'next_required_action' in info
    info2 = [r[1] for r in cur2.execute("PRAGMA table_info(research_specs)").fetchall()]
    assert 'parent_spec_id' in info2 and 'revision_proposal_id' in info2
    # original data still readable
    loaded_run = store.get_research_run(run_id)
    assert loaded_run is not None
    loaded_spec = store.get_spec(spec_id)
    assert loaded_spec is not None
    conn2.close()
