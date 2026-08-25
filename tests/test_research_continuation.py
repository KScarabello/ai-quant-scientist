from __future__ import annotations

import hashlib
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
)
from ai_quant_scientist.models.hypothesis_scientist import (
    HYPOTHESIS_CLAIM_SET_CONTRACT_VERSION,
    HypothesisClaimAggregation,
    HypothesisClaimSet,
    HypothesisScientistDecision,
    HypothesisScientistDecisionType,
    HypothesisScientistInvocation,
    ResearchBrief,
    ResearchScope,
    ResearchScopeOutcomeAggregation,
)
from ai_quant_scientist.models.post_verdict_critic import (
    PostVerdictCriticDecision,
    PostVerdictCriticDecisionType,
    PostVerdictRevisionKind,
)
from ai_quant_scientist.models.research import new_id
from ai_quant_scientist.models.research_continuation import (
    ResearchContinuationAttemptStatus,
    ResearchContinuationAuthorizationStatus,
    ResearchContinuationInvocation,
)
from ai_quant_scientist.models.research_designer import ResearchDesignerInvocation
from ai_quant_scientist.services.hypothesis_claim_ontology import build_hypothesis_claim_ontology_snapshot
from ai_quant_scientist.services.hypothesis_prompts import available_versions, get_scientist_instructions
from ai_quant_scientist.services.hypothesis_scientist import brief_to_json
from ai_quant_scientist.services.post_verdict_research_critic import GovernedPostVerdictResearchCritic
from ai_quant_scientist.services.research_continuation import (
    ContinuationHypothesisProposalValidator,
    GovernedResearchContinuation,
    continuation_context_to_payload,
)
from ai_quant_scientist.services.research_design_ontology import (
    RESEARCH_PREDICTION_PLAN_CONTRACT_VERSION,
    build_current_research_design_ontology_snapshot,
)
from ai_quant_scientist.services.scientific_verdict import ScientificVerdictEvaluator
from ai_quant_scientist.storage.sqlite_store import SQLiteStore


def _store(tmp_path) -> SQLiteStore:
    return SQLiteStore(tmp_path / "test.db")


class FakePostVerdictCritic:
    provider = "fake"
    model = "fake-post-verdict-v1"
    prompt_version = "post_verdict_research_critic_v1"

    def critique(self, context) -> PostVerdictCriticDecision:
        return PostVerdictCriticDecision(
            id=new_id(),
            scientific_verdict_id=context.scientific_verdict_id,
            decision=PostVerdictCriticDecisionType.CONTINUE,
            revision_kind=PostVerdictRevisionKind.MECHANISM_REVISION,
            diagnosis=(
                "The deterministic contrast falsified both required claims, so a new mechanism-level "
                "adaptive hypothesis may be warranted under the same frozen scope."
            ),
            next_step_rationale=(
                "A scope-preserving mechanism-focused follow-up may be warranted without asserting a design "
                "or executable plan."
            ),
            provider=self.provider,
            model=self.model,
            prompt_version=self.prompt_version,
            raw_response='{"fake":"critic"}',
        )


class RecordingContinuationScientist:
    provider = "fake"
    model = "fake-continuation-v6"
    prompt_version = "v6"

    def __init__(self, decision: HypothesisScientistDecision | None = None, *, error: Exception | None = None) -> None:
        self.calls = 0
        self.last_context = None
        self._decision = decision
        self._error = error

    def generate(self, context):
        self.calls += 1
        self.last_context = context
        if self._error is not None:
            raise self._error
        if self._decision is not None:
            return self._decision
        return _adaptive_decision(context.continuation_authorization_id)


def _candidate(label: str) -> ResearchCandidate:
    return ResearchCandidate.create(
        hypothesis_statement=(
            f"{label}: a stricter signal threshold should lower trade frequency and improve risk-adjusted performance."
        ),
        hypothesis_rationale=(
            "Filtering weaker signal realizations should reduce trade_count while improving Sharpe under fixed lookback."
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


def _contrast(plan: InitialExperimentPlan) -> ParameterSensitivityContrastResult:
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
                baseline_value=4.0,
                comparator_value=4.0,
                delta=0.0,
                baseline_condition_id=plan.ordered_conditions[0].id,
                comparator_condition_id=plan.ordered_conditions[1].id,
            ),
            OutcomeContrast(
                outcome=DesignOutcome.SHARPE,
                baseline_value=1.0,
                comparator_value=0.75,
                delta=-0.25,
                baseline_condition_id=plan.ordered_conditions[0].id,
                comparator_condition_id=plan.ordered_conditions[1].id,
            ),
        ),
    )


