"""Comprehensive deterministic tests for Capabilities & Data Requirements (V0.9/V0.10).

Zero network calls. No randomness.
"""
from __future__ import annotations

from datetime import date, datetime

import pytest

from ai_quant_scientist.capabilities import (
    AnyRequirement,
    AssetClass,
    Capability,
    CapabilityRegistry,
    DataKind,
    DataRequirement,
    FeasibilityReasonCode,
    FeasibilityStatus,
    GATE_VERSION,
    GateDecision,
    REGISTRY_VERSION,
    RequirementResult,
    Resolution,
    ResearchCandidate,
    ResearchFeasibilityDecision,
    ResearchFeasibilityGate,
    ToolRequirement,
    build_v1_registry,
    compute_registry_fingerprint,
)
from ai_quant_scientist.capabilities.v1_registry import DEFAULT_REGISTRY, _CAPABILITIES


# ─── fixtures ─────────────────────────────────────────────────────────────────

def _synth_req(
    req_id: str = "r1",
    asset_class: AssetClass | None = AssetClass.SYNTHETIC,
    resolution: Resolution | None = None,
    required_fields: tuple[str, ...] | None = None,
    required_parameters: tuple[str, ...] | None = None,
    instruments: tuple[str, ...] | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    point_in_time_required: bool = False,
) -> DataRequirement:
    return DataRequirement(
        requirement_id=req_id,
        data_kind=DataKind.SYNTHETIC_PARAMETRIC,
        asset_class=asset_class,
        resolution=resolution,
        required_fields=required_fields,
        required_parameters=required_parameters,
        instruments=instruments,
        start_date=start_date,
        end_date=end_date,
        point_in_time_required=point_in_time_required,
    )


def _make_cap(**overrides) -> Capability:
    defaults = dict(
        capability_id="test_cap",
        capability_type="DATA_FEED",
        data_kind=DataKind.OHLCV,
        asset_classes=(AssetClass.EQUITY,),
        resolutions=(Resolution.DAILY,),
        instruments=None,
        available_fields=("open", "high", "low", "close", "volume"),
        coverage_start=date(2010, 1, 1),
        coverage_end=date(2026, 1, 1),
        point_in_time=False,
        supported_parameters=None,
        provider="test",
        enabled=True,
        version="1",
    )
    defaults.update(overrides)
    return Capability(**defaults)


# ─── registry basics ──────────────────────────────────────────────────────────

def test_v1_registry_loads():
    reg = build_v1_registry()
    assert len(reg.list_capabilities()) >= 1


def test_registry_version():
    reg = build_v1_registry()
    assert reg.version == REGISTRY_VERSION


def test_registry_fingerprint_is_non_empty():
    assert len(DEFAULT_REGISTRY.fingerprint) == 64  # SHA-256 hex


def test_registry_ordering_does_not_affect_fingerprint():
    cap_a = _make_cap(capability_id="a_cap")
    cap_b = _make_cap(capability_id="b_cap")
    reg1 = CapabilityRegistry([cap_a, cap_b])
    reg2 = CapabilityRegistry([cap_b, cap_a])
    assert reg1.fingerprint == reg2.fingerprint


def test_different_content_changes_fingerprint():
    cap1 = _make_cap(capability_id="cap", version="1")
    cap2 = _make_cap(capability_id="cap", version="2")
    f1 = compute_registry_fingerprint([cap1])
    f2 = compute_registry_fingerprint([cap2])
    assert f1 != f2


def test_empty_registry_has_stable_fingerprint():
    f1 = compute_registry_fingerprint([])
    f2 = compute_registry_fingerprint([])
    assert f1 == f2


# ─── V1 actual content ────────────────────────────────────────────────────────

def test_stub_backtester_is_registered():
    cap_ids = [c.capability_id for c in DEFAULT_REGISTRY.list_capabilities()]
    assert "stub_backtester_v1" in cap_ids


def test_stub_backtester_is_synthetic_parametric():
    stub = next(c for c in DEFAULT_REGISTRY.list_capabilities() if c.capability_id == "stub_backtester_v1")
    assert stub.data_kind == DataKind.SYNTHETIC_PARAMETRIC


