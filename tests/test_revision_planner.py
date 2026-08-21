"""Comprehensive deterministic tests for RevisionPlanner and RevisionIntent. Zero API calls."""
from __future__ import annotations

import pytest

from ai_quant_scientist.models.revision import (
    ExperimentType,
    PlannerResult,
    RevisionDirection,
    RevisionIntent,
    validate_revision_intent,
)
from ai_quant_scientist.services.revision_planner import (
    PLANNER_VERSION,
    PlannerRejectionError,
    RevisionPlanner,
)


# ─── helpers ─────────────────────────────────────────────────────────────────

def _intent(
    parameter: str = "signal_threshold",
    direction: RevisionDirection = RevisionDirection.DECREASE,
    experiment_type: ExperimentType = ExperimentType.MECHANISTIC_DIAGNOSTIC,
    rationale: str = "test rationale",
    prediction: str = "trade count will change",
    confidence: str = "medium",
    parent_spec_id: str = "spec-01",
    research_run_id: str = "run-01",
) -> RevisionIntent:
    return RevisionIntent(
        id="intent-01",
        research_run_id=research_run_id,
        parent_spec_id=parent_spec_id,
        parameter=parameter,
        direction=direction,
        experiment_type=experiment_type,
        rationale=rationale,
        prediction=prediction,
        confidence=confidence,
    )


_DEFAULT_CONSTRAINTS = {
    "signal_threshold": {"type": "float", "min": -10.0, "max": 10.0, "step": 0.5},
    "lookback": {"type": "int", "min": 1, "max": 365, "step": 5},
}

_SPEC_01 = {"id": "spec-01", "parameters": {"signal_threshold": 2.0, "lookback": 20}}
_SPEC_06 = {"id": "spec-06", "parameters": {"signal_threshold": 2.5, "lookback": 10}}

_planner = RevisionPlanner()


# ─── intent contract validation ──────────────────────────────────────────────

def test_valid_intent_passes():
    validate_revision_intent(_intent())


def test_intent_requires_parent_spec_id():
    with pytest.raises(ValueError, match="parent_spec_id"):
        validate_revision_intent(_intent(parent_spec_id=""))


def test_intent_requires_non_empty_parameter():
    with pytest.raises(ValueError, match="parameter"):
        validate_revision_intent(_intent(parameter=""))


def test_intent_direction_enum_validated():
    # RevisionDirection is an Enum; invalid string cannot be stored in the dataclass
    # by normal construction; test that validate catches a wrong type
    bad = _intent().__class__(
        id="x", research_run_id="r", parent_spec_id="s",
        parameter="signal_threshold", direction="SIDEWAYS",  # type: ignore
        experiment_type=ExperimentType.MECHANISTIC_DIAGNOSTIC,
        rationale="r", prediction="p", confidence="low",
    )
    with pytest.raises(ValueError, match="direction"):
        validate_revision_intent(bad)


def test_intent_experiment_type_validated():
    bad = _intent().__class__(
        id="x", research_run_id="r", parent_spec_id="s",
        parameter="signal_threshold", direction=RevisionDirection.DECREASE,
        experiment_type="UNKNOWN_TYPE",  # type: ignore
        rationale="r", prediction="p", confidence="low",
    )
    with pytest.raises(ValueError, match="experiment_type"):
        validate_revision_intent(bad)


def test_intent_requires_non_empty_rationale():
    with pytest.raises(ValueError, match="rationale"):
        validate_revision_intent(_intent(rationale="   "))


def test_intent_requires_non_empty_prediction():
    with pytest.raises(ValueError, match="prediction"):
        validate_revision_intent(_intent(prediction=""))


def test_intent_confidence_must_be_valid():
    with pytest.raises(ValueError, match="confidence"):
        validate_revision_intent(_intent(confidence="80%"))

    with pytest.raises(ValueError, match="confidence"):
        validate_revision_intent(_intent(confidence="moderate"))

    validate_revision_intent(_intent(confidence="low"))
    validate_revision_intent(_intent(confidence="medium"))
    validate_revision_intent(_intent(confidence="high"))


def test_no_exact_target_value_in_intent():
    """RevisionIntent has no 'to' or 'target_value' field."""
    fields = [f.name for f in _intent().__dataclass_fields__.values()]
    assert "to" not in fields
    assert "target_value" not in fields


# ─── planner: DECREASE selects nearest lower candidate ───────────────────────

def test_decrease_selects_nearest_lower_float():
    intent = _intent(parameter="signal_threshold", direction=RevisionDirection.DECREASE)
    result = _planner.plan(intent, _SPEC_01, _DEFAULT_CONSTRAINTS, [])
    assert result.rejection_reason is None
    assert result.planned_change == {"signal_threshold": 1.5}  # 2.0 - 0.5


