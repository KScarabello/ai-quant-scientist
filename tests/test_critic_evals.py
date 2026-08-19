from __future__ import annotations

import os
from ai_quant_scientist.evals.critic_eval import load_cases_from_file, CriticEvalSuite, CriticEvalCase, CriticEvalResult
from ai_quant_scientist.models.critic import CriticDecision, CriticDecisionType


class AlwaysIllegalCritic:
    provider = "fake"
    model = "illegal-v1"

    def critique(self, context):
        # propose an unknown parameter change
        return CriticDecision(
            id="illegal-1",
            research_run_id=context.research_run_id,
            decision_type=CriticDecisionType.PROPOSE_REVISION,
            parent_spec_id=context.current_spec.get("id"),
            changes={"magic_indicator": 42},
            rationale="add magic",
            prediction=None,
            confidence=None,
        )


class AlwaysValidCritic:
    provider = "fake"
    model = "valid-v1"

    def critique(self, context):
        # propose a single bounded change to signal_threshold
        cur = context.current_spec.get("parameters", {})
        old = float(cur.get("signal_threshold", 2.0))
        return CriticDecision(
            id="valid-1",
            research_run_id=context.research_run_id,
            decision_type=CriticDecisionType.PROPOSE_REVISION,
            parent_spec_id=context.current_spec.get("id"),
            changes={"signal_threshold": round(old - 0.5, 3)},
            rationale="lower threshold to increase observations",
            prediction="trade_count expected to increase",
            confidence="low",
        )


class AlwaysNoUsefulCritic:
    provider = "fake"
    model = "nouseful-v1"

    def critique(self, context):
        return CriticDecision(
            id="nouse-1",
            research_run_id=context.research_run_id,
            decision_type=CriticDecisionType.NO_USEFUL_REVISION,
            parent_spec_id=context.current_spec.get("id"),
            changes=None,
            rationale="no bounded revision justified",
            prediction=None,
            confidence=None,
        )


def test_fixture_loading_and_uniqueness():
    path = os.path.join(os.getcwd(), "evals", "critic_v1.json")
    cases = load_cases_from_file(path)
    assert len(cases) == 15
    ids = [c.id for c in cases]
    assert len(set(ids)) == len(ids)


def test_internal_consistency():
    path = os.path.join(os.getcwd(), "evals", "critic_v1.json")
    cases = load_cases_from_file(path)
    for c in cases:
        spec = c.context.get("current_spec", {})
        assert "parameters" in spec


def test_contract_hard_failure_detection():
    path = os.path.join(os.getcwd(), "evals", "critic_v1.json")
    cases = load_cases_from_file(path)
    suite = CriticEvalSuite(cases)
    # run first case with illegal critic
    res = suite.run(AlwaysIllegalCritic(), prompt_version="v1")
    # There should be recorded hard failures for case-01 when illegal used
    assert any(r.contract_passed is False and r.hard_failures for r in res)


def test_valid_output_contract_passes():
    path = os.path.join(os.getcwd(), "evals", "critic_v1.json")
    cases = load_cases_from_file(path)
    suite = CriticEvalSuite(cases[:1])
    res = suite.run(AlwaysValidCritic(), prompt_version="v1")
    assert res[0].contract_passed is True


def test_no_useful_revision_scored():
    path = os.path.join(os.getcwd(), "evals", "critic_v1.json")
    cases = load_cases_from_file(path)
    suite = CriticEvalSuite(cases[:1])
    res = suite.run(AlwaysNoUsefulCritic(), prompt_version="v1")
    assert res[0].contract_passed is True


def test_repeated_spec_detection():
    # craft a case where prior_lineage contains same param value
    path = os.path.join(os.getcwd(), "evals", "critic_v1.json")
    cases = load_cases_from_file(path)
    case = cases[12]  # case-13 has prior with 2.5

    class RepeatCritic:
        provider = "fake"
        model = "repeat-v1"

        def critique(self, context):
            return CriticDecision(
                id="repeat-1",
                research_run_id=context.research_run_id,
                decision_type=CriticDecisionType.PROPOSE_REVISION,
                parent_spec_id=context.current_spec.get("id"),
                changes={"signal_threshold": 2.5},
                rationale="repeat test",
                prediction=None,
                confidence=None,
            )

    suite = CriticEvalSuite([case])
    res = suite.run(RepeatCritic(), prompt_version="v1")
    # Expect a hard failure REPEATS_IDENTICAL_SPEC or similar
    assert any("REPEATS_IDENTICAL_SPEC" in h or "REPEATS_IDENTICAL_SPEC" == h for r in res for h in r.hard_failures) or any(r.contract_passed is False for r in res)


def test_fake_critic_suite_run_all_cases():
    # Scripted critic: for MINIMUM_SHARPE_NOT_MET propose lowering threshold, else NO_USEFUL
    class ScriptedCritic:
        provider = "fake"
        model = "scripted-v1"

        def critique(self, context):
            reasons = context.evaluation.get("reason_codes", [])
            params = context.current_spec.get("parameters", {})
            if "MINIMUM_SHARPE_NOT_MET" in reasons and "signal_threshold" in params:
                old = float(params.get("signal_threshold", 2.0))
                return CriticDecision(id="s-"+context.id, research_run_id=context.research_run_id, decision_type=CriticDecisionType.PROPOSE_REVISION, parent_spec_id=context.current_spec.get("id"), changes={"signal_threshold": round(old-0.5,3)}, rationale="scripted", prediction="trade count up", confidence="low")
            return CriticDecision(id="s-"+context.id, research_run_id=context.research_run_id, decision_type=CriticDecisionType.NO_USEFUL_REVISION, parent_spec_id=context.current_spec.get("id"), changes=None, rationale="none", prediction=None, confidence=None)

    path = os.path.join(os.getcwd(), "evals", "critic_v1.json")
    cases = load_cases_from_file(path)
    suite = CriticEvalSuite(cases)
    results = suite.run(ScriptedCritic(), prompt_version="v1")
    assert len(results) == 15