def test_stub_backtester_has_no_real_asset_class():
    stub = next(c for c in DEFAULT_REGISTRY.list_capabilities() if c.capability_id == "stub_backtester_v1")
    assert AssetClass.EQUITY not in stub.asset_classes
    assert AssetClass.SYNTHETIC in stub.asset_classes


def test_stub_backtester_supports_required_parameters():
    stub = next(c for c in DEFAULT_REGISTRY.list_capabilities() if c.capability_id == "stub_backtester_v1")
    assert "signal_threshold" in stub.supported_parameters
    assert "lookback" in stub.supported_parameters


# ─── disabled capability does not satisfy ─────────────────────────────────────

def test_disabled_capability_does_not_satisfy_requirement():
    cap = _make_cap(capability_id="disabled", enabled=False)
    reg = CapabilityRegistry([cap])
    req = DataRequirement(requirement_id="r", data_kind=DataKind.OHLCV)
    result = reg.evaluate_requirement(req)
    assert not result.satisfied
    assert FeasibilityReasonCode.NO_MATCHING_DATA_KIND in result.reason_codes


# ─── unknown capability fails closed ──────────────────────────────────────────

def test_unregistered_capability_not_assumed_available():
    """Regression: a capability that is not registered must NOT be assumed to exist."""
    reg = CapabilityRegistry([])  # empty registry
    req = DataRequirement(requirement_id="r", data_kind=DataKind.OHLCV)
    result = reg.evaluate_requirement(req)
    assert not result.satisfied
    assert FeasibilityReasonCode.NO_MATCHING_DATA_KIND in result.reason_codes


# ─── exact match → TESTABLE ───────────────────────────────────────────────────

def test_synthetic_parametric_requirement_testable():
    req = _synth_req(required_parameters=("signal_threshold", "lookback"))
    result = DEFAULT_REGISTRY.evaluate_requirement(req)
    assert result.satisfied
    assert result.matched_capability is not None


def test_synthetic_parametric_with_no_constraints_testable():
    req = DataRequirement(requirement_id="r", data_kind=DataKind.SYNTHETIC_PARAMETRIC)
    result = DEFAULT_REGISTRY.evaluate_requirement(req)
    assert result.satisfied


# ─── unsupported data kind ────────────────────────────────────────────────────

def test_ohlcv_not_testable():
    req = DataRequirement(requirement_id="r", data_kind=DataKind.OHLCV)
    result = DEFAULT_REGISTRY.evaluate_requirement(req)
    assert not result.satisfied
    assert FeasibilityReasonCode.NO_MATCHING_DATA_KIND in result.reason_codes


def test_order_book_not_testable():
    req = DataRequirement(requirement_id="r", data_kind=DataKind.ORDER_BOOK)
    result = DEFAULT_REGISTRY.evaluate_requirement(req)
    assert not result.satisfied
    assert FeasibilityReasonCode.NO_MATCHING_DATA_KIND in result.reason_codes


def test_fundamentals_not_testable():
    req = DataRequirement(requirement_id="r", data_kind=DataKind.FUNDAMENTALS)
    result = DEFAULT_REGISTRY.evaluate_requirement(req)
    assert not result.satisfied


# ─── asset class ──────────────────────────────────────────────────────────────

def test_equity_asset_class_unavailable_on_stub():
    req = _synth_req(asset_class=AssetClass.EQUITY)
    result = DEFAULT_REGISTRY.evaluate_requirement(req)
    assert not result.satisfied
    assert FeasibilityReasonCode.ASSET_CLASS_UNAVAILABLE in result.reason_codes


def test_synthetic_asset_class_satisfied():
    req = _synth_req(asset_class=AssetClass.SYNTHETIC)
    result = DEFAULT_REGISTRY.evaluate_requirement(req)
    assert result.satisfied


# ─── resolution ───────────────────────────────────────────────────────────────

def test_daily_resolution_unavailable_on_stub():
    req = _synth_req(resolution=Resolution.DAILY)
    result = DEFAULT_REGISTRY.evaluate_requirement(req)
    assert not result.satisfied
    assert FeasibilityReasonCode.RESOLUTION_UNAVAILABLE in result.reason_codes


