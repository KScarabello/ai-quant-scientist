from __future__ import annotations

import sqlite3

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
    PredictionVerdictResult,
    ResearchDesignIntent,
    ResearchDesignKind,
    ResearchPredictionPlan,
    ScientificVerdictStatus,
)
from ai_quant_scientist.models.research import new_id
from ai_quant_scientist.models.research_designer import ResearchDesignerInvocation
from ai_quant_scientist.services.research_design_ontology import (
    RESEARCH_PREDICTION_PLAN_CONTRACT_VERSION,
    build_current_research_design_ontology_snapshot,
)
from ai_quant_scientist.services.scientific_verdict import (
    SCIENTIFIC_VERDICT_POLICY_VERSION,
    ScientificVerdictEvaluator,
    build_directional_scientific_verdict_policy,
)
from ai_quant_scientist.storage.sqlite_store import SQLiteStore


def _store(tmp_path) -> SQLiteStore:
    return SQLiteStore(tmp_path / "test.db")


def _candidate() -> ResearchCandidate:
    return ResearchCandidate.create(
        hypothesis_statement=(
            "A stricter signal threshold should lower trade frequency and improve risk-adjusted performance."
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
        rationale="Bounded threshold-sensitivity design with precommitted directional predictions.",
        source="research_designer_v2:fake:fake-v1",
        provider="fake",
        model="fake-v1",
        prompt_version="v2",
        ontology_version=ontology.version,
        ontology_fingerprint=ontology.fingerprint,
    )


def _designer_invocation(candidate_id: str, design_intent_id: str) -> ResearchDesignerInvocation:
    ontology = build_current_research_design_ontology_snapshot()
    return ResearchDesignerInvocation(
        id=new_id(),
        candidate_id=candidate_id,
        candidate_snapshot_json="{}",
        candidate_feasibility_decision_id="ready-1",
        prompt_version="v2",
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


def _prediction_plan(candidate_id: str, design_intent_id: str, invocation_id: str) -> ResearchPredictionPlan:
    ontology = build_current_research_design_ontology_snapshot()
    return ResearchPredictionPlan(
        id=new_id(),
        candidate_id=candidate_id,
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


def _plan(candidate_id: str, design_intent_id: str, prediction_plan_id: str | None) -> InitialExperimentPlan:
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


def _persist_prediction_bench(store: SQLiteStore):
    candidate = _candidate()
    design_intent = _design_intent(candidate.id)
    invocation = _designer_invocation(candidate.id, design_intent.id)
    prediction_plan = _prediction_plan(candidate.id, design_intent.id, invocation.id)
    plan = _plan(candidate.id, design_intent.id, prediction_plan.id)

    store.save_research_candidate(candidate)
    store.save_research_design_intent(design_intent)
    store.save_research_designer_invocation(invocation)
    store.save_research_prediction_plan(prediction_plan)
    store.save_initial_experiment_plan(plan)
    return candidate, design_intent, invocation, prediction_plan, plan


def test_directional_scientific_verdict_policy_v1_is_stable():
    policy = build_directional_scientific_verdict_policy()
    assert policy.version == SCIENTIFIC_VERDICT_POLICY_VERSION
    assert policy.aggregation_rule == "ALL_PREDICTIONS_REQUIRED"
    assert len(policy.fingerprint()) == 64


def test_scientific_verdict_full_pass_is_supported(tmp_path):
    store = _store(tmp_path)
    _, _, _, prediction_plan, plan = _persist_prediction_bench(store)
    contrast = _contrast(plan, trade_count=(10.0, 8.0), sharpe=(0.5, 0.8))
    store.save_parameter_sensitivity_contrast_result(contrast)

    verdict = ScientificVerdictEvaluator(store=store).evaluate(
        prediction_plan_id=prediction_plan.id,
        experiment_plan_id=plan.id,
        contrast_result_id=contrast.id,
    )

    assert verdict.overall_status == ScientificVerdictStatus.SUPPORTED
    assert {item.result for item in verdict.per_outcome_verdicts} == {PredictionVerdictResult.PASS}


def test_scientific_verdict_v014_like_numbers_are_falsified_without_retrospective_attachment(tmp_path):
    store = _store(tmp_path)
    _, _, _, prediction_plan, plan = _persist_prediction_bench(store)
    contrast = _contrast(plan, trade_count=(4.0, 4.0), sharpe=(1.0, 0.75))
    store.save_parameter_sensitivity_contrast_result(contrast)

    verdict = ScientificVerdictEvaluator(store=store).evaluate(
        prediction_plan_id=prediction_plan.id,
        experiment_plan_id=plan.id,
        contrast_result_id=contrast.id,
    )

    assert verdict.overall_status == ScientificVerdictStatus.FALSIFIED
    per_outcome = {item.outcome.value: item for item in verdict.per_outcome_verdicts}
    assert per_outcome["trade_count"].observed_direction == ExpectedDirection.NO_CHANGE
    assert per_outcome["trade_count"].result == PredictionVerdictResult.FAIL
    assert per_outcome["sharpe"].observed_direction == ExpectedDirection.DECREASE
    assert per_outcome["sharpe"].result == PredictionVerdictResult.FAIL


def test_scientific_verdict_partial_failure_is_still_falsified(tmp_path):
    store = _store(tmp_path)
    _, _, _, prediction_plan, plan = _persist_prediction_bench(store)
    contrast = _contrast(plan, trade_count=(10.0, 8.0), sharpe=(1.0, 0.75))
    store.save_parameter_sensitivity_contrast_result(contrast)

    verdict = ScientificVerdictEvaluator(store=store).evaluate(
        prediction_plan_id=prediction_plan.id,
        experiment_plan_id=plan.id,
        contrast_result_id=contrast.id,
    )

    assert verdict.overall_status == ScientificVerdictStatus.FALSIFIED
    assert {item.result for item in verdict.per_outcome_verdicts} == {
        PredictionVerdictResult.PASS,
        PredictionVerdictResult.FAIL,
    }


def test_scientific_verdict_missing_outcome_is_indeterminate(tmp_path):
    store = _store(tmp_path)
    _, _, _, prediction_plan, plan = _persist_prediction_bench(store)
    contrast = ParameterSensitivityContrastResult(
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
                baseline_value=10.0,
                comparator_value=8.0,
                delta=-2.0,
                baseline_condition_id=plan.ordered_conditions[0].id,
                comparator_condition_id=plan.ordered_conditions[1].id,
            ),
        ),
    )
    store.save_parameter_sensitivity_contrast_result(contrast)

    verdict = ScientificVerdictEvaluator(store=store).evaluate(
        prediction_plan_id=prediction_plan.id,
        experiment_plan_id=plan.id,
        contrast_result_id=contrast.id,
    )

    assert verdict.overall_status == ScientificVerdictStatus.INDETERMINATE
    per_outcome = {item.outcome.value: item for item in verdict.per_outcome_verdicts}
    assert per_outcome["trade_count"].result == PredictionVerdictResult.PASS
    assert per_outcome["sharpe"].result == PredictionVerdictResult.INDETERMINATE


def test_scientific_verdict_ignores_unpredicted_extra_metrics(tmp_path):
    store = _store(tmp_path)
    _, _, _, prediction_plan, plan = _persist_prediction_bench(store)
    contrast = ParameterSensitivityContrastResult(
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
                baseline_value=10.0,
                comparator_value=8.0,
                delta=-2.0,
                baseline_condition_id=plan.ordered_conditions[0].id,
                comparator_condition_id=plan.ordered_conditions[1].id,
            ),
            OutcomeContrast(
                outcome=DesignOutcome.SHARPE,
                baseline_value=0.5,
                comparator_value=0.8,
                delta=0.3,
                baseline_condition_id=plan.ordered_conditions[0].id,
                comparator_condition_id=plan.ordered_conditions[1].id,
            ),
            OutcomeContrast(
                outcome=DesignOutcome.NET_PNL,
                baseline_value=12.5,
                comparator_value=9.38,
                delta=-3.12,
                baseline_condition_id=plan.ordered_conditions[0].id,
                comparator_condition_id=plan.ordered_conditions[1].id,
            ),
        ),
    )
    store.save_parameter_sensitivity_contrast_result(contrast)

    verdict = ScientificVerdictEvaluator(store=store).evaluate(
        prediction_plan_id=prediction_plan.id,
        experiment_plan_id=plan.id,
        contrast_result_id=contrast.id,
    )

    assert verdict.overall_status == ScientificVerdictStatus.SUPPORTED
    assert [item.outcome.value for item in verdict.per_outcome_verdicts] == ["sharpe", "trade_count"]