def _persist_parent_chain(store: SQLiteStore, *, label: str) -> dict:
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
    contrast = _contrast(plan)

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
    critic_result = GovernedPostVerdictResearchCritic(
        store=store,
        critic=FakePostVerdictCritic(),
    ).critique(verdict.id)
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
        "post_verdict_intent": critic_result.intent,
        "post_verdict_invocation": critic_result.invocation,
    }


def _adaptive_decision(authorization_id: str) -> HypothesisScientistDecision:
    reqs = (
        DataRequirement(
            requirement_id="data",
            data_kind=DataKind.SYNTHETIC_PARAMETRIC,
            asset_class=AssetClass.SYNTHETIC,
        ),
        ToolRequirement(
            requirement_id="tool",
            tool_kind=ToolKind.BACKTEST_EXECUTION,
        ),
    )
    from ai_quant_scientist.capabilities.serialization import requirements_to_json

    return HypothesisScientistDecision(
        id=new_id(),
        decision_type=HypothesisScientistDecisionType.PROPOSE_HYPOTHESIS,
        research_brief_id=authorization_id,
        hypothesis_statement=(
            "Under the same scope, a stricter signal threshold may reduce strategy selectivity enough to lower "
            "Sharpe while still reducing trade_count."
        ),
        hypothesis_rationale=(
            "If the stricter threshold removes observations that previously contributed positively to risk-adjusted "
            "performance without strongly changing execution frequency, Sharpe may fall while trade_count still declines."
        ),
        requirements_snapshot=requirements_to_json(reqs),
        independent_variable=DesignVariable.SIGNAL_THRESHOLD,
        independent_variable_direction=ExpectedDirection.INCREASE,
        outcome_claims=(
            OutcomePrediction(
                outcome=DesignOutcome.TRADE_COUNT,
                expected_direction=ExpectedDirection.DECREASE,
            ),
            OutcomePrediction(
                outcome=DesignOutcome.SHARPE,
                expected_direction=ExpectedDirection.DECREASE,
            ),
        ),
        claim_aggregation=HypothesisClaimAggregation.ALL_CLAIMS_REQUIRED,
        provider="fake",
        model="fake-v6",
        prompt_version="v6",
        raw_response='{"fake":"adaptive"}',
    )


def test_prompt_v6_available_and_frozen_hashes_unchanged():
    assert "v6" in available_versions()
    assert hashlib.sha256(get_scientist_instructions("v5").encode("utf-8")).hexdigest() == (
        "568a07e7467df49401e97120735d0ed650458d0ececb4a8b5cdd33c2e694d3dd"
    )


def test_prompt_v6_contains_adaptive_continuation_contract():
    prompt = get_scientist_instructions("v6")
    assert "ADAPTIVE follow-up hypothesis generation step" in prompt
    assert "must not be canonically identical to the parent claim set" in prompt
    assert "NOT an independent discovery step" in prompt


def test_prepare_creates_pending_authorization_for_continue_mechanism_revision(tmp_path):
    store = _store(tmp_path)
    chain = _persist_parent_chain(store, label="prepare")
    service = GovernedResearchContinuation(store=store, scientist=RecordingContinuationScientist())

    authorization = service.prepare(chain["post_verdict_intent"].id)

    assert authorization.authorization_status == ResearchContinuationAuthorizationStatus.PENDING
    assert authorization.allowed_revision_kind == PostVerdictRevisionKind.MECHANISM_REVISION
    assert authorization.generation_number == 2
    assert authorization.origin.value == "POST_VERDICT_ADAPTIVE"


def test_stop_intent_cannot_create_continuation_authorization(tmp_path):
    store = _store(tmp_path)
    chain = _persist_parent_chain(store, label="stop")
    with store.connect() as conn:
        conn.execute(
            "UPDATE post_verdict_research_intents SET decision = ?, revision_kind = ? WHERE id = ?",
            ("STOP", "NONE", chain["post_verdict_intent"].id),
        )

    service = GovernedResearchContinuation(store=store, scientist=RecordingContinuationScientist())
    with pytest.raises(RuntimeError, match="decision CONTINUE"):
        service.prepare(chain["post_verdict_intent"].id)