def test_not_applicable_resolution_satisfied():
    req = _synth_req(resolution=Resolution.NOT_APPLICABLE)
    result = DEFAULT_REGISTRY.evaluate_requirement(req)
    assert result.satisfied


# ─── fields ───────────────────────────────────────────────────────────────────

def test_missing_required_field_not_testable():
    cap = _make_cap(available_fields=("open", "high", "low", "close"))
    reg = CapabilityRegistry([cap])
    req = DataRequirement(
        requirement_id="r",
        data_kind=DataKind.OHLCV,
        required_fields=("open", "high", "low", "close", "volume"),
    )
    result = reg.evaluate_requirement(req)
    assert not result.satisfied
    assert FeasibilityReasonCode.REQUIRED_FIELD_MISSING in result.reason_codes


def test_supported_subset_of_fields_testable():
    cap = _make_cap(available_fields=("open", "high", "low", "close", "volume"))
    reg = CapabilityRegistry([cap])
    req = DataRequirement(
        requirement_id="r",
        data_kind=DataKind.OHLCV,
        required_fields=("close", "volume"),  # subset
    )
    result = reg.evaluate_requirement(req)
    assert result.satisfied


# ─── date coverage ────────────────────────────────────────────────────────────

def test_complete_date_coverage_testable():
    cap = _make_cap(coverage_start=date(2010, 1, 1), coverage_end=date(2026, 1, 1))
    reg = CapabilityRegistry([cap])
    req = DataRequirement(
        requirement_id="r", data_kind=DataKind.OHLCV,
        start_date=date(2020, 1, 1), end_date=date(2025, 12, 31),
    )
    assert reg.evaluate_requirement(req).satisfied


def test_insufficient_date_coverage_not_testable():
    cap = _make_cap(coverage_start=date(2020, 1, 1), coverage_end=date(2026, 1, 1))
    reg = CapabilityRegistry([cap])
    req = DataRequirement(
        requirement_id="r", data_kind=DataKind.OHLCV,
        start_date=date(2015, 1, 1), end_date=date(2025, 12, 31),  # starts before coverage
    )
    result = reg.evaluate_requirement(req)
    assert not result.satisfied
    assert FeasibilityReasonCode.DATE_COVERAGE_INSUFFICIENT in result.reason_codes


def test_unknown_coverage_does_not_satisfy_bounded_date_requirement():
    cap = _make_cap(coverage_start=None, coverage_end=None)  # unknown
    reg = CapabilityRegistry([cap])
    req = DataRequirement(
        requirement_id="r", data_kind=DataKind.OHLCV,
        start_date=date(2020, 1, 1), end_date=date(2025, 12, 31),
    )
    result = reg.evaluate_requirement(req)
    assert not result.satisfied
    assert FeasibilityReasonCode.DATE_COVERAGE_INSUFFICIENT in result.reason_codes


# ─── instruments ──────────────────────────────────────────────────────────────

def test_unsupported_instrument_not_testable():
    cap = _make_cap(instruments=("AAPL", "MSFT"))
    reg = CapabilityRegistry([cap])
    req = DataRequirement(
        requirement_id="r", data_kind=DataKind.OHLCV,
        instruments=("AAPL", "GOOGL"),  # GOOGL not in cap
    )
    result = reg.evaluate_requirement(req)
    assert not result.satisfied
    assert FeasibilityReasonCode.INSTRUMENT_UNAVAILABLE in result.reason_codes


def test_none_instruments_on_capability_does_not_satisfy_constrained_requirement():
    # Hardened: capability.instruments=None means NOT DECLARED, not unrestricted.
    cap = _make_cap(instruments=None)
    reg = CapabilityRegistry([cap])
    req = DataRequirement(
        requirement_id="r", data_kind=DataKind.OHLCV,
        instruments=("AAPL", "NVDA", "SPY"),
    )
    result = reg.evaluate_requirement(req)
    assert not result.satisfied
    assert FeasibilityReasonCode.INSTRUMENT_UNAVAILABLE in result.reason_codes


