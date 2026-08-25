from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from ai_quant_scientist.capabilities import AssetClass, DataKind, DataRequirement, ToolKind, ToolRequirement
from ai_quant_scientist.capabilities.gate import ResearchCandidate
from ai_quant_scientist.models.design import (
    AnalysisIntent,
    ComparisonIntent,
    DesignOutcome,
    DesignVariable,
    ExpectedDirection,
    ExperimentCondition,
    ExperimentConditionRole,
    InitialExperimentCompletionRule,
    InitialExperimentPlan,
    OutcomeContrast,
    OutcomePrediction,
    ParameterSensitivityContrastResult,
    ResearchDesignIntent,
    ResearchDesignKind,
    ResearchPredictionPlan,
    ScientificVerdictStatus,
)
from ai_quant_scientist.models.hypothesis_scientist import (
    HYPOTHESIS_CLAIM_SET_CONTRACT_VERSION,
    HypothesisClaimAggregation,
    HypothesisClaimSet,
    HypothesisScientistInvocation,
    ResearchBrief,
    ResearchScope,
    ResearchScopeOutcomeAggregation,
)
from ai_quant_scientist.models.post_verdict_critic import (
    PostVerdictCriticDecision,
    PostVerdictCriticDecisionType,
    PostVerdictCriticInvocation,
    PostVerdictRevisionKind,
)
from ai_quant_scientist.models.research import new_id
from ai_quant_scientist.models.research_designer import ResearchDesignerInvocation
from ai_quant_scientist.services.hypothesis_claim_ontology import build_hypothesis_claim_ontology_snapshot
from ai_quant_scientist.services.hypothesis_scientist import brief_to_json
from ai_quant_scientist.services.post_verdict_research_critic import GovernedPostVerdictResearchCritic
from ai_quant_scientist.services.post_verdict_research_critic_prompts import (
    CURRENT_POST_VERDICT_RESEARCH_CRITIC_PROMPT_VERSION,
    get_post_verdict_research_critic_prompt_hash,
)
from ai_quant_scientist.services.research_design_ontology import (
    RESEARCH_PREDICTION_PLAN_CONTRACT_VERSION,
    build_current_research_design_ontology_snapshot,
)
from ai_quant_scientist.services.scientific_verdict import ScientificVerdictEvaluator
from ai_quant_scientist.storage.sqlite_store import SQLiteStore


def _store(tmp_path) -> SQLiteStore:
    return SQLiteStore(tmp_path / "test.db")


class CountingCritic:
    provider = "fake"
    model = "fake-post-verdict-v1"
    prompt_version = CURRENT_POST_VERDICT_RESEARCH_CRITIC_PROMPT_VERSION

    def __init__(
        self,
        *,
        decision: PostVerdictCriticDecisionType = PostVerdictCriticDecisionType.CONTINUE,
        revision_kind: PostVerdictRevisionKind = PostVerdictRevisionKind.MECHANISM_REVISION,
        diagnosis: str = (
            "The deterministic verdict contradicted both precommitted claims, suggesting the proposed filtering "
            "mechanism was not supported under the frozen scope."
        ),
        next_step_rationale: str = (
            "A bounded mechanism-focused follow-up under the same frozen scope may still be defensible if the next "
            "scientist revisits why stricter filtering would improve trade quality rather than merely reduce activity."
        ),
    ) -> None:
        self.calls = 0
        self.last_verdict_id: str | None = None
        self._decision = decision
        self._revision_kind = revision_kind
        self._diagnosis = diagnosis
        self._next_step_rationale = next_step_rationale

    def critique(self, context) -> PostVerdictCriticDecision:
        self.calls += 1
        self.last_verdict_id = context.scientific_verdict_id
        return PostVerdictCriticDecision(
            id=new_id(),
            scientific_verdict_id=context.scientific_verdict_id,
            decision=self._decision,
            revision_kind=self._revision_kind,
            diagnosis=self._diagnosis,
            next_step_rationale=self._next_step_rationale,
            provider=self.provider,
            model=self.model,
            prompt_version=self.prompt_version,
            raw_response='{"fake":"response"}',
        )


class RaisingCritic:
    provider = "fake"
    model = "fake-post-verdict-v1"
    prompt_version = CURRENT_POST_VERDICT_RESEARCH_CRITIC_PROMPT_VERSION

    def __init__(self, message: str = "provider exploded") -> None:
        self.calls = 0
        self.message = message

    def critique(self, context) -> PostVerdictCriticDecision:
        self.calls += 1
        raise RuntimeError(self.message)