def test_pending_authorization_cannot_invoke_scientist(tmp_path):
    store = _store(tmp_path)
    chain = _persist_parent_chain(store, label="pending")
    scientist = RecordingContinuationScientist()
    service = GovernedResearchContinuation(store=store, scientist=scientist)
    authorization = service.prepare(chain["post_verdict_intent"].id)

    with pytest.raises(RuntimeError, match="awaiting explicit continuation authorization"):
        service.generate_hypothesis(authorization.id)

    assert scientist.calls == 0


def test_authorized_continuation_permits_exactly_one_successful_attempt_and_reuses_existing_result(tmp_path):
    store = _store(tmp_path)
    chain = _persist_parent_chain(store, label="success")
    scientist = RecordingContinuationScientist()
    service = GovernedResearchContinuation(store=store, scientist=scientist)
    authorization = service.authorize(service.prepare(chain["post_verdict_intent"].id).id)

    first = service.generate_hypothesis(authorization.id)
    second = service.generate_hypothesis(authorization.id)

    assert scientist.calls == 1
    assert first.status == ResearchContinuationAttemptStatus.GENERATED_ADAPTIVE_HYPOTHESIS
    assert second.reused_existing is True
    assert first.candidate is not None and second.candidate is not None
    assert first.candidate.id == second.candidate.id
    stored_auth = store.get_research_continuation_authorization(authorization.id)
    assert stored_auth.authorization_status == ResearchContinuationAuthorizationStatus.CONSUMED


def test_continuation_context_includes_bounded_lineage_and_excludes_raw_experiment_payload(tmp_path):
    store = _store(tmp_path)
    chain = _persist_parent_chain(store, label="context")
    service = GovernedResearchContinuation(store=store, scientist=RecordingContinuationScientist())
    authorization = service.authorize(service.prepare(chain["post_verdict_intent"].id).id)
    scientist = RecordingContinuationScientist(
        HypothesisScientistDecision(
            id=new_id(),
            decision_type=HypothesisScientistDecisionType.NO_HYPOTHESIS,
            research_brief_id=authorization.id,
            no_hypothesis_reason="No defensible novel adaptive hypothesis exists under the frozen scope.",
            provider="fake",
            model="fake-v6",
            prompt_version="v6",
            raw_response='{"fake":"no"}',
        )
    )
    service = GovernedResearchContinuation(store=store, scientist=scientist)
    result = service.generate_hypothesis(authorization.id)
    payload = continuation_context_to_payload(scientist.last_context)
    payload_json = json.dumps(payload, sort_keys=True)

    assert result.status == ResearchContinuationAttemptStatus.NO_HYPOTHESIS
    assert payload["research_scope"]["independent_variable"] == "signal_threshold"
    assert payload["parent_hypothesis_claim_set"]["hypothesis_claim_set_id"] == chain["claim_set"].id
    assert payload["parent_verdict_status"] == "FALSIFIED"
    assert payload["critic_revision_kind"] == "MECHANISM_REVISION"
    assert "contrast_result" not in payload
    assert "2.0" not in payload_json
    assert "2.5" not in payload_json
    assert "\"lookback\": 20" not in payload_json
    assert "stub_backtester_v1" not in payload_json