def test_increase_selects_nearest_upper_int():
    intent = _intent(parameter="lookback", direction=RevisionDirection.INCREASE)
    result = _planner.plan(intent, _SPEC_06, _DEFAULT_CONSTRAINTS, [])
    assert result.rejection_reason is None
    assert result.planned_change == {"lookback": 15}  # 10 + 5


def test_current_value_excluded():
    intent = _intent(parameter="signal_threshold", direction=RevisionDirection.DECREASE)
    result = _planner.plan(intent, _SPEC_01, _DEFAULT_CONSTRAINTS, [])
    # 2.0 must never be proposed
    assert result.planned_change != {"signal_threshold": 2.0}


def test_out_of_bounds_candidate_excluded():
    # force spec at the minimum edge
    spec = {"id": "s", "parameters": {"signal_threshold": -9.5, "lookback": 20}}
    intent = _intent(parameter="signal_threshold", direction=RevisionDirection.DECREASE)
    result = _planner.plan(intent, spec, _DEFAULT_CONSTRAINTS, [])
    # only -10.0 is left; it's within bounds so it should be selected
    assert result.rejection_reason is None
    assert result.planned_change == {"signal_threshold": -10.0}


def test_fully_below_min_fails_closed():
    spec = {"id": "s", "parameters": {"signal_threshold": -10.0, "lookback": 20}}
    intent = _intent(parameter="signal_threshold", direction=RevisionDirection.DECREASE)
    result = _planner.plan(intent, spec, _DEFAULT_CONSTRAINTS, [])
    assert result.rejection_reason is not None
    assert result.planned_change is None


# ─── planner: previously tested candidate skipped ────────────────────────────

def test_previously_tested_candidate_skipped():
    # Lineage has lookback=15 already tested → planner should select 20
    intent = _intent(parameter="lookback", direction=RevisionDirection.INCREASE)
    lineage = [{"id": "spec-old", "parameters": {"signal_threshold": 2.5, "lookback": 15}}]
    result = _planner.plan(intent, _SPEC_06, _DEFAULT_CONSTRAINTS, lineage)
    assert result.rejection_reason is None
    assert result.planned_change == {"lookback": 20}
    assert 15 in result.tested_values_skipped


def test_next_candidate_selected_if_nearest_tested():
    # both 15 and 20 tested → should select 25
    intent = _intent(parameter="lookback", direction=RevisionDirection.INCREASE)
    lineage = [
        {"id": "a", "parameters": {"signal_threshold": 2.5, "lookback": 15}},
        {"id": "b", "parameters": {"signal_threshold": 2.5, "lookback": 20}},
    ]
    result = _planner.plan(intent, _SPEC_06, _DEFAULT_CONSTRAINTS, lineage)
    assert result.planned_change == {"lookback": 25}


def test_no_legal_candidate_fails_closed():
    # fill all candidates for DECREASE on signal_threshold from 2.0 down to min
    intent = _intent(parameter="signal_threshold", direction=RevisionDirection.DECREASE)
    # step=0.5 from 2.0 down to -10.0 → 24 candidates
    candidates = [round(2.0 - 0.5 * i, 10) for i in range(1, 25)]
    current_params = {"signal_threshold": 2.0, "lookback": 20}
    lineage = [{"id": f"s{i}", "parameters": {**current_params, "signal_threshold": c}} for i, c in enumerate(candidates)]
    result = _planner.plan(intent, _SPEC_01, _DEFAULT_CONSTRAINTS, lineage)
    assert result.rejection_reason is not None
    assert result.planned_change is None


# ─── planner: wrong parameter / type errors ──────────────────────────────────

def test_wrong_parameter_rejected():
    intent = _intent(parameter="volatility_band")  # not in constraints
    result = _planner.plan(intent, _SPEC_01, _DEFAULT_CONSTRAINTS, [])
    assert "parameter_not_in_constraints" in result.rejection_reason


def test_parameter_not_in_spec_rejected():
    spec = {"id": "s", "parameters": {"lookback": 20}}  # no signal_threshold
    intent = _intent(parameter="signal_threshold")
    result = _planner.plan(intent, spec, _DEFAULT_CONSTRAINTS, [])
    assert "parameter_not_in_current_spec" in result.rejection_reason


def test_float_without_step_fails_closed():
    constraints = {"signal_threshold": {"type": "float", "min": -10.0, "max": 10.0}}  # no step
    intent = _intent(parameter="signal_threshold")
    result = _planner.plan(intent, _SPEC_01, constraints, [])
    assert "no_step_policy_for_continuous_parameter" in result.rejection_reason