def _candidate(label: str) -> ResearchCandidate:
    return ResearchCandidate.create(
        hypothesis_statement=(
            f"{label}: a stricter signal threshold should lower trade frequency and improve risk-adjusted performance."
        ),
        hypothesis_rationale=(
            "Filtering weaker signal realizations should reduce trade_count while improving Sharpe "
            "under fixed lookback."
        ),
        requirements=[
            DataRequirement(
                requirement_id="data",
                data_kind=DataKind.SYNTHETIC_PARAMETRIC,
                asset_class=AssetClass.SYNTHETIC,
            ),
            ToolRequirement(
                requirement_id="tool",
                tool_kind=ToolKind.BACKTEST_EXECUTION,
            ),
        ],
    )


def _brief(label: str) -> ResearchBrief:
    return ResearchBrief.create(
        research_question=f"{label}: does tightening signal threshold change trade_count and sharpe?",
        asset_class_focus="SYNTHETIC",
        methodological_constraints=[
            "Use bounded synthetic evidence only.",
            "Do not expose exact execution values to AI.",
        ],
        research_scope=ResearchScope.create(
            independent_variable=DesignVariable.SIGNAL_THRESHOLD,
            requested_outcomes=[DesignOutcome.TRADE_COUNT, DesignOutcome.SHARPE],
            outcome_aggregation=ResearchScopeOutcomeAggregation.ALL_OUTCOMES_REQUIRED,
        ),
        source=f"test-{label}",
    )


def _claim_set(candidate_id: str, invocation_id: str) -> HypothesisClaimSet:
    ontology = build_hypothesis_claim_ontology_snapshot()
    return HypothesisClaimSet(
        id=new_id(),
        candidate_id=candidate_id,
        hypothesis_scientist_invocation_id=invocation_id,
        independent_variable=DesignVariable.SIGNAL_THRESHOLD,
        independent_variable_direction=ExpectedDirection.INCREASE,
        claims=(
            OutcomePrediction(
                outcome=DesignOutcome.TRADE_COUNT,
                expected_direction=ExpectedDirection.DECREASE,
            ),
            OutcomePrediction(
                outcome=DesignOutcome.SHARPE,
                expected_direction=ExpectedDirection.INCREASE,
            ),
        ),
        claim_aggregation=HypothesisClaimAggregation.ALL_CLAIMS_REQUIRED,
        claim_contract_version=HYPOTHESIS_CLAIM_SET_CONTRACT_VERSION,
        ontology_version=ontology.version,
        ontology_fingerprint=ontology.fingerprint,
    )


def _design_intent(candidate_id: str) -> ResearchDesignIntent:
    ontology = build_current_research_design_ontology_snapshot()
    return ResearchDesignIntent.create(
        candidate_id=candidate_id,
        design_kind=ResearchDesignKind.PARAMETER_SENSITIVITY,
        independent_variables=(DesignVariable.SIGNAL_THRESHOLD,),
        dependent_outcomes=(DesignOutcome.SHARPE, DesignOutcome.TRADE_COUNT),
        controls=(DesignVariable.LOOKBACK,),
        comparison_intent=ComparisonIntent.CONTRAST_PARAMETER_LEVELS,
        analysis_intent=AnalysisIntent.SENSITIVITY_ANALYSIS,
        falsification_condition=(
            "The hypothesis is falsified if a stricter threshold does not yield lower trade frequency "
            "and higher risk-adjusted performance."
        ),
        rationale="Bounded threshold-sensitivity design over the frozen scope.",
        source="research_designer_v3:fake:fake-v1",
        provider="fake",
        model="fake-v1",
        prompt_version="v3",
        ontology_version=ontology.version,
        ontology_fingerprint=ontology.fingerprint,
    )


def _designer_invocation(candidate_id: str, claim_set_id: str, design_intent_id: str) -> ResearchDesignerInvocation:
    ontology = build_current_research_design_ontology_snapshot()
    return ResearchDesignerInvocation(
        id=new_id(),
        candidate_id=candidate_id,
        hypothesis_claim_set_id=claim_set_id,
        candidate_snapshot_json="{}",
        candidate_feasibility_decision_id="ready-1",
        prompt_version="v3",
        ontology_version=ontology.version,
        ontology_fingerprint=ontology.fingerprint,
        intent_contract_version="research_design_intent_v1",
        provider="fake",
        model="fake-v1",
        raw_response=None,
        parsed_decision_json='{"decision_type":"DESIGN"}',
        validation_status="VALID",
        validation_errors_json=None,
        resulting_design_intent_id=design_intent_id,
    )