def test_parameter_sensitivity_contrast_result_duplicate_outcomes_fail_closed():
    with pytest.raises(ValueError, match="must not repeat outcomes"):
        ParameterSensitivityContrastResult(
            id=new_id(),
            plan_id="plan-1",
            independent_variable=DesignVariable.SIGNAL_THRESHOLD,
            baseline_condition_id="baseline",
            comparator_condition_id="comparator",
            baseline_parameter_value=2.0,
            comparator_parameter_value=2.5,
            outcomes=(
                OutcomeContrast(
                    outcome=DesignOutcome.TRADE_COUNT,
                    baseline_value=10.0,
                    comparator_value=8.0,
                    delta=-2.0,
                    baseline_condition_id="baseline",
                    comparator_condition_id="comparator",
                ),
                OutcomeContrast(
                    outcome=DesignOutcome.TRADE_COUNT,
                    baseline_value=10.0,
                    comparator_value=7.0,
                    delta=-3.0,
                    baseline_condition_id="baseline",
                    comparator_condition_id="comparator",
                ),
            ),
        )


def test_scientific_verdict_wrong_provenance_fails_closed(tmp_path):
    store = _store(tmp_path)
    _, _, _, prediction_plan_a, _plan_a = _persist_prediction_bench(store)
    _, _, _, prediction_plan_b, plan_b = _persist_prediction_bench(store)
    contrast_b = _contrast(plan_b, trade_count=(10.0, 8.0), sharpe=(0.5, 0.8))
    store.save_parameter_sensitivity_contrast_result(contrast_b)

    with pytest.raises(ValueError, match="does not point to the requested ResearchPredictionPlan"):
        ScientificVerdictEvaluator(store=store).evaluate(
            prediction_plan_id=prediction_plan_a.id,
            experiment_plan_id=plan_b.id,
            contrast_result_id=contrast_b.id,
        )


