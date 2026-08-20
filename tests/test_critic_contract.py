"""Deterministic contract-hardening tests for validate_critic_decision.

All tests use only domain models — zero real API calls.
"""
from __future__ import annotations

import pytest

from ai_quant_scientist.models.critic import (
    CriticDecision,
    CriticDecisionType,
    new_id,
    validate_critic_decision,
)


def _make(
    decision_type: CriticDecisionType,
    parent_spec_id: str | None = "spec-01",
    changes: dict | None = None,
    rationale: str | None = "short rationale",
    prediction: str | None = "a falsifiable prediction",
    confidence: str | None = "medium",
) -> CriticDecision:
    return CriticDecision(
        id=new_id(),
        research_run_id="run-01",
        decision_type=decision_type,
        parent_spec_id=parent_spec_id,
        changes=changes,
        rationale=rationale,
        prediction=prediction,
        confidence=confidence,
    )


PROPOSE = CriticDecisionType.PROPOSE_REVISION
NO_REV = CriticDecisionType.NO_USEFUL_REVISION


# ─── valid decisions pass ─────────────────────────────────────────────────────

def test_valid_propose_revision_low():
    validate_critic_decision(_make(PROPOSE, changes={"signal_threshold": 1.5}, confidence="low"))


def test_valid_propose_revision_medium():
    validate_critic_decision(_make(PROPOSE, changes={"signal_threshold": 1.5}, confidence="medium"))


def test_valid_propose_revision_high():
    validate_critic_decision(_make(PROPOSE, changes={"signal_threshold": 1.5}, confidence="high"))


def test_valid_no_useful_null_confidence():
    validate_critic_decision(_make(NO_REV, changes=None, prediction=None, confidence=None))


def test_valid_no_useful_low_confidence():
    validate_critic_decision(_make(NO_REV, changes=None, prediction=None, confidence="low"))


# ─── invalid decision strings ─────────────────────────────────────────────────

def test_invalid_confidence_moderate():
    with pytest.raises(ValueError, match="confidence"):
        validate_critic_decision(_make(PROPOSE, changes={"signal_threshold": 1.5}, confidence="moderate"))


def test_invalid_confidence_medium_high():
    with pytest.raises(ValueError, match="confidence"):
        validate_critic_decision(_make(PROPOSE, changes={"signal_threshold": 1.5}, confidence="medium-high"))


def test_invalid_confidence_dot_eight():
    with pytest.raises(ValueError, match="confidence"):
        validate_critic_decision(_make(PROPOSE, changes={"signal_threshold": 1.5}, confidence=".8"))


def test_invalid_confidence_zero_point_eight():
    with pytest.raises(ValueError, match="confidence"):
        validate_critic_decision(_make(PROPOSE, changes={"signal_threshold": 1.5}, confidence="0.8"))


def test_invalid_confidence_80_percent():
    with pytest.raises(ValueError, match="confidence"):
        validate_critic_decision(_make(PROPOSE, changes={"signal_threshold": 1.5}, confidence="80%"))


def test_invalid_confidence_60_percent():
    with pytest.raises(ValueError, match="confidence"):
        validate_critic_decision(_make(PROPOSE, changes={"signal_threshold": 1.5}, confidence="60%"))


def test_invalid_confidence_non_english():
    with pytest.raises(ValueError, match="confidence"):
        validate_critic_decision(_make(PROPOSE, changes={"signal_threshold": 1.5}, confidence="低"))


def test_invalid_confidence_none_for_propose():
    # PROPOSE_REVISION requires non-null confidence in the valid vocabulary
    with pytest.raises(ValueError, match="confidence"):
        validate_critic_decision(_make(PROPOSE, changes={"signal_threshold": 1.5}, confidence=None))


# ─── PROPOSE_REVISION cross-field rules ───────────────────────────────────────

def test_propose_without_change_rejected():
    with pytest.raises(ValueError, match="change"):
        validate_critic_decision(_make(PROPOSE, changes=None))


def test_propose_with_empty_changes_rejected():
    with pytest.raises(ValueError, match="change"):
        validate_critic_decision(_make(PROPOSE, changes={}))


def test_propose_with_multiple_changes_rejected():
    with pytest.raises(ValueError, match="change"):
        validate_critic_decision(_make(PROPOSE, changes={"signal_threshold": 1.5, "lookback": 20}))


def test_propose_without_parent_spec_id_rejected():
    with pytest.raises(ValueError, match="parent_spec_id"):
        validate_critic_decision(_make(PROPOSE, changes={"signal_threshold": 1.5}, parent_spec_id=None))


def test_propose_without_rationale_rejected():
    with pytest.raises(ValueError, match="rationale"):
        validate_critic_decision(_make(PROPOSE, changes={"signal_threshold": 1.5}, rationale=None))


def test_propose_with_empty_rationale_rejected():
    with pytest.raises(ValueError, match="rationale"):
        validate_critic_decision(_make(PROPOSE, changes={"signal_threshold": 1.5}, rationale="   "))


def test_propose_without_prediction_rejected():
    with pytest.raises(ValueError, match="prediction"):
        validate_critic_decision(_make(PROPOSE, changes={"signal_threshold": 1.5}, prediction=None))


def test_propose_with_empty_prediction_rejected():
    with pytest.raises(ValueError, match="prediction"):
        validate_critic_decision(_make(PROPOSE, changes={"signal_threshold": 1.5}, prediction=""))


# ─── NO_USEFUL_REVISION cross-field rules ─────────────────────────────────────

def test_no_useful_with_changes_rejected():
    with pytest.raises(ValueError, match="changes"):
        validate_critic_decision(_make(NO_REV, changes={"signal_threshold": 1.5}, prediction=None))


def test_no_useful_invalid_confidence_rejected():
    with pytest.raises(ValueError, match="confidence"):
        validate_critic_decision(_make(NO_REV, changes=None, prediction=None, confidence="moderate"))


def test_no_useful_with_prediction_allowed():
    # The domain does not prohibit prediction on NO_USEFUL_REVISION
    validate_critic_decision(_make(NO_REV, changes=None, prediction="some optional note", confidence=None))