def test_identical_child_signature_invalid_for_mechanism_revision_even_with_new_rationale(tmp_path):
    store = _store(tmp_path)
    chain = _persist_parent_chain(store, label="identical")
    service = GovernedResearchContinuation(store=store, scientist=RecordingContinuationScientist())
    authorization = service.authorize(service.prepare(chain["post_verdict_intent"].id).id)
    decision = HypothesisScientistDecision(
        id=new_id(),
        decision_type=HypothesisScientistDecisionType.PROPOSE_HYPOTHESIS,
        research_brief_id=authorization.id,
        hypothesis_statement="A restated hypothesis with different prose.",
        hypothesis_rationale="Different wording, same structured claims.",
        requirements_snapshot=_adaptive_decision(authorization.id).requirements_snapshot,
        independent_variable=DesignVariable.SIGNAL_THRESHOLD,
        independent_variable_direction=ExpectedDirection.INCREASE,
        outcome_claims=(
            OutcomePrediction(outcome=DesignOutcome.TRADE_COUNT, expected_direction=ExpectedDirection.DECREASE),
            OutcomePrediction(outcome=DesignOutcome.SHARPE, expected_direction=ExpectedDirection.INCREASE),
        ),
        claim_aggregation=HypothesisClaimAggregation.ALL_CLAIMS_REQUIRED,
        provider="fake",
        model="fake-v6",
        prompt_version="v6",
        raw_response='{"fake":"same"}',
    )
    scientist = RecordingContinuationScientist(decision)
    service = GovernedResearchContinuation(store=store, scientist=scientist)

    result = service.generate_hypothesis(authorization.id)

    assert result.status == ResearchContinuationAttemptStatus.INVALID_ATTEMPT
    assert result.candidate is None
    assert "continuation_novelty" in json.loads(result.invocation.validation_errors_json)


@pytest.mark.parametrize(
    "bad_decision",
    [
        lambda auth_id: HypothesisScientistDecision(
            id=new_id(),
            decision_type=HypothesisScientistDecisionType.PROPOSE_HYPOTHESIS,
            research_brief_id=auth_id,
            hypothesis_statement="Bad missing outcome",
            hypothesis_rationale="Omits sharpe from the frozen scope.",
            requirements_snapshot=_adaptive_decision(auth_id).requirements_snapshot,
            independent_variable=DesignVariable.SIGNAL_THRESHOLD,
            independent_variable_direction=ExpectedDirection.INCREASE,
            outcome_claims=(
                OutcomePrediction(outcome=DesignOutcome.TRADE_COUNT, expected_direction=ExpectedDirection.DECREASE),
            ),
            claim_aggregation=HypothesisClaimAggregation.ALL_CLAIMS_REQUIRED,
            provider="fake",
            model="fake-v6",
            prompt_version="v6",
            raw_response='{"fake":"bad"}',
        ),
        lambda auth_id: HypothesisScientistDecision(
            id=new_id(),
            decision_type=HypothesisScientistDecisionType.PROPOSE_HYPOTHESIS,
            research_brief_id=auth_id,
            hypothesis_statement="Bad missing aggregation",
            hypothesis_rationale="Omits required aggregation semantics.",
            requirements_snapshot=_adaptive_decision(auth_id).requirements_snapshot,
            independent_variable=DesignVariable.SIGNAL_THRESHOLD,
            independent_variable_direction=ExpectedDirection.INCREASE,
            outcome_claims=(
                OutcomePrediction(outcome=DesignOutcome.TRADE_COUNT, expected_direction=ExpectedDirection.DECREASE),
                OutcomePrediction(outcome=DesignOutcome.SHARPE, expected_direction=ExpectedDirection.DECREASE),
            ),
            claim_aggregation=None,
            provider="fake",
            model="fake-v6",
            prompt_version="v6",
            raw_response='{"fake":"bad"}',
        ),
        lambda auth_id: HypothesisScientistDecision(
            id=new_id(),
            decision_type=HypothesisScientistDecisionType.PROPOSE_HYPOTHESIS,
            research_brief_id=auth_id,
            hypothesis_statement="Bad extra outcome",
            hypothesis_rationale="Adds net_pnl.",
            requirements_snapshot=_adaptive_decision(auth_id).requirements_snapshot,
            independent_variable=DesignVariable.SIGNAL_THRESHOLD,
            independent_variable_direction=ExpectedDirection.INCREASE,
            outcome_claims=(
                OutcomePrediction(outcome=DesignOutcome.TRADE_COUNT, expected_direction=ExpectedDirection.DECREASE),
                OutcomePrediction(outcome=DesignOutcome.SHARPE, expected_direction=ExpectedDirection.DECREASE),
                OutcomePrediction(outcome=DesignOutcome.NET_PNL, expected_direction=ExpectedDirection.INCREASE),
            ),
            claim_aggregation=HypothesisClaimAggregation.ALL_CLAIMS_REQUIRED,
            provider="fake",
            model="fake-v6",
            prompt_version="v6",
            raw_response='{"fake":"bad"}',
        ),
        lambda auth_id: HypothesisScientistDecision(
            id=new_id(),
            decision_type=HypothesisScientistDecisionType.PROPOSE_HYPOTHESIS,
            research_brief_id=auth_id,
            hypothesis_statement="Bad wrong iv",
            hypothesis_rationale="Changes independent variable.",
            requirements_snapshot=_adaptive_decision(auth_id).requirements_snapshot,
            independent_variable=DesignVariable.LOOKBACK,
            independent_variable_direction=ExpectedDirection.INCREASE,
            outcome_claims=(
                OutcomePrediction(outcome=DesignOutcome.TRADE_COUNT, expected_direction=ExpectedDirection.DECREASE),
                OutcomePrediction(outcome=DesignOutcome.SHARPE, expected_direction=ExpectedDirection.DECREASE),
            ),
            claim_aggregation=HypothesisClaimAggregation.ALL_CLAIMS_REQUIRED,
            provider="fake",
            model="fake-v6",
            prompt_version="v6",
            raw_response='{"fake":"bad"}',
        ),
        lambda auth_id: HypothesisScientistDecision(
            id=new_id(),
            decision_type=HypothesisScientistDecisionType.PROPOSE_HYPOTHESIS,
            research_brief_id=auth_id,
            hypothesis_statement="Bad NO_CHANGE",
            hypothesis_rationale="Uses unsupported NO_CHANGE.",
            requirements_snapshot=_adaptive_decision(auth_id).requirements_snapshot,
            independent_variable=DesignVariable.SIGNAL_THRESHOLD,
            independent_variable_direction=ExpectedDirection.INCREASE,
            outcome_claims=(
                OutcomePrediction(outcome=DesignOutcome.TRADE_COUNT, expected_direction=ExpectedDirection.NO_CHANGE),
                OutcomePrediction(outcome=DesignOutcome.SHARPE, expected_direction=ExpectedDirection.DECREASE),
            ),
            claim_aggregation=HypothesisClaimAggregation.ALL_CLAIMS_REQUIRED,
            provider="fake",
            model="fake-v6",
            prompt_version="v6",
            raw_response='{"fake":"bad"}',
        ),
    ],
)
def test_scope_and_direction_failures_consume_attempt_and_persist_invalid_invocation(tmp_path, bad_decision):
    store = _store(tmp_path)
    chain = _persist_parent_chain(store, label="invalid")
    authorization = GovernedResearchContinuation(
        store=store,
        scientist=RecordingContinuationScientist(),
    ).authorize(
        GovernedResearchContinuation(
            store=store,
            scientist=RecordingContinuationScientist(),
        ).prepare(chain["post_verdict_intent"].id).id
    )
    scientist = RecordingContinuationScientist(bad_decision(authorization.id))
    service = GovernedResearchContinuation(store=store, scientist=scientist)

    result = service.generate_hypothesis(authorization.id)
    retry = service.generate_hypothesis(authorization.id)

    assert result.status == ResearchContinuationAttemptStatus.INVALID_ATTEMPT
    assert retry.reused_existing is True
    assert scientist.calls == 1
    assert store.get_research_continuation_authorization(authorization.id).authorization_status == (
        ResearchContinuationAuthorizationStatus.CONSUMED
    )