def _prediction_plan(candidate_id: str, claim_set_id: str, design_intent_id: str, invocation_id: str) -> ResearchPredictionPlan:
    ontology = build_current_research_design_ontology_snapshot()
    return ResearchPredictionPlan(
        id=new_id(),
        candidate_id=candidate_id,
        hypothesis_claim_set_id=claim_set_id,
        design_intent_id=design_intent_id,
        research_designer_invocation_id=invocation_id,
        prediction_contract_version=RESEARCH_PREDICTION_PLAN_CONTRACT_VERSION,
        ontology_version=ontology.version,
        ontology_fingerprint=ontology.fingerprint,
        independent_variable=DesignVariable.SIGNAL_THRESHOLD,
        predictions=(
            OutcomePrediction(
                outcome=DesignOutcome.TRADE_COUNT,
                expected_direction=ExpectedDirection.DECREASE,
            ),
            OutcomePrediction(
                outcome=DesignOutcome.SHARPE,
                expected_direction=ExpectedDirection.INCREASE,
            ),
        ),
    )


def _plan(candidate_id: str, design_intent_id: str, prediction_plan_id: str) -> InitialExperimentPlan:
    return InitialExperimentPlan(
        id=new_id(),
        candidate_id=candidate_id,
        design_intent_id=design_intent_id,
        candidate_feasibility_decision_id="ready-1",
        selected_capability_id="stub_backtester_v1",
        design_kind=ResearchDesignKind.PARAMETER_SENSITIVITY,
        independent_variable=DesignVariable.SIGNAL_THRESHOLD,
        control_variables=(DesignVariable.LOOKBACK,),
        dependent_outcomes=(DesignOutcome.SHARPE, DesignOutcome.TRADE_COUNT),
        ordered_conditions=(
            ExperimentCondition(
                id=new_id(),
                ordinal=1,
                role=ExperimentConditionRole.BASELINE,
                exact_parameters={"signal_threshold": 2.0, "lookback": 20},
                selected_capability_id="stub_backtester_v1",
                expected_tool_kind="BACKTEST_EXECUTION",
            ),
            ExperimentCondition(
                id=new_id(),
                ordinal=2,
                role=ExperimentConditionRole.COMPARATOR,
                exact_parameters={"signal_threshold": 2.5, "lookback": 20},
                selected_capability_id="stub_backtester_v1",
                expected_tool_kind="BACKTEST_EXECUTION",
            ),
        ),
        completion_rule=InitialExperimentCompletionRule.ALL_CONDITIONS_REQUIRED,
        materializer_version="spec_materializer_v2",
        materialization_policy_version="stub_spec_materialization_policy_v2",
        materialization_policy_fingerprint="policy-fingerprint",
        registry_version="capability_registry_v1",
        registry_fingerprint="registry-fingerprint",
        research_prediction_plan_id=prediction_plan_id,
    )


def _contrast(plan: InitialExperimentPlan, *, trade_count: tuple[float, float], sharpe: tuple[float, float]) -> ParameterSensitivityContrastResult:
    return ParameterSensitivityContrastResult(
        id=new_id(),
        plan_id=plan.id,
        independent_variable=DesignVariable.SIGNAL_THRESHOLD,
        baseline_condition_id=plan.ordered_conditions[0].id,
        comparator_condition_id=plan.ordered_conditions[1].id,
        baseline_parameter_value=2.0,
        comparator_parameter_value=2.5,
        outcomes=(
            OutcomeContrast(
                outcome=DesignOutcome.TRADE_COUNT,
                baseline_value=trade_count[0],
                comparator_value=trade_count[1],
                delta=trade_count[1] - trade_count[0],
                baseline_condition_id=plan.ordered_conditions[0].id,
                comparator_condition_id=plan.ordered_conditions[1].id,
            ),
            OutcomeContrast(
                outcome=DesignOutcome.SHARPE,
                baseline_value=sharpe[0],
                comparator_value=sharpe[1],
                delta=sharpe[1] - sharpe[0],
                baseline_condition_id=plan.ordered_conditions[0].id,
                comparator_condition_id=plan.ordered_conditions[1].id,
            ),
        ),
    )