def test_unconstrained_instrument_requirement_not_affected_by_none_capability():
    # If the requirement doesn't constrain instruments, None capability instruments is irrelevant.
    cap = _make_cap(instruments=None)
    reg = CapabilityRegistry([cap])
    req = DataRequirement(requirement_id="r", data_kind=DataKind.OHLCV)  # no instrument constraint
    assert reg.evaluate_requirement(req).satisfied


# ─── point-in-time ────────────────────────────────────────────────────────────

def test_pit_required_not_satisfied_by_non_pit_capability():
    cap = _make_cap(point_in_time=False)
    reg = CapabilityRegistry([cap])
    req = DataRequirement(
        requirement_id="r", data_kind=DataKind.OHLCV, point_in_time_required=True,
    )
    result = reg.evaluate_requirement(req)
    assert not result.satisfied
    assert FeasibilityReasonCode.POINT_IN_TIME_UNAVAILABLE in result.reason_codes


def test_pit_not_required_satisfied_by_non_pit_capability():
    cap = _make_cap(point_in_time=False)
    reg = CapabilityRegistry([cap])
    req = DataRequirement(
        requirement_id="r", data_kind=DataKind.OHLCV, point_in_time_required=False,
    )
    assert reg.evaluate_requirement(req).satisfied


# ─── required parameters ──────────────────────────────────────────────────────

def test_missing_required_parameter_not_testable():
    req = _synth_req(required_parameters=("signal_threshold", "lookback", "volatility_band"))
    result = DEFAULT_REGISTRY.evaluate_requirement(req)
    assert not result.satisfied
    assert FeasibilityReasonCode.REQUIRED_PARAMETER_MISSING in result.reason_codes


def test_none_parameters_on_capability_does_not_satisfy_constrained_requirement():
    # Hardened: capability.supported_parameters=None means NOT DECLARED, not unrestricted.
    cap = _make_cap(supported_parameters=None)
    reg = CapabilityRegistry([cap])
    req = DataRequirement(
        requirement_id="r", data_kind=DataKind.OHLCV,
        required_parameters=("signal_threshold",),
    )
    result = reg.evaluate_requirement(req)
    assert not result.satisfied
    assert FeasibilityReasonCode.REQUIRED_PARAMETER_MISSING in result.reason_codes


def test_unconstrained_parameter_requirement_not_affected_by_none_capability():
    # If the requirement doesn't constrain parameters, None capability parameters is irrelevant.
    cap = _make_cap(supported_parameters=None)
    reg = CapabilityRegistry([cap])
    req = DataRequirement(requirement_id="r", data_kind=DataKind.OHLCV)  # no parameter constraint
    assert reg.evaluate_requirement(req).satisfied


# ─── multiple requirements ────────────────────────────────────────────────────

def test_all_satisfied_is_testable():
    r1 = DataRequirement(requirement_id="r1", data_kind=DataKind.SYNTHETIC_PARAMETRIC)
    r2 = _synth_req(req_id="r2", required_parameters=("signal_threshold",))
    result = DEFAULT_REGISTRY.evaluate([r1, r2])
    assert result.status == FeasibilityStatus.TESTABLE
    assert "r1" in result.satisfied_ids
    assert "r2" in result.satisfied_ids
    assert len(result.unsatisfied_ids) == 0


def test_one_unsatisfied_makes_overall_not_testable():
    """Requirement A satisfied (synthetic) + Requirement B not (OHLCV) → NOT_TESTABLE."""
    r_ok = DataRequirement(requirement_id="synth", data_kind=DataKind.SYNTHETIC_PARAMETRIC)
    r_bad = DataRequirement(requirement_id="ohlcv", data_kind=DataKind.OHLCV)
    result = DEFAULT_REGISTRY.evaluate([r_ok, r_bad])
    assert result.status == FeasibilityStatus.NOT_TESTABLE
    assert "synth" in result.satisfied_ids
    assert "ohlcv" in result.unsatisfied_ids
    assert FeasibilityReasonCode.NO_MATCHING_DATA_KIND in result.reason_codes