def test_scientific_verdict_wrong_independent_variable_fails_closed(tmp_path):
    store = _store(tmp_path)
    _, _, _, prediction_plan, plan = _persist_prediction_bench(store)
    bad_contrast = ParameterSensitivityContrastResult(
        id=new_id(),
        plan_id=plan.id,
        independent_variable=DesignVariable.LOOKBACK,
        baseline_condition_id=plan.ordered_conditions[0].id,
        comparator_condition_id=plan.ordered_conditions[1].id,
        baseline_parameter_value=20.0,
        comparator_parameter_value=30.0,
        outcomes=(),
    )
    store.save_parameter_sensitivity_contrast_result(bad_contrast)

    with pytest.raises(ValueError, match="Contrast independent variable does not match"):
        ScientificVerdictEvaluator(store=store).evaluate(
            prediction_plan_id=prediction_plan.id,
            experiment_plan_id=plan.id,
            contrast_result_id=bad_contrast.id,
        )


def test_scientific_verdict_inverted_relation_fails_closed(tmp_path):
    store = _store(tmp_path)
    _, _, _, prediction_plan, plan = _persist_prediction_bench(store)
    bad_contrast = ParameterSensitivityContrastResult(
        id=new_id(),
        plan_id=plan.id,
        independent_variable=DesignVariable.SIGNAL_THRESHOLD,
        baseline_condition_id=plan.ordered_conditions[0].id,
        comparator_condition_id=plan.ordered_conditions[1].id,
        baseline_parameter_value=2.5,
        comparator_parameter_value=2.0,
        outcomes=(),
    )
    store.save_parameter_sensitivity_contrast_result(bad_contrast)

    with pytest.raises(ValueError, match="must be greater than the baseline value"):
        ScientificVerdictEvaluator(store=store).evaluate(
            prediction_plan_id=prediction_plan.id,
            experiment_plan_id=plan.id,
            contrast_result_id=bad_contrast.id,
        )


def test_scientific_verdict_retry_is_idempotent(tmp_path):
    store = _store(tmp_path)
    _, _, _, prediction_plan, plan = _persist_prediction_bench(store)
    contrast = _contrast(plan, trade_count=(10.0, 8.0), sharpe=(0.5, 0.8))
    store.save_parameter_sensitivity_contrast_result(contrast)

    evaluator = ScientificVerdictEvaluator(store=store)
    first = evaluator.evaluate(
        prediction_plan_id=prediction_plan.id,
        experiment_plan_id=plan.id,
        contrast_result_id=contrast.id,
    )
    second = evaluator.evaluate(
        prediction_plan_id=prediction_plan.id,
        experiment_plan_id=plan.id,
        contrast_result_id=contrast.id,
    )

    assert first.id == second.id
    with store.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM scientific_verdicts").fetchone()[0] == 1


def test_historical_plan_without_prediction_plan_receives_no_retrospective_v15_verdict(tmp_path):
    store = _store(tmp_path)
    candidate = _candidate()
    design_intent = _design_intent(candidate.id)
    plan = _plan(candidate.id, design_intent.id, None)
    contrast = _contrast(plan, trade_count=(4.0, 4.0), sharpe=(1.0, 0.75))

    store.save_research_candidate(candidate)
    store.save_research_design_intent(design_intent)
    store.save_initial_experiment_plan(plan)
    store.save_parameter_sensitivity_contrast_result(contrast)

    with pytest.raises(ValueError, match="must not receive retrospective V0.15 verdicts"):
        ScientificVerdictEvaluator(store=store).evaluate_plan(plan.id)