def _persist_chain(store: SQLiteStore, *, label: str, trade_count=(4.0, 4.0), sharpe=(1.0, 0.75)) -> dict:
    candidate = _candidate(label)
    brief = _brief(label)
    invocation = HypothesisScientistInvocation(
        id=new_id(),
        research_brief_id=brief.id,
        research_brief_snapshot=brief_to_json(brief),
        prompt_version="v5",
        provider="fake",
        model="fake-scientist",
        raw_response=None,
        parsed_decision_json='{"decision_type":"PROPOSE_HYPOTHESIS"}',
        validation_status="VALID",
        validation_errors_json=None,
        resulting_candidate_id=candidate.id,
        resulting_claim_set_id=None,
    )
    claim_set = _claim_set(candidate.id, invocation.id)
    invocation = HypothesisScientistInvocation(
        id=invocation.id,
        research_brief_id=invocation.research_brief_id,
        research_brief_snapshot=invocation.research_brief_snapshot,
        prompt_version=invocation.prompt_version,
        provider=invocation.provider,
        model=invocation.model,
        raw_response=invocation.raw_response,
        parsed_decision_json=invocation.parsed_decision_json,
        validation_status=invocation.validation_status,
        validation_errors_json=invocation.validation_errors_json,
        resulting_candidate_id=invocation.resulting_candidate_id,
        resulting_claim_set_id=claim_set.id,
        created_at=invocation.created_at,
    )
    design_intent = _design_intent(candidate.id)
    designer_invocation = _designer_invocation(candidate.id, claim_set.id, design_intent.id)
    prediction_plan = _prediction_plan(candidate.id, claim_set.id, design_intent.id, designer_invocation.id)
    plan = _plan(candidate.id, design_intent.id, prediction_plan.id)
    contrast = _contrast(plan, trade_count=trade_count, sharpe=sharpe)

    store.save_governed_hypothesis_bundle(invocation=invocation, candidate=candidate, claim_set=claim_set)
    store.save_governed_research_design_bundle(
        invocation=designer_invocation,
        design_intent=design_intent,
        prediction_plan=prediction_plan,
    )
    store.save_initial_experiment_plan(plan)
    store.save_parameter_sensitivity_contrast_result(contrast)
    verdict = ScientificVerdictEvaluator(store=store).evaluate(
        prediction_plan_id=prediction_plan.id,
        experiment_plan_id=plan.id,
        contrast_result_id=contrast.id,
    )
    return {
        "candidate": candidate,
        "brief": brief,
        "invocation": invocation,
        "claim_set": claim_set,
        "design_intent": design_intent,
        "designer_invocation": designer_invocation,
        "prediction_plan": prediction_plan,
        "plan": plan,
        "contrast": contrast,
        "verdict": verdict,
    }


def _table_counts(store: SQLiteStore) -> dict[str, int]:
    tables = (
        "hypothesis_scientist_invocations",
        "research_candidates",
        "hypothesis_claim_sets",
        "research_design_intents",
        "research_prediction_plans",
        "initial_experiment_plans",
        "condition_execution_records",
        "parameter_sensitivity_contrast_results",
        "scientific_verdicts",
        "post_verdict_critic_invocations",
        "post_verdict_research_intents",
    )
    with store.connect() as conn:
        return {
            table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in tables
        }


def test_post_verdict_critic_uses_exact_verdict_id_not_latest(tmp_path):
    store = _store(tmp_path)
    first = _persist_chain(store, label="first")
    second = _persist_chain(store, label="second")
    critic = CountingCritic()

    result = GovernedPostVerdictResearchCritic(store=store, critic=critic).critique(first["verdict"].id)

    assert critic.calls == 1
    assert critic.last_verdict_id == first["verdict"].id
    assert result.intent.scientific_verdict_id == first["verdict"].id
    assert result.intent.scientific_verdict_id != second["verdict"].id
    assert result.intent.research_scope_payload()["requested_outcomes"] == ["sharpe", "trade_count"]


def test_post_verdict_critic_second_call_reuses_existing_intent_without_provider_call(tmp_path):
    store = _store(tmp_path)
    chain = _persist_chain(store, label="idempotence")
    critic = CountingCritic()
    service = GovernedPostVerdictResearchCritic(store=store, critic=critic)

    first = service.critique(chain["verdict"].id)
    second = service.critique(chain["verdict"].id)

    assert critic.calls == 1
    assert first.intent.id == second.intent.id
    assert second.reused_existing is True
    with store.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM post_verdict_critic_invocations").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM post_verdict_research_intents").fetchone()[0] == 1


def test_post_verdict_critic_invalid_output_consumes_budget_and_persists_failed_invocation(tmp_path):
    store = _store(tmp_path)
    chain = _persist_chain(store, label="invalid-budget")
    critic = CountingCritic(
        diagnosis="The stricter threshold leaves room to vary lookback next.",
        next_step_rationale="Keep the same frozen scope.",
    )
    service = GovernedPostVerdictResearchCritic(store=store, critic=critic)

    with pytest.raises(ValueError, match="new independent variable"):
        service.critique(chain["verdict"].id)

    invocation = store.get_post_verdict_critic_invocation_by_scientific_verdict_id(chain["verdict"].id)
    assert invocation is not None
    assert invocation.validation_status == "INVALID"
    assert invocation.resulting_intent_id is None
    assert invocation.raw_response == '{"fake":"response"}'
    assert critic.calls == 1
    assert store.get_post_verdict_research_intent_by_scientific_verdict_id(chain["verdict"].id) is None

    with pytest.raises(RuntimeError, match="budget already consumed"):
        service.critique(chain["verdict"].id)

    assert critic.calls == 1


