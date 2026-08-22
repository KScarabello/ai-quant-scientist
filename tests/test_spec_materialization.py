from __future__ import annotations

import sqlite3
from dataclasses import replace

import pytest

from ai_quant_scientist.capabilities import (
    AssetClass,
    Capability,
    CapabilityRegistry,
    DataKind,
    DataRequirement,
    ResearchCandidate,
    Resolution,
    ToolKind,
    ToolRequirement,
    build_v1_registry,
)
from ai_quant_scientist.capabilities.intake import GovernedResearchIntake
from ai_quant_scientist.models.design import (
    AnalysisIntent,
    ComparisonIntent,
    ConditionExecutionStatus,
    DesignOutcome,
    DesignVariable,
    ExperimentCondition,
    ExperimentConditionRole,
    InitialExperimentCompletionRule,
    InitialExperimentPlan,
    InitialExperimentPlanProposalStatus,
    ResearchDesignIntent,
    ResearchDesignKind,
    SpecFeasibilityPhase,
    SpecFeasibilityReasonCode,
    SpecFeasibilityStatus,
)
from ai_quant_scientist.services.spec_materialization import (
    GovernedSpecMaterialization,
    InitialExperimentExecutor,
    MaterializationBlockedError,
    SpecFeasibilityValidator,
    SpecMaterializer,
    build_stub_materialization_policy,
)
from ai_quant_scientist.storage.sqlite_store import SQLiteStore


def _store(tmp_path) -> SQLiteStore:
    return SQLiteStore(tmp_path / "test.db")


def _registry() -> CapabilityRegistry:
    return build_v1_registry()