def test_provider_error_consumes_attempt_and_persists_failure(tmp_path):
    store = _store(tmp_path)
    chain = _persist_parent_chain(store, label="provider-error")
    scientist = RecordingContinuationScientist(error=RuntimeError("synthetic provider failure"))
    service = GovernedResearchContinuation(store=store, scientist=scientist)
    authorization = service.authorize(service.prepare(chain["post_verdict_intent"].id).id)

    result = service.generate_hypothesis(authorization.id)
    retry = service.generate_hypothesis(authorization.id)

    assert result.status == ResearchContinuationAttemptStatus.PROVIDER_ERROR
    assert retry.reused_existing is True
    assert scientist.calls == 1
    assert json.loads(result.invocation.validation_errors_json) == {
        "provider_error": "RuntimeError: synthetic provider failure"
    }


def test_no_hypothesis_is_terminal_and_consumes_attempt(tmp_path):
    store = _store(tmp_path)
    chain = _persist_parent_chain(store, label="no-hypothesis")
    service = GovernedResearchContinuation(store=store, scientist=RecordingContinuationScientist())
    authorization = service.authorize(service.prepare(chain["post_verdict_intent"].id).id)
    decision = HypothesisScientistDecision(
        id=new_id(),
        decision_type=HypothesisScientistDecisionType.NO_HYPOTHESIS,
        research_brief_id=authorization.id,
        no_hypothesis_reason="No defensible novel adaptive hypothesis exists under the frozen scope.",
        provider="fake",
        model="fake-v6",
        prompt_version="v6",
        raw_response='{"fake":"no"}',
    )
    scientist = RecordingContinuationScientist(decision)
    service = GovernedResearchContinuation(store=store, scientist=scientist)

    result = service.generate_hypothesis(authorization.id)
    retry = service.generate_hypothesis(authorization.id)

    assert result.status == ResearchContinuationAttemptStatus.NO_HYPOTHESIS
    assert retry.reused_existing is True
    assert scientist.calls == 1