def test_post_verdict_critic_provider_error_consumes_budget_and_persists_failed_invocation(tmp_path):
    store = _store(tmp_path)
    chain = _persist_chain(store, label="provider-error")
    critic = RaisingCritic("synthetic provider failure")
    service = GovernedPostVerdictResearchCritic(store=store, critic=critic)

    with pytest.raises(RuntimeError, match="synthetic provider failure"):
        service.critique(chain["verdict"].id)

    invocation = store.get_post_verdict_critic_invocation_by_scientific_verdict_id(chain["verdict"].id)
    assert invocation is not None
    assert invocation.validation_status == "ERROR"
    assert invocation.resulting_intent_id is None
    assert json.loads(invocation.validation_errors_json) == {
        "provider_error": "RuntimeError: synthetic provider failure"
    }
    assert critic.calls == 1
    assert store.get_post_verdict_research_intent_by_scientific_verdict_id(chain["verdict"].id) is None

    with pytest.raises(RuntimeError, match="budget already consumed"):
        service.critique(chain["verdict"].id)

    assert critic.calls == 1


def test_post_verdict_critic_preexisting_reservation_without_intent_blocks_second_provider_call(tmp_path):
    store = _store(tmp_path)
    chain = _persist_chain(store, label="preexisting-reservation")
    critic = CountingCritic()
    store.try_create_post_verdict_critic_invocation(
        PostVerdictCriticInvocation(
            id=new_id(),
            scientific_verdict_id=chain["verdict"].id,
            context_version="post_verdict_critic_context_v1",
            prompt_version="v1",
            provider="fake",
            model="fake",
            context_snapshot_json="{}",
            raw_response=None,
            parsed_decision_json=None,
            validation_status="ERROR",
            validation_errors_json='{"provider_error":"prior failure"}',
            resulting_intent_id=None,
        )
    )

    with pytest.raises(RuntimeError, match="budget already consumed"):
        GovernedPostVerdictResearchCritic(store=store, critic=critic).critique(chain["verdict"].id)

    assert critic.calls == 0


def test_post_verdict_critic_wrong_verdict_to_contrast_provenance_fails_closed(tmp_path):
    store = _store(tmp_path)
    chain = _persist_chain(store, label="mismatch-a")
    other = _persist_chain(store, label="mismatch-b")
    with store.connect() as conn:
        conn.execute(
            "UPDATE scientific_verdicts SET experiment_plan_id = ? WHERE id = ?",
            (other["plan"].id, chain["verdict"].id),
        )
    with pytest.raises(ValueError, match="ContrastResult|InitialExperimentPlan|Contrast"):
        GovernedPostVerdictResearchCritic(store=store, critic=CountingCritic()).critique(chain["verdict"].id)


def test_post_verdict_critic_wrong_verdict_to_prediction_plan_provenance_fails_closed(tmp_path):
    store = _store(tmp_path)
    chain = _persist_chain(store, label="prediction-a")
    other = _persist_chain(store, label="prediction-b")
    with store.connect() as conn:
        conn.execute(
            "UPDATE scientific_verdicts SET prediction_plan_id = ? WHERE id = ?",
            (other["prediction_plan"].id, chain["verdict"].id),
        )
    with pytest.raises(ValueError, match="ResearchPredictionPlan"):
        GovernedPostVerdictResearchCritic(store=store, critic=CountingCritic()).critique(chain["verdict"].id)


def test_post_verdict_critic_wrong_claim_set_design_provenance_fails_closed(tmp_path):
    store = _store(tmp_path)
    chain = _persist_chain(store, label="claim-a")
    other = _persist_chain(store, label="claim-b")
    with store.connect() as conn:
        conn.execute(
            "UPDATE research_prediction_plans SET hypothesis_claim_set_id = ? WHERE id = ?",
            (other["claim_set"].id, chain["prediction_plan"].id),
        )
    with pytest.raises(ValueError, match="HypothesisClaimSet|ResearchCandidate"):
        GovernedPostVerdictResearchCritic(store=store, critic=CountingCritic()).critique(chain["verdict"].id)


def test_post_verdict_critic_missing_research_scope_fails_closed(tmp_path):
    store = _store(tmp_path)
    chain = _persist_chain(store, label="missing-scope")
    snapshot = json.loads(chain["invocation"].research_brief_snapshot)
    snapshot["research_scope"] = None
    with store.connect() as conn:
        conn.execute(
            "UPDATE hypothesis_scientist_invocations SET research_brief_snapshot = ? WHERE id = ?",
            (json.dumps(snapshot, sort_keys=True), chain["invocation"].id),
        )
    with pytest.raises(ValueError, match="ResearchScope"):
        GovernedPostVerdictResearchCritic(store=store, critic=CountingCritic()).critique(chain["verdict"].id)