def test_individual_results_preserved():
    """Per-requirement verdicts must be accessible regardless of overall status."""
    r_ok = DataRequirement(requirement_id="synth", data_kind=DataKind.SYNTHETIC_PARAMETRIC)
    r_bad = DataRequirement(requirement_id="ohlcv", data_kind=DataKind.OHLCV)
    result = DEFAULT_REGISTRY.evaluate([r_ok, r_bad])
    by_id = {r.requirement.requirement_id: r for r in result.requirement_results}
    assert by_id["synth"].satisfied
    assert not by_id["ohlcv"].satisfied


def test_empty_requirements_is_testable():
    result = DEFAULT_REGISTRY.evaluate([])
    assert result.status == FeasibilityStatus.TESTABLE


# ─── determinism ──────────────────────────────────────────────────────────────

def test_same_inputs_produce_same_status():
    req = DataRequirement(requirement_id="r", data_kind=DataKind.SYNTHETIC_PARAMETRIC)
    r1 = DEFAULT_REGISTRY.evaluate([req])
    r2 = DEFAULT_REGISTRY.evaluate([req])
    assert r1.status == r2.status
    assert r1.registry_fingerprint == r2.registry_fingerprint
    assert r1.satisfied_ids == r2.satisfied_ids
    assert r1.reason_codes == r2.reason_codes


def test_different_registries_different_fingerprints():
    reg_empty = CapabilityRegistry([])
    reg_full = build_v1_registry()
    assert reg_empty.fingerprint != reg_full.fingerprint


# ─── registry fingerprint in results ─────────────────────────────────────────

def test_feasibility_result_contains_registry_fingerprint():
    result = DEFAULT_REGISTRY.evaluate([])
    assert result.registry_fingerprint == DEFAULT_REGISTRY.fingerprint
    assert result.registry_version == REGISTRY_VERSION


# ─── future integration contract ──────────────────────────────────────────────

def test_not_testable_does_not_mean_hypothesis_rejected():
    """MISSING_REQUIREMENT != scientifically invalid hypothesis.
    The FeasibilityResult preserves detail so a future system can distinguish
    'missing data capability' from 'bad science'.
    """
    r_bad = DataRequirement(requirement_id="ohlcv", data_kind=DataKind.OHLCV)
    result = DEFAULT_REGISTRY.evaluate([r_bad])
    # NOT_TESTABLE — but the reason is infrastructure, not science
    assert result.status == FeasibilityStatus.NOT_TESTABLE
    assert FeasibilityReasonCode.NO_MATCHING_DATA_KIND in result.reason_codes
    # The unsatisfied requirement is preserved for future data-acquisition tracking
    assert "ohlcv" in result.unsatisfied_ids


# ─── CLI smoke test ───────────────────────────────────────────────────────────

def test_cli_capabilities_command_exits_zero():
    from ai_quant_scientist.cli import main
    rc = main(["capabilities"])
    assert rc == 0


# ─── ToolRequirement ─────────────────────────────────────────────────────────

def test_tool_requirement_satisfied_when_registered():
    req = ToolRequirement(requirement_id="tool", tool_name="stub_backtester_v1")
    result = DEFAULT_REGISTRY.evaluate_tool_requirement(req)
    assert result.satisfied
    assert result.matched_capability is not None
    assert result.matched_capability.capability_id == "stub_backtester_v1"


def test_tool_requirement_fails_when_not_registered():
    req = ToolRequirement(requirement_id="tool", tool_name="databento_backtester")
    result = DEFAULT_REGISTRY.evaluate_tool_requirement(req)
    assert not result.satisfied
    assert FeasibilityReasonCode.TOOL_UNAVAILABLE in result.reason_codes


def test_tool_unavailable_can_be_emitted():
    reg = CapabilityRegistry([])  # empty
    req = ToolRequirement(requirement_id="tool", tool_name="stub_backtester_v1")
    result = reg.evaluate_tool_requirement(req)
    assert FeasibilityReasonCode.TOOL_UNAVAILABLE in result.reason_codes