def test_int_without_step_defaults_to_1():
    constraints = {"lookback": {"type": "int", "min": 1, "max": 365}}  # no step
    intent = _intent(parameter="lookback", direction=RevisionDirection.INCREASE)
    result = _planner.plan(intent, _SPEC_06, constraints, [])
    assert result.rejection_reason is None
    assert result.planned_change == {"lookback": 11}  # 10 + 1 (default step)


# ─── planner: complete-spec duplicate detection ──────────────────────────────

def test_complete_spec_duplicate_detected():
    """Planner detects a full-spec duplicate, not just a single value match."""
    intent = _intent(parameter="signal_threshold", direction=RevisionDirection.DECREASE)
    # lineage has signal_threshold=1.5 but with a DIFFERENT lookback
    lineage = [{"id": "x", "parameters": {"signal_threshold": 1.5, "lookback": 99}}]
    # _SPEC_01 has lookback=20, so {signal_threshold:1.5, lookback:20} was NOT tested
    result = _planner.plan(intent, _SPEC_01, _DEFAULT_CONSTRAINTS, lineage)
    assert result.planned_change == {"signal_threshold": 1.5}  # not a duplicate

    # now add the full spec that would be created
    lineage2 = lineage + [{"id": "y", "parameters": {"signal_threshold": 1.5, "lookback": 20}}]
    result2 = _planner.plan(intent, _SPEC_01, _DEFAULT_CONSTRAINTS, lineage2)
    assert 1.5 in result2.tested_values_skipped
    assert result2.planned_change == {"signal_threshold": 1.0}


# ─── planner: determinism ─────────────────────────────────────────────────────

def test_same_inputs_produce_same_output():
    intent = _intent(parameter="lookback", direction=RevisionDirection.INCREASE)
    r1 = _planner.plan(intent, _SPEC_06, _DEFAULT_CONSTRAINTS, [])
    r2 = _planner.plan(intent, _SPEC_06, _DEFAULT_CONSTRAINTS, [])
    assert r1.planned_change == r2.planned_change
    assert r1.planner_version == r2.planner_version


def test_planner_version_recorded():
    result = _planner.plan(
        _intent(parameter="lookback", direction=RevisionDirection.INCREASE),
        _SPEC_06, _DEFAULT_CONSTRAINTS, [],
    )
    assert result.planner_version == PLANNER_VERSION


def test_planner_makes_no_network_calls(monkeypatch):
    """Patch urllib to confirm planner never touches the network."""
    import urllib.request
    called = []

    def die(*a, **kw):
        called.append(True)
        raise AssertionError("planner made a network call")

    monkeypatch.setattr(urllib.request, "urlopen", die)
    _planner.plan(
        _intent(parameter="lookback", direction=RevisionDirection.INCREASE),
        _SPEC_06, _DEFAULT_CONSTRAINTS, [],
    )
    assert not called


# ─── planner: PERTURB direction ───────────────────────────────────────────────

def test_perturb_selects_nearest_overall():
    # With signal_threshold=2.0, nearest up is 2.5, nearest down is 1.5
    intent = _intent(parameter="signal_threshold", direction=RevisionDirection.PERTURB)
    result = _planner.plan(intent, _SPEC_01, _DEFAULT_CONSTRAINTS, [])
    # INCREASE preferred on tie; both are equidistant (0.5 each), so 2.5
    assert result.planned_change in ({"signal_threshold": 1.5}, {"signal_threshold": 2.5})
    assert result.rejection_reason is None


def test_perturb_falls_back_when_nearest_tested():
    # 2.5 already tested → should try 1.5 or 3.0
    intent = _intent(parameter="signal_threshold", direction=RevisionDirection.PERTURB)
    lineage = [{"id": "x", "parameters": {"signal_threshold": 2.5, "lookback": 20}}]
    result = _planner.plan(intent, _SPEC_01, _DEFAULT_CONSTRAINTS, lineage)
    assert result.planned_change is not None
    val = list(result.planned_change.values())[0]
    assert val != 2.5


# ─── integration fixtures (spec-equivalent to TOO_FEW_TRADES and LOOKBACK_SENSITIVITY)