def test_post_verdict_critic_non_falsified_verdict_is_not_applicable_and_makes_zero_provider_calls(tmp_path):
    store = _store(tmp_path)
    chain = _persist_chain(store, label="supported", trade_count=(10.0, 8.0), sharpe=(0.5, 0.8))
    assert chain["verdict"].overall_status == ScientificVerdictStatus.SUPPORTED
    critic = CountingCritic()
    with pytest.raises(RuntimeError, match="only applicable to FALSIFIED"):
        GovernedPostVerdictResearchCritic(store=store, critic=critic).critique(chain["verdict"].id)
    assert critic.calls == 0


@pytest.mark.parametrize(
    ("decision", "revision_kind"),
    [
        (PostVerdictCriticDecisionType.CONTINUE, PostVerdictRevisionKind.MECHANISM_REVISION),
        (PostVerdictCriticDecisionType.CONTINUE, PostVerdictRevisionKind.SCOPE_PRESERVING_HYPOTHESIS_REVISION),
        (PostVerdictCriticDecisionType.CONTINUE, PostVerdictRevisionKind.REPLICATION),
        (PostVerdictCriticDecisionType.STOP, PostVerdictRevisionKind.NONE),
    ],
)
def test_post_verdict_critic_accepts_valid_output_contracts(tmp_path, decision, revision_kind):
    store = _store(tmp_path)
    chain = _persist_chain(store, label=f"valid-{decision.value}-{revision_kind.value}")
    critic = CountingCritic(
        decision=decision,
        revision_kind=revision_kind,
        next_step_rationale=(
            "A bounded next step may still be scientifically defensible under the same frozen scope."
            if decision == PostVerdictCriticDecisionType.CONTINUE
            else "The current frozen scope does not justify another bounded follow-up."
        ),
    )
    result = GovernedPostVerdictResearchCritic(store=store, critic=critic).critique(chain["verdict"].id)
    assert result.intent.decision == decision
    assert result.intent.revision_kind == revision_kind


@pytest.mark.parametrize(
    ("decision", "revision_kind", "message"),
    [
        (PostVerdictCriticDecisionType.STOP, PostVerdictRevisionKind.MECHANISM_REVISION, "STOP requires"),
        (PostVerdictCriticDecisionType.CONTINUE, PostVerdictRevisionKind.NONE, "CONTINUE requires"),
    ],
)
def test_post_verdict_critic_rejects_invalid_decision_revision_pairs(tmp_path, decision, revision_kind, message):
    store = _store(tmp_path)
    chain = _persist_chain(store, label=f"invalid-{decision.value}-{revision_kind.value}")
    critic = CountingCritic(decision=decision, revision_kind=revision_kind)
    with pytest.raises(ValueError, match=message):
        GovernedPostVerdictResearchCritic(store=store, critic=critic).critique(chain["verdict"].id)


@pytest.mark.parametrize(
    ("diagnosis", "rationale", "message"),
    [
        (
            "The next experiment should use signal_threshold = 3.0 to try a stronger filter.",
            "Keep the same scope.",
            "exact execution parameter values",
        ),
        (
            "The next experiment should use lookback = 50.",
            "Keep the same scope.",
            "exact execution parameter values",
        ),
        (
            "net_pnl should be added to the next scope.",
            "Keep the same scope.",
            "outcomes outside the frozen ResearchScope",
        ),
        (
            "The prior result leaves room to vary lookback next.",
            "Keep the same scope.",
            "new independent variable",
        ),
        (
            "Use stub_backtester_v1 again for the next run.",
            "Keep the same scope.",
            "capability IDs",
        ),
        (
            "The next hypothesis should predict sharpe will decrease.",
            "Keep the same scope.",
            "next hypothesis",
        ),
    ],
)
def test_post_verdict_critic_rejects_forbidden_authority_leakage(tmp_path, diagnosis, rationale, message):
    store = _store(tmp_path)
    chain = _persist_chain(store, label="forbidden")
    critic = CountingCritic(diagnosis=diagnosis, next_step_rationale=rationale)
    with pytest.raises(ValueError, match=message):
        GovernedPostVerdictResearchCritic(store=store, critic=critic).critique(chain["verdict"].id)