def test_data_available_but_tool_missing_is_blocked():
    # Registry has the data kind but not the execution tool
    data_cap = _make_cap(capability_id="some_data", capability_type="DATA_FEED")
    reg = CapabilityRegistry([data_cap])
    data_req = DataRequirement(requirement_id="data", data_kind=DataKind.OHLCV)
    tool_req = ToolRequirement(requirement_id="tool", tool_name="my_backtester")
    result = reg.evaluate([data_req, tool_req])
    assert result.status == FeasibilityStatus.NOT_TESTABLE
    assert "tool" in result.unsatisfied_ids
    assert FeasibilityReasonCode.TOOL_UNAVAILABLE in result.reason_codes
    assert "data" in result.satisfied_ids


def test_tool_and_data_both_available():
    cap = _make_cap(capability_id="my_tool", capability_type="EXECUTION_TOOL",
                    data_kind=DataKind.OHLCV)
    reg = CapabilityRegistry([cap])
    data_req = DataRequirement(requirement_id="data", data_kind=DataKind.OHLCV)
    tool_req = ToolRequirement(requirement_id="tool", tool_name="my_tool")
    result = reg.evaluate([data_req, tool_req])
    assert result.status == FeasibilityStatus.TESTABLE


# ─── V0.9 hardening regressions ──────────────────────────────────────────────

def test_supported_parameters_testable():
    req = _synth_req(required_parameters=("signal_threshold", "lookback"))
    result = DEFAULT_REGISTRY.evaluate_requirement(req)
    assert result.satisfied


# ─── ResearchCandidate ───────────────────────────────────────────────────────

def test_research_candidate_requires_hypothesis():
    with pytest.raises(ValueError, match="hypothesis_statement"):
        ResearchCandidate.create(
            hypothesis_statement="",
            hypothesis_rationale="something",
            requirements=[DataRequirement(requirement_id="r", data_kind=DataKind.SYNTHETIC_PARAMETRIC)],
        )


def test_research_candidate_requires_explicit_requirements():
    with pytest.raises(ValueError, match="requirements"):
        ResearchCandidate.create(
            hypothesis_statement="signal predicts direction",
            hypothesis_rationale="rationale",
            requirements=[],  # empty — not inferred from prose
        )


def test_research_candidate_valid_construction():
    req = DataRequirement(requirement_id="r", data_kind=DataKind.SYNTHETIC_PARAMETRIC)
    candidate = ResearchCandidate.create(
        hypothesis_statement="signal predicts direction",
        hypothesis_rationale="based on prior work",
        requirements=[req],
    )
    assert candidate.id
    assert len(candidate.requirements) == 1
    assert candidate.source == "manual"


def test_research_candidate_is_immutable():
    req = DataRequirement(requirement_id="r", data_kind=DataKind.SYNTHETIC_PARAMETRIC)
    candidate = ResearchCandidate.create(
        hypothesis_statement="signal predicts direction",
        hypothesis_rationale="r",
        requirements=[req],
    )
    with pytest.raises((AttributeError, TypeError)):
        candidate.hypothesis_statement = "mutated"  # type: ignore


# ─── ResearchFeasibilityGate ─────────────────────────────────────────────────

def _synthetic_candidate() -> ResearchCandidate:
    return ResearchCandidate.create(
        hypothesis_statement="signal_threshold controls trade frequency",
        hypothesis_rationale="TOO_FEW_TRADES suggests threshold is too strict",
        requirements=[
            DataRequirement(requirement_id="data", data_kind=DataKind.SYNTHETIC_PARAMETRIC,
                            asset_class=AssetClass.SYNTHETIC),
            ToolRequirement(requirement_id="tool", tool_name="stub_backtester_v1"),
        ],
    )


def _ohlcv_mes_candidate() -> ResearchCandidate:
    return ResearchCandidate.create(
        hypothesis_statement="order-book imbalance predicts MES returns",
        hypothesis_rationale="microstructure hypothesis",
        requirements=[
            DataRequirement(requirement_id="ob", data_kind=DataKind.ORDER_BOOK,
                            asset_class=AssetClass.FUTURES, instruments=("MES",),
                            resolution=Resolution.SECOND_1),
            ToolRequirement(requirement_id="tool", tool_name="futures_backtester"),
        ],
    )