def test_wrong_parent_lineage_reference_fails_closed(tmp_path):
    store = _store(tmp_path)
    chain = _persist_parent_chain(store, label="broken-lineage")
    other_chain = _persist_parent_chain(store, label="other-lineage")
    with store.connect() as conn:
        conn.execute(
            "UPDATE post_verdict_research_intents SET hypothesis_claim_set_id = ? WHERE id = ?",
            (other_chain["claim_set"].id, chain["post_verdict_intent"].id),
        )
    service = GovernedResearchContinuation(store=store, scientist=RecordingContinuationScientist())

    with pytest.raises(ValueError, match="prediction-plan lineage"):
        service.prepare(chain["post_verdict_intent"].id)


def test_authorization_reservation_allows_only_one_provider_attempt(tmp_path):
    store = _store(tmp_path)
    chain = _persist_parent_chain(store, label="reservation")
    service = GovernedResearchContinuation(store=store, scientist=RecordingContinuationScientist())
    authorization = service.authorize(service.prepare(chain["post_verdict_intent"].id).id)

    first_invocation = ResearchContinuationInvocation(
        id=new_id(),
        continuation_authorization_id=authorization.id,
        post_verdict_research_intent_id=authorization.post_verdict_research_intent_id,
        parent_scientific_verdict_id=authorization.parent_scientific_verdict_id,
        context_version="research_continuation_context_v1",
        prompt_version="v6",
        provider="fake",
        model="fake-v6",
        context_snapshot_json="{}",
        raw_response=None,
        parsed_decision_json=None,
        attempt_status=ResearchContinuationAttemptStatus.IN_PROGRESS,
        validation_errors_json=None,
        resulting_candidate_id=None,
        resulting_claim_set_id=None,
    )
    second_invocation = ResearchContinuationInvocation(
            id=new_id(),
            continuation_authorization_id=authorization.id,
            post_verdict_research_intent_id=authorization.post_verdict_research_intent_id,
            parent_scientific_verdict_id=authorization.parent_scientific_verdict_id,
            context_version="research_continuation_context_v1",
            prompt_version="v6",
            provider="fake",
            model="fake-v6",
            context_snapshot_json="{}",
            raw_response=None,
            parsed_decision_json=None,
            attempt_status=ResearchContinuationAttemptStatus.IN_PROGRESS,
            validation_errors_json=None,
            resulting_candidate_id=None,
            resulting_claim_set_id=None,
    )

    first = store.try_reserve_research_continuation_invocation(first_invocation)
    second = store.try_reserve_research_continuation_invocation(second_invocation)

    assert first is True
    assert second is False
    assert store.get_research_continuation_authorization(authorization.id).authorization_status == (
        ResearchContinuationAuthorizationStatus.CONSUMED
    )