def test_post_verdict_critic_allows_retrospective_directional_diagnosis(tmp_path):
    store = _store(tmp_path)
    chain = _persist_chain(store, label="retrospective")
    critic = CountingCritic(
        diagnosis=(
            "The stricter signal_threshold failed to improve sharpe, and trade_count stayed flat in the "
            "bounded contrast."
        ),
        next_step_rationale=(
            "A bounded follow-up may still be defensible if the next scientist explains why the mechanism "
            "might fail under this frozen scope."
        ),
    )

    result = GovernedPostVerdictResearchCritic(store=store, critic=critic).critique(chain["verdict"].id)

    assert result.intent.id
    assert critic.calls == 1


def test_post_verdict_critic_rejects_future_direction_authoring_even_without_next_hypothesis_phrase(tmp_path):
    store = _store(tmp_path)
    chain = _persist_chain(store, label="future-direction")
    critic = CountingCritic(
        diagnosis="The current bounded result is mixed.",
        next_step_rationale="In a follow-up, sharpe should increase while trade_count decreases.",
    )

    with pytest.raises(ValueError, match="future expected direction"):
        GovernedPostVerdictResearchCritic(store=store, critic=critic).critique(chain["verdict"].id)


def test_post_verdict_critic_adds_only_post_verdict_rows_and_leaves_historical_evidence_unchanged(tmp_path):
    store = _store(tmp_path)
    chain = _persist_chain(store, label="immutability")
    before = _table_counts(store)
    original_verdict = store.get_scientific_verdict(chain["verdict"].id)
    original_plan = store.get_initial_experiment_plan(chain["plan"].id)
    original_claim_set = store.get_hypothesis_claim_set(chain["claim_set"].id)

    result = GovernedPostVerdictResearchCritic(store=store, critic=CountingCritic()).critique(chain["verdict"].id)

    after = _table_counts(store)
    assert after["post_verdict_critic_invocations"] == before["post_verdict_critic_invocations"] + 1
    assert after["post_verdict_research_intents"] == before["post_verdict_research_intents"] + 1
    for table in (
        "hypothesis_scientist_invocations",
        "research_candidates",
        "hypothesis_claim_sets",
        "research_design_intents",
        "research_prediction_plans",
        "initial_experiment_plans",
        "condition_execution_records",
        "parameter_sensitivity_contrast_results",
        "scientific_verdicts",
    ):
        assert after[table] == before[table]
    assert store.get_scientific_verdict(chain["verdict"].id) == original_verdict
    assert store.get_initial_experiment_plan(chain["plan"].id) == original_plan
    assert store.get_hypothesis_claim_set(chain["claim_set"].id) == original_claim_set
    assert result.intent.research_scope_payload()["independent_variable"] == "signal_threshold"


def test_post_verdict_critic_prompt_hash_is_stable():
    assert get_post_verdict_research_critic_prompt_hash(CURRENT_POST_VERDICT_RESEARCH_CRITIC_PROMPT_VERSION) == (
        "7c7d4f32853ff2e8425fb63e4b786be73c42b9badd4ab39eee75c696c2d0b8e0"
    )