def test_synthetic_candidate_is_ready_for_spec():
    gate = ResearchFeasibilityGate()
    decision = gate.evaluate(_synthetic_candidate(), DEFAULT_REGISTRY)
    assert decision.decision == GateDecision.READY_FOR_SPEC
    assert decision.is_ready
    assert not decision.is_blocked


def test_ohlcv_mes_candidate_is_blocked():
    gate = ResearchFeasibilityGate()
    decision = gate.evaluate(_ohlcv_mes_candidate(), DEFAULT_REGISTRY)
    assert decision.decision == GateDecision.BLOCKED_CAPABILITY
    assert decision.is_blocked
    assert not decision.is_ready


def test_gate_records_registry_version_and_fingerprint():
    gate = ResearchFeasibilityGate()
    decision = gate.evaluate(_synthetic_candidate(), DEFAULT_REGISTRY)
    assert decision.registry_version == DEFAULT_REGISTRY.version
    assert decision.registry_fingerprint == DEFAULT_REGISTRY.fingerprint


def test_gate_records_gate_policy_version():
    gate = ResearchFeasibilityGate()
    decision = gate.evaluate(_synthetic_candidate(), DEFAULT_REGISTRY)
    assert decision.gate_version == GATE_VERSION


def test_gate_records_candidate_id():
    candidate = _synthetic_candidate()
    gate = ResearchFeasibilityGate()
    decision = gate.evaluate(candidate, DEFAULT_REGISTRY)
    assert decision.candidate_id == candidate.id


def test_gate_preserves_feasibility_result():
    gate = ResearchFeasibilityGate()
    decision = gate.evaluate(_synthetic_candidate(), DEFAULT_REGISTRY)
    fr = decision.feasibility_result
    assert fr is not None
    assert hasattr(fr, "status")
    assert hasattr(fr, "requirement_results")


def test_gate_blocked_preserves_unsatisfied_requirements():
    gate = ResearchFeasibilityGate()
    decision = gate.evaluate(_ohlcv_mes_candidate(), DEFAULT_REGISTRY)
    fr = decision.feasibility_result
    assert len(fr.unsatisfied_ids) > 0
    assert FeasibilityReasonCode.NO_MATCHING_DATA_KIND in fr.reason_codes


def test_gate_same_input_same_logical_decision():
    gate = ResearchFeasibilityGate()
    c = _synthetic_candidate()
    d1 = gate.evaluate(c, DEFAULT_REGISTRY)
    d2 = gate.evaluate(c, DEFAULT_REGISTRY)
    assert d1.decision == d2.decision
    assert d1.gate_version == d2.gate_version
    assert d1.registry_fingerprint == d2.registry_fingerprint


def test_blocked_candidate_is_not_scientifically_rejected():
    """BLOCKED_CAPABILITY ≠ hypothesis rejected.
    The hypothesis may be scientifically interesting; it just cannot be tested now.
    """
    gate = ResearchFeasibilityGate()
    decision = gate.evaluate(_ohlcv_mes_candidate(), DEFAULT_REGISTRY)
    # The gate says BLOCKED — not REJECTED. The unsatisfied requirements remain
    # visible for future data-acquisition tracking.
    assert decision.decision == GateDecision.BLOCKED_CAPABILITY
    fr = decision.feasibility_result
    assert len(fr.unsatisfied_ids) > 0  # evidence preserved, not discarded


def test_gate_makes_no_network_calls(monkeypatch):
    import urllib.request
    called = []
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **kw: called.append(True))
    gate = ResearchFeasibilityGate()
    gate.evaluate(_synthetic_candidate(), DEFAULT_REGISTRY)
    assert not called


# ─── CLI feasibility-check ─────────────────────────────────────────────────────

def test_cli_feasibility_check_synthetic():
    from ai_quant_scientist.cli import main
    rc = main(["feasibility-check", "--preset", "synthetic"])
    assert rc == 0


def test_cli_feasibility_check_ohlcv():
    from ai_quant_scientist.cli import main
    rc = main(["feasibility-check", "--preset", "ohlcv-mes"])
    assert rc == 0