def _synthetic_candidate() -> ResearchCandidate:
    return ResearchCandidate.create(
        hypothesis_statement="Signal threshold strictness changes trade frequency and performance.",
        hypothesis_rationale="Synthetic stub experiment with deterministic execution.",
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


def _blocked_candidate() -> ResearchCandidate:
    return ResearchCandidate.create(
        hypothesis_statement="MES order-book imbalance predicts one-second futures returns.",
        hypothesis_rationale="Requires unavailable real market data.",
        requirements=[
            DataRequirement(
                requirement_id="book",
                data_kind=DataKind.ORDER_BOOK,
                asset_class=AssetClass.FUTURES,
                instruments=("MES",),
                resolution=Resolution.SECOND_1,
            ),
            ToolRequirement(
                requirement_id="tool",
                tool_kind=ToolKind.BACKTEST_EXECUTION,
            ),
        ],
    )


def _design_intent(
    candidate_id: str,
    *,
    dependent_outcomes: tuple[DesignOutcome, ...] = (
        DesignOutcome.TRADE_COUNT,
        DesignOutcome.NET_PNL,
        DesignOutcome.SHARPE,
    ),
) -> ResearchDesignIntent:
    return ResearchDesignIntent.create(
        candidate_id=candidate_id,
        design_kind=ResearchDesignKind.PARAMETER_SENSITIVITY,
        independent_variables=(DesignVariable.SIGNAL_THRESHOLD,),
        dependent_outcomes=dependent_outcomes,
        controls=(DesignVariable.LOOKBACK,),
        comparison_intent=ComparisonIntent.CONTRAST_PARAMETER_LEVELS,
        analysis_intent=AnalysisIntent.SENSITIVITY_ANALYSIS,
        falsification_condition=(
            "If loosening signal threshold does not materially change trade_count or risk-adjusted "
            "performance, the hypothesis is weakened."
        ),
        rationale="Investigate threshold sensitivity while holding lookback fixed.",
    )


def _submit_candidate(store: SQLiteStore, registry: CapabilityRegistry, candidate: ResearchCandidate):
    intake = GovernedResearchIntake(store, registry)
    result = intake.submit(candidate)
    latest = store.get_latest_feasibility_decision(candidate.id)
    assert latest is not None
    return result, latest


def _disabled_stub_registry() -> CapabilityRegistry:
    stub = next(c for c in build_v1_registry().list_capabilities() if c.capability_id == "stub_backtester_v1")
    disabled = replace(stub, enabled=False)
    return CapabilityRegistry([disabled])


def test_research_design_intent_is_immutable():
    intent = _design_intent("candidate-1")
    with pytest.raises(Exception):
        intent.rationale = "mutated"  # type: ignore[misc]


def test_research_design_intent_rejects_malformed_abstract_fields():
    with pytest.raises(ValueError):
        ResearchDesignIntent(
            id="intent-1",
            candidate_id="candidate-1",
            design_kind=ResearchDesignKind.PARAMETER_SENSITIVITY,
            independent_variables=("signal_threshold=2.0",),  # type: ignore[arg-type]
            dependent_outcomes=(DesignOutcome.SHARPE,),
            controls=(DesignVariable.LOOKBACK,),
            comparison_intent=ComparisonIntent.CONTRAST_PARAMETER_LEVELS,
            analysis_intent=AnalysisIntent.SENSITIVITY_ANALYSIS,
            falsification_condition="f",
            rationale="r",
        )


def test_materializer_is_deterministic_for_same_inputs(tmp_path):
    store = _store(tmp_path)
    registry = _registry()
    candidate = _synthetic_candidate()
    _, latest = _submit_candidate(store, registry, candidate)
    stored_candidate = store.get_research_candidate(candidate.id)
    assert stored_candidate is not None
    intent = _design_intent(candidate.id)
    materializer = SpecMaterializer()

    result1 = materializer.materialize(
        candidate=stored_candidate,
        design_intent=intent,
        candidate_feasibility_decision=latest,
        registry=registry,
    )
    result2 = materializer.materialize(
        candidate=stored_candidate,
        design_intent=intent,
        candidate_feasibility_decision=latest,
        registry=registry,
    )

    assert tuple(c.exact_parameters for c in result1.plan.ordered_conditions) == tuple(
        c.exact_parameters for c in result2.plan.ordered_conditions
    )
    assert result1.plan.materialization_policy_fingerprint == result2.plan.materialization_policy_fingerprint


def test_materializer_exact_values_come_from_policy(tmp_path):
    store = _store(tmp_path)
    registry = _registry()
    candidate = _synthetic_candidate()
    _, latest = _submit_candidate(store, registry, candidate)
    stored_candidate = store.get_research_candidate(candidate.id)
    assert stored_candidate is not None
    intent = _design_intent(candidate.id)

    policy = build_stub_materialization_policy()
    result = SpecMaterializer(policy=policy).materialize(
        candidate=stored_candidate,
        design_intent=intent,
        candidate_feasibility_decision=latest,
        registry=registry,
    )

    baseline, comparator = result.plan.ordered_conditions
    assert dict(baseline.exact_parameters) == dict(policy.baseline_parameters)
    assert dict(comparator.exact_parameters) == dict(policy.comparator_parameters)
    assert result.plan.materialization_policy_version == policy.version


def test_only_ready_candidates_can_be_materialized(tmp_path):
    store = _store(tmp_path)
    registry = _registry()
    candidate = _blocked_candidate()
    intake_result, _ = _submit_candidate(store, registry, candidate)
    assert intake_result.is_blocked

    governed = GovernedSpecMaterialization(store=store, registry=registry)
    with pytest.raises(MaterializationBlockedError):
        governed.materialize(
            candidate,
            _design_intent(candidate.id),
            candidate_feasibility_decision_id=store.get_latest_feasibility_decision(candidate.id).id,
        )

    assert store.list_spec_feasibility_decisions(candidate.id) == []


def test_design_intent_wrong_candidate_fails_closed(tmp_path):
    store = _store(tmp_path)
    registry = _registry()
    candidate_a = _synthetic_candidate()
    _, latest = _submit_candidate(store, registry, candidate_a)
    candidate_b = _synthetic_candidate()
    store.save_research_candidate(candidate_b)
    intent = _design_intent(candidate_a.id)

    with pytest.raises(MaterializationBlockedError):
        SpecMaterializer().materialize(
            candidate=candidate_b,
            design_intent=intent,
            candidate_feasibility_decision=latest,
            registry=registry,
        )


def test_unsupported_independent_variable_fails_closed(tmp_path):
    store = _store(tmp_path)
    registry = _registry()
    candidate = _synthetic_candidate()
    _, latest = _submit_candidate(store, registry, candidate)
    intent = ResearchDesignIntent.create(
        candidate_id=candidate.id,
        design_kind=ResearchDesignKind.PARAMETER_SENSITIVITY,
        independent_variables=(DesignVariable.LOOKBACK,),
        dependent_outcomes=(DesignOutcome.SHARPE,),
        controls=(DesignVariable.LOOKBACK,),
        comparison_intent=ComparisonIntent.CONTRAST_PARAMETER_LEVELS,
        analysis_intent=AnalysisIntent.SENSITIVITY_ANALYSIS,
        falsification_condition="Falsify if changing lookback does not matter.",
        rationale="Unsupported V1 path for materializer.",
    )

    with pytest.raises(MaterializationBlockedError):
        SpecMaterializer().materialize(
            candidate=candidate,
            design_intent=intent,
            candidate_feasibility_decision=latest,
            registry=registry,
        )


def test_unsupported_score_outcome_is_truthfully_rejected(tmp_path):
    store = _store(tmp_path)
    registry = _registry()
    candidate = _synthetic_candidate()
    _, latest = _submit_candidate(store, registry, candidate)

    with pytest.raises(MaterializationBlockedError):
        SpecMaterializer().materialize(
            candidate=candidate,
            design_intent=_design_intent(candidate.id, dependent_outcomes=(DesignOutcome.SCORE,)),
            candidate_feasibility_decision=latest,
            registry=registry,
        )


def test_plan_contains_baseline_and_comparator_only_independent_variable_differs(tmp_path):
    store = _store(tmp_path)
    registry = _registry()
    candidate = _synthetic_candidate()
    _, latest = _submit_candidate(store, registry, candidate)
    result = SpecMaterializer().materialize(
        candidate=candidate,
        design_intent=_design_intent(candidate.id),
        candidate_feasibility_decision=latest,
        registry=registry,
    )

    baseline, comparator = result.plan.ordered_conditions
    assert baseline.role == ExperimentConditionRole.BASELINE
    assert comparator.role == ExperimentConditionRole.COMPARATOR
    assert baseline.exact_parameters["lookback"] == comparator.exact_parameters["lookback"] == 20
    assert baseline.exact_parameters["signal_threshold"] == 2.0
    assert comparator.exact_parameters["signal_threshold"] == 2.5


def test_candidate_authorization_id_for_other_candidate_fails_closed(tmp_path):
    store = _store(tmp_path)
    registry = _registry()
    candidate_a = _synthetic_candidate()
    _, latest_a = _submit_candidate(store, registry, candidate_a)
    candidate_b = _synthetic_candidate()
    store.save_research_candidate(candidate_b)

    governed = GovernedSpecMaterialization(store=store, registry=registry)
    with pytest.raises(MaterializationBlockedError):
        governed.materialize(
            candidate_b,
            _design_intent(candidate_b.id),
            candidate_feasibility_decision_id=latest_a.id,
        )


class _ComparatorRejectingValidator(SpecFeasibilityValidator):
    def validate(self, **kwargs):
        decision = super().validate(**kwargs)
        if kwargs["proposed_parameters"]["signal_threshold"] == 2.5:
            return replace(
                decision,
                status=SpecFeasibilityStatus.FAIL,
                reason_codes=(SpecFeasibilityReasonCode.PARAMETER_TYPE_INVALID,),
                validation_notes="comparator_rejected_for_test",
            )
        return decision


def test_comparator_failed_exact_feasibility_rejects_plan(tmp_path):
    store = _store(tmp_path)
    registry = _registry()
    candidate = _synthetic_candidate()
    _, latest = _submit_candidate(store, registry, candidate)
    materializer = SpecMaterializer(feasibility_validator=_ComparatorRejectingValidator())
    result = materializer.materialize(
        candidate=candidate,
        design_intent=_design_intent(candidate.id),
        candidate_feasibility_decision=latest,
        registry=registry,
    )
    comparator_decision = result.condition_feasibility_decisions[1]
    assert result.proposal.status == InitialExperimentPlanProposalStatus.REJECTED
    assert comparator_decision.status == SpecFeasibilityStatus.FAIL
    assert comparator_decision.condition_id == result.plan.ordered_conditions[1].id


def test_fresh_acceptance_revalidation_blocks_disabled_capability(tmp_path):
    store = _store(tmp_path)
    registry = _registry()
    candidate = _synthetic_candidate()
    _, latest = _submit_candidate(store, registry, candidate)

    governed = GovernedSpecMaterialization(store=store, registry=registry)
    materialized = governed.materialize(
        candidate,
        _design_intent(candidate.id),
        candidate_feasibility_decision_id=latest.id,
    )

    disabled = GovernedSpecMaterialization(store=store, registry=_disabled_stub_registry())
    with pytest.raises(ValueError):
        disabled.accept_proposal(materialized.proposal.id)

    proposal = store.get_initial_experiment_plan_proposal(materialized.proposal.id)
    assert proposal is not None
    assert proposal.status == InitialExperimentPlanProposalStatus.PROPOSED
    acceptance_decisions = [
        decision
        for decision in store.list_spec_feasibility_decisions(candidate.id)
        if decision.phase == SpecFeasibilityPhase.ACCEPTANCE
    ]
    assert len(acceptance_decisions) == 2
    assert any(SpecFeasibilityReasonCode.CAPABILITY_DISABLED in d.reason_codes for d in acceptance_decisions)


def test_policy_is_immutable_and_fingerprint_stable():
    policy = build_stub_materialization_policy()
    fingerprint = policy.fingerprint()
    with pytest.raises(TypeError):
        policy.baseline_parameters["signal_threshold"] = 99  # type: ignore[index]
    assert policy.fingerprint() == fingerprint


def test_condition_payload_is_deeply_immutable(tmp_path):
    store = _store(tmp_path)
    registry = _registry()
    candidate = _synthetic_candidate()
    _, latest = _submit_candidate(store, registry, candidate)
    result = SpecMaterializer().materialize(
        candidate=candidate,
        design_intent=_design_intent(candidate.id),
        candidate_feasibility_decision=latest,
        registry=registry,
    )
    with pytest.raises(TypeError):
        result.plan.ordered_conditions[0].exact_parameters["lookback"] = 99  # type: ignore[index]


def test_invalid_plan_shapes_fail_closed():
    baseline = ExperimentCondition(
        id="baseline",
        ordinal=1,
        role=ExperimentConditionRole.BASELINE,
        exact_parameters={"signal_threshold": 2.0, "lookback": 20},
        selected_capability_id="stub_backtester_v1",
        expected_tool_kind=ToolKind.BACKTEST_EXECUTION.value,
    )
    comparator = ExperimentCondition(
        id="comparator",
        ordinal=2,
        role=ExperimentConditionRole.BASELINE,
        exact_parameters={"signal_threshold": 2.5, "lookback": 20},
        selected_capability_id="stub_backtester_v1",
        expected_tool_kind=ToolKind.BACKTEST_EXECUTION.value,
    )
    with pytest.raises(ValueError):
        InitialExperimentPlan(
            id="plan",
            candidate_id="candidate",
            design_intent_id="intent",
            candidate_feasibility_decision_id="decision",
            selected_capability_id="stub_backtester_v1",
            design_kind=ResearchDesignKind.PARAMETER_SENSITIVITY,
            independent_variable=DesignVariable.SIGNAL_THRESHOLD,
            control_variables=(DesignVariable.LOOKBACK,),
            dependent_outcomes=(DesignOutcome.TRADE_COUNT,),
            ordered_conditions=(baseline,),
            completion_rule=InitialExperimentCompletionRule.ALL_CONDITIONS_REQUIRED,
            materializer_version="m",
            materialization_policy_version="p",
            materialization_policy_fingerprint="f",
            registry_version="r",
            registry_fingerprint="rf",
        )
    with pytest.raises(ValueError):
        InitialExperimentPlan(
            id="plan",
            candidate_id="candidate",
            design_intent_id="intent",
            candidate_feasibility_decision_id="decision",
            selected_capability_id="stub_backtester_v1",
            design_kind=ResearchDesignKind.PARAMETER_SENSITIVITY,
            independent_variable=DesignVariable.SIGNAL_THRESHOLD,
            control_variables=(DesignVariable.LOOKBACK,),
            dependent_outcomes=(DesignOutcome.TRADE_COUNT,),
            ordered_conditions=(baseline, comparator),
            completion_rule=InitialExperimentCompletionRule.ALL_CONDITIONS_REQUIRED,
            materializer_version="m",
            materialization_policy_version="p",
            materialization_policy_fingerprint="f",
            registry_version="r",
            registry_fingerprint="rf",
        )


def test_comparator_cannot_change_control_variable():
    baseline = ExperimentCondition(
        id="baseline",
        ordinal=1,
        role=ExperimentConditionRole.BASELINE,
        exact_parameters={"signal_threshold": 2.0, "lookback": 20},
        selected_capability_id="stub_backtester_v1",
        expected_tool_kind=ToolKind.BACKTEST_EXECUTION.value,
    )
    comparator = ExperimentCondition(
        id="comparator",
        ordinal=2,
        role=ExperimentConditionRole.COMPARATOR,
        exact_parameters={"signal_threshold": 2.5, "lookback": 25},
        selected_capability_id="stub_backtester_v1",
        expected_tool_kind=ToolKind.BACKTEST_EXECUTION.value,
    )
    with pytest.raises(ValueError):
        InitialExperimentPlan(
            id="plan",
            candidate_id="candidate",
            design_intent_id="intent",
            candidate_feasibility_decision_id="decision",
            selected_capability_id="stub_backtester_v1",
            design_kind=ResearchDesignKind.PARAMETER_SENSITIVITY,
            independent_variable=DesignVariable.SIGNAL_THRESHOLD,
            control_variables=(DesignVariable.LOOKBACK,),
            dependent_outcomes=(DesignOutcome.TRADE_COUNT,),
            ordered_conditions=(baseline, comparator),
            completion_rule=InitialExperimentCompletionRule.ALL_CONDITIONS_REQUIRED,
            materializer_version="m",
            materialization_policy_version="p",
            materialization_policy_fingerprint="f",
            registry_version="r",
            registry_fingerprint="rf",
        )


def test_execution_cannot_start_before_acceptance(tmp_path):
    store = _store(tmp_path)
    registry = _registry()
    candidate = _synthetic_candidate()
    _, latest = _submit_candidate(store, registry, candidate)
    governed = GovernedSpecMaterialization(store=store, registry=registry)
    materialized = governed.materialize(
        candidate,
        _design_intent(candidate.id),
        candidate_feasibility_decision_id=latest.id,
    )
    executor = InitialExperimentExecutor(store=store)
    with pytest.raises(RuntimeError):
        executor.execute_plan(materialized.plan.id)


def test_accepted_plan_cannot_be_accepted_twice(tmp_path):
    store = _store(tmp_path)
    registry = _registry()
    candidate = _synthetic_candidate()
    _, latest = _submit_candidate(store, registry, candidate)
    governed = GovernedSpecMaterialization(store=store, registry=registry)
    proposal = governed.materialize(
        candidate,
        _design_intent(candidate.id),
        candidate_feasibility_decision_id=latest.id,
    ).proposal

    governed.accept_proposal(proposal.id)
    with pytest.raises(ValueError):
        governed.accept_proposal(proposal.id)


def test_completed_conditions_are_not_rerun_and_no_contrast_before_completion(tmp_path):
    store = _store(tmp_path)
    registry = _registry()
    candidate = _synthetic_candidate()
    _, latest = _submit_candidate(store, registry, candidate)
    governed = GovernedSpecMaterialization(store=store, registry=registry)
    materialized = governed.materialize(
        candidate,
        _design_intent(candidate.id),
        candidate_feasibility_decision_id=latest.id,
    )
    governed.accept_proposal(materialized.proposal.id)

    from ai_quant_scientist.tools.stub_backtester import StubBacktester

    class FlakyBacktester:
        name = "stub_backtester"

        def __init__(self) -> None:
            self.calls = 0
            self._delegate = StubBacktester()

        def run(self, *, spec, attempt_id):
            self.calls += 1
            if self.calls == 2:
                raise RuntimeError("boom_after_baseline")
            return self._delegate.run(spec=spec, attempt_id=attempt_id)

    flaky_tool = FlakyBacktester()
    executor = InitialExperimentExecutor(store=store, research_tool=flaky_tool)
    with pytest.raises(RuntimeError):
        executor.execute_plan(materialized.plan.id)

    records = store.list_condition_execution_records(materialized.plan.id)
    assert len(records) == 1
    assert store.get_parameter_sensitivity_contrast_result(materialized.plan.id) is None

    executor = InitialExperimentExecutor(store=store)
    contrast = executor.execute_plan(materialized.plan.id)
    assert contrast.plan_id == materialized.plan.id
    assert len(store.list_condition_execution_records(materialized.plan.id)) == 2
    contrast_again = executor.execute_plan(materialized.plan.id)
    assert contrast_again.id == contrast.id
    assert len(store.list_condition_execution_records(materialized.plan.id)) == 2


def test_semantic_closure_end_to_end_executes_true_parameter_sensitivity_contrast(tmp_path):
    store = _store(tmp_path)
    registry = _registry()
    candidate = _synthetic_candidate()
    intake_result, latest = _submit_candidate(store, registry, candidate)
    assert intake_result.is_ready

    governed = GovernedSpecMaterialization(store=store, registry=registry)
    materialized = governed.materialize(
        candidate,
        _design_intent(candidate.id),
        candidate_feasibility_decision_id=latest.id,
    )

    baseline, comparator = materialized.plan.ordered_conditions
    assert baseline.role == ExperimentConditionRole.BASELINE
    assert comparator.role == ExperimentConditionRole.COMPARATOR
    assert baseline.exact_parameters["signal_threshold"] == 2.0
    assert comparator.exact_parameters["signal_threshold"] == 2.5
    assert baseline.exact_parameters["lookback"] == comparator.exact_parameters["lookback"] == 20
    assert all(decision.status == SpecFeasibilityStatus.PASS for decision in materialized.condition_feasibility_decisions)

    accepted = governed.accept_proposal(materialized.proposal.id)
    assert accepted.status == InitialExperimentPlanProposalStatus.ACCEPTED

    executor = InitialExperimentExecutor(store=store)
    contrast = executor.execute_plan(materialized.plan.id)

    records = store.list_condition_execution_records(materialized.plan.id)
    assert len(records) == 2
    assert [record.role for record in records] == [
        ExperimentConditionRole.BASELINE,
        ExperimentConditionRole.COMPARATOR,
    ]
    assert [record.ordinal for record in records] == [1, 2]
    assert [record.status for record in records] == [
        ConditionExecutionStatus.COMPLETED,
        ConditionExecutionStatus.COMPLETED,
    ]

    assert contrast.baseline_parameter_value == 2.0
    assert contrast.comparator_parameter_value == 2.5
    outcomes = {outcome.outcome.value: outcome for outcome in contrast.outcomes}
    assert set(outcomes) == {"trade_count", "net_pnl", "sharpe"}
    assert outcomes["trade_count"].baseline_value == 4.0
    assert outcomes["trade_count"].comparator_value == 4.0
    assert outcomes["trade_count"].delta == 0.0
    assert outcomes["net_pnl"].baseline_value == 12.5
    assert outcomes["net_pnl"].comparator_value == 9.38
    assert outcomes["net_pnl"].delta == pytest.approx(-3.12)
    assert outcomes["sharpe"].baseline_value == 1.0
    assert outcomes["sharpe"].comparator_value == 0.75
    assert outcomes["sharpe"].delta == pytest.approx(-0.25)

    proposal = store.get_initial_experiment_plan_proposal(materialized.proposal.id)
    assert proposal is not None
    assert proposal.status == InitialExperimentPlanProposalStatus.COMPLETED
    assert proposal.contrast_result_id == contrast.id
    assert store.get_parameter_sensitivity_contrast_result(materialized.plan.id) is not None
    with store.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM research_runs").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM spec_revision_proposals").fetchone()[0] == 0


