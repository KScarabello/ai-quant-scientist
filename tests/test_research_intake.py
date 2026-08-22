"""Comprehensive deterministic tests for V0.11 Research Intake persistence.

Zero API calls. Zero network calls. Uses temp-file SQLite DBs.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from ai_quant_scientist.capabilities import (
    AssetClass,
    Capability,
    CapabilityRegistry,
    DataKind,
    DataRequirement,
    FeasibilityReasonCode,
    FeasibilityResult,
    FeasibilityStatus,
    GATE_VERSION,
    GateDecision,
    REGISTRY_VERSION,
    RequirementResult,
    ResearchCandidate,
    ResearchFeasibilityDecision,
    ResearchFeasibilityGate,
    Resolution,
    ToolKind,
    ToolRequirement,
    build_v1_registry,
)
from ai_quant_scientist.capabilities.intake import (
    GovernedResearchIntake,
    IntakeResult,
    StoredFeasibilityDecision,
)
from ai_quant_scientist.capabilities.serialization import (
    compute_candidate_fingerprint,
    feasibility_result_to_dict,
    requirements_from_json,
    requirements_to_json,
)
from ai_quant_scientist.storage.sqlite_store import SQLiteStore


# ─── fixtures ─────────────────────────────────────────────────────────────────

def _store(tmp_path: Path) -> SQLiteStore:
    return SQLiteStore(tmp_path / "test.db")


def _synthetic_candidate() -> ResearchCandidate:
    return ResearchCandidate.create(
        hypothesis_statement="signal_threshold governs trade frequency",
        hypothesis_rationale="TOO_FEW_TRADES suggests threshold is too strict",
        requirements=[
            DataRequirement(requirement_id="data", data_kind=DataKind.SYNTHETIC_PARAMETRIC,
                            asset_class=AssetClass.SYNTHETIC,
                            required_parameters=("signal_threshold", "lookback")),
            ToolRequirement(requirement_id="tool", tool_kind=ToolKind.BACKTEST_EXECUTION),
        ],
    )


def _blocked_candidate() -> ResearchCandidate:
    return ResearchCandidate.create(
        hypothesis_statement="order-book imbalance predicts MES returns",
        hypothesis_rationale="microstructure hypothesis requiring real data",
        requirements=[
            DataRequirement(requirement_id="ob", data_kind=DataKind.ORDER_BOOK,
                            asset_class=AssetClass.FUTURES, instruments=("MES",),
                            resolution=Resolution.SECOND_1),
            ToolRequirement(requirement_id="futures_tool", tool_kind=ToolKind.BACKTEST_EXECUTION),
        ],
    )


# ─── strong typing: FeasibilityResult, not object ────────────────────────────

def test_feasibility_decision_is_strongly_typed():
    gate = ResearchFeasibilityGate()
    decision = gate.evaluate(_synthetic_candidate(), build_v1_registry())
    assert isinstance(decision.feasibility_result, FeasibilityResult)


def test_feasibility_decision_no_object_annotation():
    """Confirm the field is not typed as bare object."""
    import inspect
    hints = {}
    for f in ResearchFeasibilityDecision.__dataclass_fields__.values():
        hints[f.name] = f.type
    assert "feasibility_result" in hints
    assert hints["feasibility_result"] is not object


# ─── requirements serialization round-trips ──────────────────────────────────

def test_data_requirement_round_trip():
    req = DataRequirement(
        requirement_id="r1",
        data_kind=DataKind.OHLCV,
        asset_class=AssetClass.EQUITY,
        resolution=Resolution.DAILY,
        required_fields=("close", "volume"),
        instruments=("AAPL", "MSFT"),
        start_date=date(2020, 1, 1),
        end_date=date(2025, 12, 31),
        point_in_time_required=True,
    )
    s = requirements_to_json((req,))
    (r2,) = requirements_from_json(s)
    assert r2 == req


def test_tool_requirement_round_trip():
    req = ToolRequirement(requirement_id="t", tool_kind=ToolKind.BACKTEST_EXECUTION, label="lbl")
    s = requirements_to_json((req,))
    (r2,) = requirements_from_json(s)
    assert r2 == req


def test_legacy_tool_requirement_snapshot_still_round_trips():
    req = ToolRequirement(requirement_id="t", legacy_tool_name="stub_backtester_v1", label="lbl")
    s = requirements_to_json((req,))
    (r2,) = requirements_from_json(s)
    assert r2 == req


def test_mixed_requirements_round_trip_preserves_ordering_and_types():
    reqs = (
        DataRequirement(requirement_id="d", data_kind=DataKind.SYNTHETIC_PARAMETRIC),
        ToolRequirement(requirement_id="t", tool_kind=ToolKind.BACKTEST_EXECUTION),
        DataRequirement(requirement_id="d2", data_kind=DataKind.OHLCV, resolution=Resolution.MINUTE_1),
    )
    s = requirements_to_json(reqs)
    back = requirements_from_json(s)
    assert len(back) == 3
    assert isinstance(back[0], DataRequirement) and back[0].requirement_id == "d"
    assert isinstance(back[1], ToolRequirement) and back[1].tool_kind == ToolKind.BACKTEST_EXECUTION
    assert isinstance(back[2], DataRequirement) and back[2].data_kind == DataKind.OHLCV


def test_null_optional_fields_round_trip():
    req = DataRequirement(requirement_id="r", data_kind=DataKind.SYNTHETIC_PARAMETRIC)
    s = requirements_to_json((req,))
    (r2,) = requirements_from_json(s)
    assert r2.instruments is None
    assert r2.required_fields is None
    assert r2.start_date is None
    assert r2.asset_class is None


# ─── candidate fingerprint ───────────────────────────────────────────────────

def test_candidate_fingerprint_stable():
    c = _synthetic_candidate()
    f1 = compute_candidate_fingerprint(c.hypothesis_statement, c.hypothesis_rationale, c.requirements)
    f2 = compute_candidate_fingerprint(c.hypothesis_statement, c.hypothesis_rationale, c.requirements)
    assert f1 == f2 and len(f1) == 64


def test_candidate_fingerprint_excludes_id_and_timestamps():
    c1 = _synthetic_candidate()
    c2 = ResearchCandidate.create(
        hypothesis_statement=c1.hypothesis_statement,
        hypothesis_rationale=c1.hypothesis_rationale,
        requirements=list(c1.requirements),
    )
    f1 = compute_candidate_fingerprint(c1.hypothesis_statement, c1.hypothesis_rationale, c1.requirements)
    f2 = compute_candidate_fingerprint(c2.hypothesis_statement, c2.hypothesis_rationale, c2.requirements)
    assert f1 == f2  # same science, different id/timestamp → same fingerprint


def test_candidate_fingerprint_changes_with_different_hypothesis():
    c = _synthetic_candidate()
    f1 = compute_candidate_fingerprint(c.hypothesis_statement, c.hypothesis_rationale, c.requirements)
    f2 = compute_candidate_fingerprint("different hypothesis", c.hypothesis_rationale, c.requirements)
    assert f1 != f2


# ─── candidate persistence ────────────────────────────────────────────────────

def test_save_and_load_candidate(tmp_path):
    store = _store(tmp_path)
    c = _synthetic_candidate()
    store.save_research_candidate(c)
    loaded = store.get_research_candidate(c.id)
    assert loaded is not None
    assert loaded.id == c.id
    assert loaded.hypothesis_statement == c.hypothesis_statement
    assert loaded.hypothesis_rationale == c.hypothesis_rationale
    assert loaded.source == c.source
    assert loaded.requirements == c.requirements


def test_save_candidate_idempotent(tmp_path):
    store = _store(tmp_path)
    c = _synthetic_candidate()
    store.save_research_candidate(c)
    store.save_research_candidate(c)  # second call should not raise
    all_c = store.list_research_candidates()
    assert sum(1 for x in all_c if x.id == c.id) == 1


def test_unknown_candidate_returns_none(tmp_path):
    store = _store(tmp_path)
    assert store.get_research_candidate("nonexistent") is None


def test_list_candidates_returns_all(tmp_path):
    store = _store(tmp_path)
    c1 = _synthetic_candidate()
    c2 = _blocked_candidate()
    store.save_research_candidate(c1)
    store.save_research_candidate(c2)
    all_c = store.list_research_candidates()
    ids = {c.id for c in all_c}
    assert c1.id in ids and c2.id in ids


def test_persisted_candidate_requirements_match(tmp_path):
    store = _store(tmp_path)
    c = _blocked_candidate()
    store.save_research_candidate(c)
    loaded = store.get_research_candidate(c.id)
    assert loaded.requirements == c.requirements


# ─── feasibility decision persistence ────────────────────────────────────────

def test_save_and_query_feasibility_decision(tmp_path):
    store = _store(tmp_path)
    c = _synthetic_candidate()
    store.save_research_candidate(c)
    gate = ResearchFeasibilityGate()
    decision = gate.evaluate(c, build_v1_registry())
    store.save_feasibility_decision(decision)
    decisions = store.get_feasibility_decisions(c.id)
    assert len(decisions) == 1
    d = decisions[0]
    assert d.candidate_id == c.id
    assert d.gate_decision == GateDecision.READY_FOR_SPEC
    assert d.gate_version == GATE_VERSION
    assert d.registry_version == REGISTRY_VERSION
    assert d.registry_fingerprint == build_v1_registry().fingerprint


def test_blocked_decision_persisted_with_reason_codes(tmp_path):
    store = _store(tmp_path)
    c = _blocked_candidate()
    store.save_research_candidate(c)
    gate = ResearchFeasibilityGate()
    decision = gate.evaluate(c, build_v1_registry())
    store.save_feasibility_decision(decision)
    decisions = store.get_feasibility_decisions(c.id)
    d = decisions[0]
    assert d.gate_decision == GateDecision.BLOCKED_CAPABILITY
    assert len(d.unsatisfied_ids) > 0
    assert FeasibilityReasonCode.NO_MATCHING_DATA_KIND in d.reason_codes or \
           FeasibilityReasonCode.TOOL_UNAVAILABLE in d.reason_codes


def test_feasibility_snapshot_persisted(tmp_path):
    store = _store(tmp_path)
    c = _synthetic_candidate()
    store.save_research_candidate(c)
    gate = ResearchFeasibilityGate()
    decision = gate.evaluate(c, build_v1_registry())
    store.save_feasibility_decision(decision)
    d = store.get_feasibility_decisions(c.id)[0]
    assert "status" in d.feasibility_snapshot
    assert "registry_fingerprint" in d.feasibility_snapshot
    assert "requirement_results" in d.feasibility_snapshot


def test_feasibility_decision_not_overwritten_on_duplicate_id(tmp_path):
    store = _store(tmp_path)
    c = _synthetic_candidate()
    store.save_research_candidate(c)
    gate = ResearchFeasibilityGate()
    decision = gate.evaluate(c, build_v1_registry())
    store.save_feasibility_decision(decision)
    store.save_feasibility_decision(decision)  # idempotent: same id, no duplicate
    assert len(store.get_feasibility_decisions(c.id)) == 1


# ─── historical decisions: multiple per candidate ────────────────────────────

def test_candidate_can_have_multiple_feasibility_decisions(tmp_path):
    store = _store(tmp_path)
    c = _blocked_candidate()
    store.save_research_candidate(c)
    registry1 = build_v1_registry()  # stub only → blocked

    # Add a capability that satisfies the order-book requirement (test-only registry)
    from ai_quant_scientist.capabilities.models import Capability as Cap
    extra = Cap(
        capability_id="futures_backtester",
        capability_type="EXECUTION_TOOL",
        data_kind=DataKind.ORDER_BOOK,
        asset_classes=(AssetClass.FUTURES,),
        resolutions=(Resolution.SECOND_1,),
        instruments=("MES",),
        supported_tool_kinds=(ToolKind.BACKTEST_EXECUTION,),
        provider="test",
        enabled=True,
        version="1",
    )
    registry2 = CapabilityRegistry([
        *registry1.list_capabilities(),
        extra,
    ])

    gate = ResearchFeasibilityGate()
    d1 = gate.evaluate(c, registry1)
    d2 = gate.evaluate(c, registry2)  # different registry content
    store.save_feasibility_decision(d1)
    store.save_feasibility_decision(d2)

    all_decisions = store.get_feasibility_decisions(c.id)
    assert len(all_decisions) == 2
    decisions_by_decision = {d.gate_decision for d in all_decisions}
    assert GateDecision.BLOCKED_CAPABILITY in decisions_by_decision


def test_historical_decisions_ordered_by_evaluated_at(tmp_path):
    store = _store(tmp_path)
    c = _synthetic_candidate()
    store.save_research_candidate(c)
    registry = build_v1_registry()
    gate = ResearchFeasibilityGate()

    d1 = gate.evaluate(c, registry)
    d2 = gate.evaluate(c, registry)
    store.save_feasibility_decision(d1)
    store.save_feasibility_decision(d2)

    decisions = store.get_feasibility_decisions(c.id)
    assert len(decisions) == 2
    assert decisions[0].evaluated_at <= decisions[1].evaluated_at


# ─── GovernedResearchIntake ───────────────────────────────────────────────────

def test_intake_synthetic_ready_for_spec(tmp_path):
    store = _store(tmp_path)
    intake = GovernedResearchIntake(store, build_v1_registry())
    result = intake.submit(_synthetic_candidate())
    assert result.is_ready
    assert not result.is_blocked
    assert result.feasibility_decision.decision == GateDecision.READY_FOR_SPEC


def test_intake_blocked_candidate_no_spec_no_run(tmp_path):
    store = _store(tmp_path)
    intake = GovernedResearchIntake(store, build_v1_registry())
    result = intake.submit(_blocked_candidate())
    assert result.is_blocked
    assert not result.is_ready
    # No research run must have been created
    with store.connect() as c:
        count = c.execute("SELECT COUNT(*) FROM research_runs").fetchone()[0]
    assert count == 0


def test_intake_persists_candidate(tmp_path):
    store = _store(tmp_path)
    c = _synthetic_candidate()
    intake = GovernedResearchIntake(store, build_v1_registry())
    intake.submit(c)
    assert store.get_research_candidate(c.id) is not None


def test_intake_persists_feasibility_decision(tmp_path):
    store = _store(tmp_path)
    c = _synthetic_candidate()
    intake = GovernedResearchIntake(store, build_v1_registry())
    intake.submit(c)
    decisions = store.get_feasibility_decisions(c.id)
    assert len(decisions) == 1


def test_intake_idempotent_candidate(tmp_path):
    store = _store(tmp_path)
    c = _synthetic_candidate()
    intake = GovernedResearchIntake(store, build_v1_registry())
    intake.submit(c)
    intake.submit(c)  # second call should not raise or duplicate the candidate
    candidates = store.list_research_candidates()
    assert sum(1 for x in candidates if x.id == c.id) == 1


def test_intake_blocked_does_not_reject_hypothesis(tmp_path):
    """BLOCKED_CAPABILITY ≠ hypothesis rejected. Candidate remains retrievable."""
    store = _store(tmp_path)
    c = _blocked_candidate()
    intake = GovernedResearchIntake(store, build_v1_registry())
    result = intake.submit(c)
    assert result.is_blocked
    loaded = store.get_research_candidate(c.id)
    assert loaded is not None
    assert loaded.hypothesis_statement == c.hypothesis_statement


# ─── re-evaluation ────────────────────────────────────────────────────────────

def test_re_evaluate_blocked_then_ready(tmp_path):
    store = _store(tmp_path)
    c = _blocked_candidate()
    intake_v1 = GovernedResearchIntake(store, build_v1_registry())
    r1 = intake_v1.submit(c)
    assert r1.is_blocked

    # Build a registry that satisfies the order-book requirement
    from ai_quant_scientist.capabilities.models import Capability as Cap
    extra = Cap(
        capability_id="futures_backtester",
        capability_type="EXECUTION_TOOL",
        data_kind=DataKind.ORDER_BOOK,
        asset_classes=(AssetClass.FUTURES,),
        resolutions=(Resolution.SECOND_1,),
        instruments=("MES",),
        supported_tool_kinds=(ToolKind.BACKTEST_EXECUTION,),
        provider="test",
        enabled=True,
        version="1",
    )
    registry2 = CapabilityRegistry([*build_v1_registry().list_capabilities(), extra])
    intake_v2 = GovernedResearchIntake(store, registry2)
    r2 = intake_v2.re_evaluate(c.id)
    assert r2.is_ready

    all_decisions = store.get_feasibility_decisions(c.id)
    assert len(all_decisions) == 2


def test_re_evaluate_unknown_candidate_raises(tmp_path):
    store = _store(tmp_path)
    intake = GovernedResearchIntake(store, build_v1_registry())
    with pytest.raises(KeyError):
        intake.re_evaluate("nonexistent-id")


# ─── bypass safety ────────────────────────────────────────────────────────────

def test_gate_cannot_be_bypassed_through_governed_intake(tmp_path):
    """The registry check is always exercised; there is no bypass path in GovernedResearchIntake."""
    store = _store(tmp_path)
    c = _blocked_candidate()
    intake = GovernedResearchIntake(store, build_v1_registry())
    result = intake.submit(c)
    assert result.is_blocked
    with store.connect() as conn:
        count = conn.execute("SELECT COUNT(*) FROM research_runs").fetchone()[0]
    assert count == 0


# ─── schema migration ─────────────────────────────────────────────────────────

def test_v4_to_v5_migration(tmp_path):
    db = tmp_path / "v4.sqlite"
    # Create a minimal v4 database
    conn = sqlite3.connect(db)
    cur = conn.cursor()
    cur.executescript("""
        PRAGMA foreign_keys = ON;
        CREATE TABLE schema_version (id INTEGER PRIMARY KEY CHECK (id = 1), version INTEGER NOT NULL);
        INSERT INTO schema_version (id, version) VALUES (1, 4);
        CREATE TABLE research_runs (id TEXT PRIMARY KEY, stage TEXT, status TEXT,
            hypothesis_id TEXT, active_spec_id TEXT, iteration_count INTEGER,
            max_iterations INTEGER, created_at TEXT, updated_at TEXT,
            next_required_action TEXT NOT NULL DEFAULT 'NONE');
        CREATE TABLE critic_invocations (
            id TEXT PRIMARY KEY, research_run_id TEXT NOT NULL, evaluation_id TEXT,
            parent_spec_id TEXT, context_version TEXT NOT NULL, prompt_version TEXT,
            provider TEXT, model TEXT, context_snapshot_json TEXT, raw_response_text TEXT,
            parsed_decision_json TEXT, validation_status TEXT, validation_errors_json TEXT,
            resulting_proposal_id TEXT, created_at TEXT NOT NULL, completed_at TEXT);
    """)
    conn.commit()
    conn.close()

    store = SQLiteStore(db)
    with store.connect() as c:
        ver = c.execute("SELECT version FROM schema_version WHERE id = 1").fetchone()[0]
        assert ver == 5
        tables = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        assert "research_candidates" in tables
        assert "feasibility_decisions" in tables
        # pre-existing critic_invocations must survive
        assert "critic_invocations" in tables


def test_fresh_v5_db_has_all_tables(tmp_path):
    store = SQLiteStore(tmp_path / "fresh.db")
    with store.connect() as c:
        tables = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    for t in ("research_candidates", "feasibility_decisions", "critic_invocations", "research_runs"):
        assert t in tables


def test_v5_migration_idempotent(tmp_path):
    # After first open: v5→v6; after second: already v6
    store1 = SQLiteStore(tmp_path / "t.db")
    store2 = SQLiteStore(tmp_path / "t.db")
    with store2.connect() as c:
        ver = c.execute("SELECT version FROM schema_version WHERE id = 1").fetchone()[0]
        assert ver == 6


# ─── no network calls ─────────────────────────────────────────────────────────

def test_intake_makes_no_network_calls(tmp_path, monkeypatch):
    import urllib.request
    called = []
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **kw: called.append(True))
    store = _store(tmp_path)
    intake = GovernedResearchIntake(store, build_v1_registry())
    intake.submit(_synthetic_candidate())
    assert not called


# ─── determinism ─────────────────────────────────────────────────────────────

def test_same_candidate_same_registry_same_logical_decision(tmp_path):
    c = _synthetic_candidate()
    registry = build_v1_registry()
    gate = ResearchFeasibilityGate()
    d1 = gate.evaluate(c, registry)
    d2 = gate.evaluate(c, registry)
    assert d1.decision == d2.decision
    assert d1.registry_fingerprint == d2.registry_fingerprint
    assert d1.gate_version == d2.gate_version