def test_v9_to_v10_migration_adds_prediction_and_verdict_tables_without_fabricating_history(tmp_path):
    db = tmp_path / "v9.sqlite"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        PRAGMA foreign_keys = ON;
        CREATE TABLE schema_version (id INTEGER PRIMARY KEY CHECK (id = 1), version INTEGER NOT NULL);
        INSERT INTO schema_version (id, version) VALUES (1, 9);
        CREATE TABLE research_candidates (
            id TEXT PRIMARY KEY,
            hypothesis_statement TEXT NOT NULL,
            hypothesis_rationale TEXT NOT NULL,
            source TEXT NOT NULL,
            requirements_json TEXT NOT NULL,
            candidate_fingerprint TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE research_design_intents (
            id TEXT PRIMARY KEY,
            candidate_id TEXT NOT NULL,
            design_kind TEXT NOT NULL,
            independent_variables_json TEXT NOT NULL,
            dependent_outcomes_json TEXT NOT NULL,
            controls_json TEXT NOT NULL,
            comparison_intent TEXT NOT NULL,
            analysis_intent TEXT NOT NULL,
            falsification_condition TEXT NOT NULL,
            rationale TEXT NOT NULL,
            source TEXT NOT NULL,
            provider TEXT,
            model TEXT,
            prompt_version TEXT,
            ontology_version TEXT,
            ontology_fingerprint TEXT,
            created_at TEXT NOT NULL
        );
        CREATE TABLE research_designer_invocations (
            id TEXT PRIMARY KEY,
            candidate_id TEXT NOT NULL,
            candidate_snapshot_json TEXT NOT NULL,
            candidate_feasibility_decision_id TEXT NOT NULL,
            prompt_version TEXT NOT NULL,
            ontology_version TEXT NOT NULL,
            ontology_fingerprint TEXT NOT NULL,
            intent_contract_version TEXT NOT NULL,
            provider TEXT,
            model TEXT,
            raw_response TEXT,
            parsed_decision_json TEXT,
            validation_status TEXT,
            validation_errors_json TEXT,
            resulting_design_intent_id TEXT,
            created_at TEXT NOT NULL
        );
        CREATE TABLE initial_experiment_plans (
            id TEXT PRIMARY KEY,
            candidate_id TEXT NOT NULL,
            design_intent_id TEXT NOT NULL,
            candidate_feasibility_decision_id TEXT NOT NULL,
            selected_capability_id TEXT NOT NULL,
            design_kind TEXT NOT NULL,
            independent_variable TEXT NOT NULL,
            control_variables_json TEXT NOT NULL,
            dependent_outcomes_json TEXT NOT NULL,
            ordered_condition_ids_json TEXT NOT NULL,
            completion_rule TEXT NOT NULL,
            materializer_version TEXT NOT NULL,
            materialization_policy_version TEXT NOT NULL,
            materialization_policy_fingerprint TEXT NOT NULL,
            registry_version TEXT NOT NULL,
            registry_fingerprint TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE initial_experiment_conditions (
            id TEXT PRIMARY KEY,
            plan_id TEXT NOT NULL,
            ordinal INTEGER NOT NULL,
            role TEXT NOT NULL,
            exact_parameters_json TEXT NOT NULL,
            selected_capability_id TEXT NOT NULL,
            expected_tool_kind TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE parameter_sensitivity_contrast_results (
            id TEXT PRIMARY KEY,
            plan_id TEXT NOT NULL UNIQUE,
            independent_variable TEXT NOT NULL,
            baseline_condition_id TEXT NOT NULL,
            comparator_condition_id TEXT NOT NULL,
            baseline_parameter_value REAL NOT NULL,
            comparator_parameter_value REAL NOT NULL,
            outcomes_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        """
    )
    conn.commit()
    conn.close()

    store = SQLiteStore(db)
    with store.connect() as conn:
        assert conn.execute("SELECT version FROM schema_version WHERE id = 1").fetchone()[0] == 10
        plan_columns = [row[1] for row in conn.execute("PRAGMA table_info(initial_experiment_plans)").fetchall()]
        tables = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        assert "research_prediction_plan_id" in plan_columns
        assert "research_prediction_plans" in tables
        assert "scientific_verdicts" in tables
        assert conn.execute("SELECT COUNT(*) FROM research_prediction_plans").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM scientific_verdicts").fetchone()[0] == 0