def test_success_persists_adaptive_lineage_and_no_downstream_experiment_artifacts(tmp_path):
    store = _store(tmp_path)
    chain = _persist_parent_chain(store, label="lineage")
    before_counts = {}
    with store.connect() as conn:
        for table in (
            "research_prediction_plans",
            "initial_experiment_plans",
            "parameter_sensitivity_contrast_results",
            "scientific_verdicts",
        ):
            before_counts[table] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    service = GovernedResearchContinuation(store=store, scientist=RecordingContinuationScientist())
    authorization = service.authorize(service.prepare(chain["post_verdict_intent"].id).id)

    result = service.generate_hypothesis(authorization.id)

    assert result.status == ResearchContinuationAttemptStatus.GENERATED_ADAPTIVE_HYPOTHESIS
    lineage = store.get_adaptive_hypothesis_lineage_by_candidate_id(result.candidate.id)
    assert lineage is not None
    assert lineage.continuation_authorization_id == authorization.id
    assert lineage.post_verdict_research_intent_id == chain["post_verdict_intent"].id
    assert lineage.parent_scientific_verdict_id == chain["verdict"].id
    assert lineage.parent_hypothesis_claim_set_id == chain["claim_set"].id
    assert lineage.parent_candidate_id == chain["candidate"].id
    assert lineage.generation_number == 2
    assert lineage.origin.value == "POST_VERDICT_ADAPTIVE"
    with store.connect() as conn:
        for table in before_counts:
            assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == before_counts[table]


def test_v12_to_v13_migration_adds_continuation_tables_without_fabrication(tmp_path):
    db = Path(tmp_path) / "v12.sqlite"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        PRAGMA foreign_keys = ON;
        CREATE TABLE schema_version (id INTEGER PRIMARY KEY CHECK (id = 1), version INTEGER NOT NULL);
        INSERT INTO schema_version (id, version) VALUES (1, 12);
        CREATE TABLE research_candidates (
            id TEXT PRIMARY KEY, hypothesis_statement TEXT NOT NULL, hypothesis_rationale TEXT NOT NULL,
            source TEXT NOT NULL, requirements_json TEXT NOT NULL, candidate_fingerprint TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE hypothesis_claim_sets (
            id TEXT PRIMARY KEY, candidate_id TEXT NOT NULL UNIQUE, hypothesis_scientist_invocation_id TEXT NOT NULL UNIQUE,
            independent_variable TEXT NOT NULL, independent_variable_direction TEXT NOT NULL, claims_json TEXT NOT NULL,
            claim_aggregation TEXT NOT NULL, claim_contract_version TEXT NOT NULL, ontology_version TEXT NOT NULL,
            ontology_fingerprint TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TABLE scientific_verdicts (
            id TEXT PRIMARY KEY, prediction_plan_id TEXT NOT NULL, design_intent_id TEXT NOT NULL,
            experiment_plan_id TEXT NOT NULL, contrast_result_id TEXT NOT NULL, verdict_policy_version TEXT NOT NULL,
            verdict_policy_fingerprint TEXT NOT NULL, overall_status TEXT NOT NULL, per_outcome_verdicts_json TEXT NOT NULL,
            created_at TEXT NOT NULL, UNIQUE(prediction_plan_id, contrast_result_id)
        );
        CREATE TABLE post_verdict_research_intents (
            id TEXT PRIMARY KEY, scientific_verdict_id TEXT NOT NULL UNIQUE, research_brief_id TEXT NOT NULL,
            hypothesis_claim_set_id TEXT NOT NULL, research_design_intent_id TEXT NOT NULL,
            research_prediction_plan_id TEXT NOT NULL, contrast_result_id TEXT NOT NULL,
            critic_invocation_id TEXT NOT NULL UNIQUE, decision TEXT NOT NULL, revision_kind TEXT NOT NULL,
            diagnosis TEXT NOT NULL, next_step_rationale TEXT NOT NULL, prompt_version TEXT NOT NULL,
            contract_version TEXT NOT NULL, provider TEXT, model TEXT, research_scope_snapshot_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        """
    )
    conn.commit()
    conn.close()

    store = SQLiteStore(db)
    with store.connect() as conn2:
        assert conn2.execute("SELECT version FROM schema_version WHERE id = 1").fetchone()[0] == 13
        tables = [row[0] for row in conn2.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        assert "research_continuation_authorizations" in tables
        assert "research_continuation_invocations" in tables
        assert "adaptive_hypothesis_lineages" in tables
        assert conn2.execute("SELECT COUNT(*) FROM research_continuation_authorizations").fetchone()[0] == 0
        assert conn2.execute("SELECT COUNT(*) FROM research_continuation_invocations").fetchone()[0] == 0
        assert conn2.execute("SELECT COUNT(*) FROM adaptive_hypothesis_lineages").fetchone()[0] == 0