def test_integration_too_few_trades_decrease_selects_1_5():
    """Fixture equivalent to case-02 (TOO_FEW_TRADES, signal_threshold=2.0)."""
    spec = {"id": "spec-02", "parameters": {"signal_threshold": 2.0, "lookback": 20}}
    intent = _intent(
        parameter="signal_threshold",
        direction=RevisionDirection.DECREASE,
        experiment_type=ExperimentType.MECHANISTIC_DIAGNOSTIC,
        rationale="TOO_FEW_TRADES: lower threshold expected to increase trade frequency",
        prediction="trade count will increase",
    )
    result = _planner.plan(intent, spec, _DEFAULT_CONSTRAINTS, [])
    assert result.planned_change == {"signal_threshold": 1.5}


def test_integration_lookback_sensitivity_increase_selects_15():
    """Fixture equivalent to case-06 (LOOKBACK_SENSITIVITY, lookback=10)."""
    spec = {"id": "spec-06", "parameters": {"signal_threshold": 2.5, "lookback": 10}}
    intent = _intent(
        parameter="lookback",
        direction=RevisionDirection.INCREASE,
        experiment_type=ExperimentType.PARAMETER_SENSITIVITY,
        rationale="LOOKBACK_SENSITIVITY: increase isolates sensitivity to estimation window",
        prediction="trade count and performance will differ",
    )
    result = _planner.plan(intent, spec, _DEFAULT_CONSTRAINTS, [])
    assert result.planned_change == {"lookback": 15}


def test_integration_lookback_15_tested_selects_20():
    """If lookback=15 was already tested, planner selects 20."""
    spec = {"id": "spec-06", "parameters": {"signal_threshold": 2.5, "lookback": 10}}
    intent = _intent(
        parameter="lookback",
        direction=RevisionDirection.INCREASE,
        experiment_type=ExperimentType.PARAMETER_SENSITIVITY,
        rationale="sensitivity",
        prediction="will reveal",
    )
    lineage = [{"id": "prev", "parameters": {"signal_threshold": 2.5, "lookback": 15}}]
    result = _planner.plan(intent, spec, _DEFAULT_CONSTRAINTS, lineage)
    assert result.planned_change == {"lookback": 20}


def test_integration_all_candidates_tested_rejects():
    """If all candidates up from lookback=10 are tested, planner rejects."""
    spec = {"id": "spec-06", "parameters": {"signal_threshold": 2.5, "lookback": 10}}
    intent = _intent(
        parameter="lookback",
        direction=RevisionDirection.INCREASE,
        experiment_type=ExperimentType.PARAMETER_SENSITIVITY,
        rationale="sensitivity",
        prediction="will reveal",
    )
    # Exhaust all candidates: 15, 20, 25, ..., 365 (step 5 from 10)
    lineage = [
        {"id": f"s{i}", "parameters": {"signal_threshold": 2.5, "lookback": v}}
        for i, v in enumerate(range(15, 366, 5))
    ]
    result = _planner.plan(intent, spec, _DEFAULT_CONSTRAINTS, lineage)
    assert result.rejection_reason is not None
    assert result.planned_change is None


# ─── PlannerRejectionError ────────────────────────────────────────────────────

def test_planner_rejection_error_contains_result():
    """PlannerRejectionError wraps the PlannerResult for downstream inspection."""
    spec = {"id": "s", "parameters": {"volatility": 1.0}}  # not in constraints
    intent = _intent(parameter="volatility")
    result = _planner.plan(intent, spec, _DEFAULT_CONSTRAINTS, [])
    err = PlannerRejectionError(result.rejection_reason, result)
    assert err.reason == result.rejection_reason
    assert err.result is result


# ─── CriticDecision carries planner provenance ───────────────────────────────

def test_critic_decision_carries_planner_version():
    """After the intent architecture, CriticDecision.planner_version is set by adapters."""
    from ai_quant_scientist.models.critic import CriticDecision, CriticDecisionType
    from ai_quant_scientist.models.critic import new_id
    d = CriticDecision(
        id=new_id(), research_run_id="r",
        decision_type=CriticDecisionType.PROPOSE_REVISION,
        parent_spec_id="s",
        changes={"signal_threshold": 1.5},
        rationale="r", prediction="p", confidence="low",
        planner_version=PLANNER_VERSION,
    )
    assert d.planner_version == PLANNER_VERSION


def test_critic_decision_revision_intent_field_exists():
    from ai_quant_scientist.models.critic import CriticDecision, CriticDecisionType
    from ai_quant_scientist.models.critic import new_id
    import dataclasses
    d = CriticDecision(
        id=new_id(), research_run_id="r",
        decision_type=CriticDecisionType.NO_USEFUL_REVISION,
        parent_spec_id=None, changes=None,
        rationale="n", prediction=None, confidence=None,
        revision_intent=None,
    )
    raw = dataclasses.asdict(d)
    assert "revision_intent" in raw
    assert "planner_version" in raw