def test_v11_to_v12_migration_adds_post_verdict_tables_without_fabrication(tmp_path):
    db = Path(tmp_path) / "v11.sqlite"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        PRAGMA foreign_keys = ON;
        CREATE TABLE schema_version (id INTEGER PRIMARY KEY CHECK (id = 1), version INTEGER NOT NULL);
        INSERT INTO schema_version (id, version) VALUES (1, 11);
        CREATE TABLE research_design_intents (id TEXT PRIMARY KEY, candidate_id TEXT NOT NULL, design_kind TEXT NOT NULL,
            independent_variables_json TEXT NOT NULL, dependent_outcomes_json TEXT NOT NULL, controls_json TEXT NOT NULL,
            comparison_intent TEXT NOT NULL, analysis_intent TEXT NOT NULL, falsification_condition TEXT NOT NULL,
            rationale TEXT NOT NULL, source TEXT NOT NULL, provider TEXT, model TEXT, prompt_version TEXT,
            ontology_version TEXT, ontology_fingerprint TEXT, created_at TEXT NOT NULL);
        CREATE TABLE research_designer_invocations (id TEXT PRIMARY KEY, candidate_id TEXT NOT NULL, hypothesis_claim_set_id TEXT,
            candidate_snapshot_json TEXT NOT NULL, candidate_feasibility_decision_id TEXT NOT NULL, prompt_version TEXT NOT NULL,
            ontology_version TEXT NOT NULL, ontology_fingerprint TEXT NOT NULL, intent_contract_version TEXT NOT NULL,
            provider TEXT, model TEXT, raw_response TEXT, parsed_decision_json TEXT, validation_status TEXT,
            validation_errors_json TEXT, resulting_design_intent_id TEXT, created_at TEXT NOT NULL);
        CREATE TABLE hypothesis_claim_sets (id TEXT PRIMARY KEY, candidate_id TEXT NOT NULL UNIQUE,
            hypothesis_scientist_invocation_id TEXT NOT NULL UNIQUE, independent_variable TEXT NOT NULL,
            independent_variable_direction TEXT NOT NULL, claims_json TEXT NOT NULL, claim_aggregation TEXT NOT NULL,
            claim_contract_version TEXT NOT NULL, ontology_version TEXT NOT NULL, ontology_fingerprint TEXT NOT NULL,
            created_at TEXT NOT NULL);
        CREATE TABLE research_prediction_plans (id TEXT PRIMARY KEY, candidate_id TEXT NOT NULL, hypothesis_claim_set_id TEXT,
            design_intent_id TEXT NOT NULL UNIQUE, research_designer_invocation_id TEXT NOT NULL UNIQUE,
            prediction_contract_version TEXT NOT NULL, ontology_version TEXT NOT NULL, ontology_fingerprint TEXT NOT NULL,
            independent_variable TEXT NOT NULL, prediction_aggregation_rule TEXT NOT NULL, predictions_json TEXT NOT NULL,
            created_at TEXT NOT NULL);
        CREATE TABLE initial_experiment_plans (id TEXT PRIMARY KEY, candidate_id TEXT NOT NULL, design_intent_id TEXT NOT NULL,
            research_prediction_plan_id TEXT, candidate_feasibility_decision_id TEXT NOT NULL, selected_capability_id TEXT NOT NULL,
            design_kind TEXT NOT NULL, independent_variable TEXT NOT NULL, control_variables_json TEXT NOT NULL,
            dependent_outcomes_json TEXT NOT NULL, ordered_condition_ids_json TEXT NOT NULL, completion_rule TEXT NOT NULL,
            materializer_version TEXT NOT NULL, materialization_policy_version TEXT NOT NULL,
            materialization_policy_fingerprint TEXT NOT NULL, registry_version TEXT NOT NULL, registry_fingerprint TEXT NOT NULL,
            created_at TEXT NOT NULL);
        CREATE TABLE initial_experiment_conditions (id TEXT PRIMARY KEY, plan_id TEXT NOT NULL, ordinal INTEGER NOT NULL,
            role TEXT NOT NULL, exact_parameters_json TEXT NOT NULL, selected_capability_id TEXT NOT NULL,
            expected_tool_kind TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE parameter_sensitivity_contrast_results (id TEXT PRIMARY KEY, plan_id TEXT NOT NULL UNIQUE,
            independent_variable TEXT NOT NULL, baseline_condition_id TEXT NOT NULL, comparator_condition_id TEXT NOT NULL,
            baseline_parameter_value REAL NOT NULL, comparator_parameter_value REAL NOT NULL, outcomes_json TEXT NOT NULL,
            created_at TEXT NOT NULL);
        CREATE TABLE scientific_verdicts (id TEXT PRIMARY KEY, prediction_plan_id TEXT NOT NULL, design_intent_id TEXT NOT NULL,
            experiment_plan_id TEXT NOT NULL, contrast_result_id TEXT NOT NULL, verdict_policy_version TEXT NOT NULL,
            verdict_policy_fingerprint TEXT NOT NULL, overall_status TEXT NOT NULL, per_outcome_verdicts_json TEXT NOT NULL,
            created_at TEXT NOT NULL, UNIQUE(prediction_plan_id, contrast_result_id));
        INSERT INTO scientific_verdicts (
            id, prediction_plan_id, design_intent_id, experiment_plan_id, contrast_result_id,
            verdict_policy_version, verdict_policy_fingerprint, overall_status, per_outcome_verdicts_json, created_at
        ) VALUES (
            'verdict-1', 'prediction-1', 'design-1', 'plan-1', 'contrast-1',
            'directional_scientific_verdict_policy_v1', 'fp', 'FALSIFIED', '[]', '2026-08-25T00:00:00+00:00'
        );
        """
    )
    conn.commit()
    conn.close()

    store = SQLiteStore(db)
    with store.connect() as conn2:
        assert conn2.execute("SELECT version FROM schema_version WHERE id = 1").fetchone()[0] == 12
        tables = [row[0] for row in conn2.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        assert "post_verdict_critic_invocations" in tables
        assert "post_verdict_research_intents" in tables
        assert conn2.execute("SELECT COUNT(*) FROM post_verdict_critic_invocations").fetchone()[0] == 0
        assert conn2.execute("SELECT COUNT(*) FROM post_verdict_research_intents").fetchone()[0] == 0
        assert conn2.execute("SELECT id FROM scientific_verdicts WHERE id = 'verdict-1'").fetchone()[0] == "verdict-1"