def test_v7_to_v8_migration_adds_plan_tables_and_feasibility_columns(tmp_path):
    db = tmp_path / "v7.sqlite"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        PRAGMA foreign_keys = ON;
        CREATE TABLE schema_version (id INTEGER PRIMARY KEY CHECK (id = 1), version INTEGER NOT NULL);
        INSERT INTO schema_version (id, version) VALUES (1, 7);
        CREATE TABLE research_runs (
            id TEXT PRIMARY KEY,
            stage TEXT NOT NULL,
            status TEXT NOT NULL,
            hypothesis_id TEXT NOT NULL,
            active_spec_id TEXT NOT NULL,
            next_required_action TEXT NOT NULL DEFAULT 'NONE',
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
            parent_spec_id TEXT,
            revision_proposal_id TEXT,
            design_intent_id TEXT,
            spec_materialization_proposal_id TEXT,
            selected_capability_id TEXT,
            materializer_version TEXT,
            parameters_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            frozen_at TEXT,
            is_frozen INTEGER NOT NULL
        );
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
            created_at TEXT NOT NULL
        );
        CREATE TABLE spec_feasibility_decisions (
            id TEXT PRIMARY KEY,
            candidate_id TEXT NOT NULL,
            design_intent_id TEXT NOT NULL,
            selected_capability_id TEXT NOT NULL,
            status TEXT NOT NULL,
            reason_codes_json TEXT NOT NULL,
            proposed_parameters_json TEXT NOT NULL,
            validation_notes TEXT NOT NULL,
            spec_feasibility_version TEXT NOT NULL,
            registry_version TEXT NOT NULL,
            registry_fingerprint TEXT NOT NULL,
            materializer_version TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        """
    )
    conn.commit()
    conn.close()

    store = SQLiteStore(db)
    with store.connect() as c:
        version = c.execute("SELECT version FROM schema_version WHERE id = 1").fetchone()[0]
        assert version == 8
        spec_feasibility_columns = [row[1] for row in c.execute("PRAGMA table_info(spec_feasibility_decisions)").fetchall()]
        assert "plan_id" in spec_feasibility_columns
        assert "condition_id" in spec_feasibility_columns
        assert "phase" in spec_feasibility_columns
        tables = [row[0] for row in c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        assert "initial_experiment_plans" in tables
        assert "initial_experiment_conditions" in tables
        assert "initial_experiment_plan_proposals" in tables
        assert "condition_execution_records" in tables
        assert "parameter_sensitivity_contrast_results" in tables


def test_fresh_v8_db_has_plan_tables(tmp_path):
    store = SQLiteStore(tmp_path / "fresh.db")
    with store.connect() as c:
        version = c.execute("SELECT version FROM schema_version WHERE id = 1").fetchone()[0]
        tables = [row[0] for row in c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    assert version == 8
    for table in (
        "research_design_intents",
        "spec_feasibility_decisions",
        "spec_materialization_proposals",
        "initial_experiment_plans",
        "initial_experiment_conditions",
        "initial_experiment_plan_proposals",
        "condition_execution_records",
        "parameter_sensitivity_contrast_results",
    ):
        assert table in tables
