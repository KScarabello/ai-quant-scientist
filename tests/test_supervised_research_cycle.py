from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import timedelta

import pytest

from ai_quant_scientist.capabilities import CapabilityRegistry, build_v1_registry
from ai_quant_scientist.capabilities.gate import ResearchCandidate
from ai_quant_scientist.models.design import (
    AnalysisIntent,
    ComparisonIntent,
    DesignOutcome,
    DesignVariable,
    ExperimentCondition,
    ExperimentConditionRole,
    ExpectedDirection,
    InitialExperimentCompletionRule,
    InitialExperimentPlan,
    InitialExperimentPlanProposal,
    InitialExperimentPlanProposalStatus,
    OutcomeContrast,
    OutcomePrediction,
    ParameterSensitivityContrastResult,
    ResearchDesignIntent,
    ResearchDesignKind,
    ScientificVerdictStatus,
    SpecFeasibilityPhase,
)
from ai_quant_scientist.models.hypothesis_scientist import (
    HypothesisClaimAggregation,
    HypothesisScientistDecision,
    HypothesisScientistDecisionType,
    HypothesisScientistInvocation,
    ResearchBrief,
    ResearchScope,
    ResearchScopeOutcomeAggregation,
)
from ai_quant_scientist.models.research import new_id
from ai_quant_scientist.capabilities.models import (
    AssetClass,
    DataKind,
    DataRequirement,
    FeasibilityReasonCode,
    Resolution,
    ToolKind,
    ToolRequirement,
)
from ai_quant_scientist.capabilities.serialization import requirements_to_json
from ai_quant_scientist.services.hypothesis_prompts import get_scientist_instructions
from ai_quant_scientist.services.hypothesis_scientist import FakeHypothesisScientist
from ai_quant_scientist.services.research_designer import FakeResearchDesigner
from ai_quant_scientist.models.research_designer import ResearchDesignerDecisionType
from ai_quant_scientist.models.research_designer import ResearchDesignerInvocation
from ai_quant_scientist.services.research_designer_prompts import get_research_designer_instructions
from ai_quant_scientist.services.research_design_ontology import (
    build_current_research_design_ontology_snapshot,
    build_research_design_ontology_snapshot,
)
from ai_quant_scientist.services.scientist_requirement_ontology import (
    REQUIREMENT_ONTOLOGY_VERSION,
    build_requirement_ontology_snapshot,
)
from ai_quant_scientist.services.spec_materialization import (
    GovernedSpecMaterialization,
    SpecFeasibilityStatus,
    SpecFeasibilityValidator,
    SpecMaterializer,
)
from ai_quant_scientist.services.supervised_research_cycle import (
    SupervisedResearchCycle,
    SupervisedResearchCycleExecutionStatus,
    SupervisedResearchCyclePreparationStatus,
)
from ai_quant_scientist.storage.sqlite_store import SQLiteStore
from ai_quant_scientist.evals.run_live_supervised_cycle import (
    _build_acceptance_execution_artifact,
    _build_preparation_artifact,
    build_supported_supervised_cycle_brief,
    main as live_cycle_main,
    run_live_supervised_cycle,
)


def _store(tmp_path) -> SQLiteStore:
    return SQLiteStore(tmp_path / "test.db")


def _registry() -> CapabilityRegistry:
    return build_v1_registry()


def _scope(*outcomes: DesignOutcome) -> ResearchScope:
    return ResearchScope.create(
        independent_variable=DesignVariable.SIGNAL_THRESHOLD,
        requested_outcomes=outcomes or (DesignOutcome.TRADE_COUNT, DesignOutcome.SHARPE),
        outcome_aggregation=ResearchScopeOutcomeAggregation.ALL_OUTCOMES_REQUIRED,
    )


class _CountingScientist(FakeHypothesisScientist):
    def __init__(self) -> None:
        self.called = 0

    def generate(self, brief: ResearchBrief) -> HypothesisScientistDecision:
        self.called += 1
        return super().generate(brief)


class _NoHypothesisScientist(FakeHypothesisScientist):
    def __init__(self) -> None:
        self.called = 0

    def generate(self, brief: ResearchBrief) -> HypothesisScientistDecision:
        self.called += 1
        ontology = build_requirement_ontology_snapshot()
        return HypothesisScientistDecision(
            id=new_id(),
            decision_type=HypothesisScientistDecisionType.NO_HYPOTHESIS,
            research_brief_id=brief.id,
            no_hypothesis_reason="Brief is intentionally too vague for a bounded hypothesis.",
            provider=self.provider,
            model=self.model,
            prompt_version=self.prompt_version,
            ontology_version=ontology.version,
            ontology_fingerprint=ontology.fingerprint,
        )


class _CountingDesigner(FakeResearchDesigner):
    def __init__(self) -> None:
        super().__init__(prompt_version="v3")
        self.called = 0

    def design(self, context):
        self.called += 1
        return super().design(context)


class _BlockedCapabilityScientist(FakeHypothesisScientist):
    def __init__(self) -> None:
        self.called = 0

    def generate(self, brief: ResearchBrief) -> HypothesisScientistDecision:
        self.called += 1
        ontology = build_requirement_ontology_snapshot()
        requirements = (
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
        )
        return HypothesisScientistDecision(
            id=new_id(),
            decision_type=HypothesisScientistDecisionType.PROPOSE_HYPOTHESIS,
            research_brief_id=brief.id,
            hypothesis_statement="MES order-book imbalance predicts one-second futures returns.",
            hypothesis_rationale="Requires futures order-book data plus execution support.",
            requirements_snapshot=requirements_to_json(requirements),
            independent_variable=DesignVariable.SIGNAL_THRESHOLD,
            independent_variable_direction=ExpectedDirection.INCREASE,
            outcome_claims=(
                OutcomePrediction(
                    outcome=DesignOutcome.TRADE_COUNT,
                    expected_direction=ExpectedDirection.DECREASE,
                ),
            ),
            claim_aggregation=HypothesisClaimAggregation.ALL_CLAIMS_REQUIRED,
            provider=self.provider,
            model=self.model,
            prompt_version=self.prompt_version,
            ontology_version=ontology.version,
            ontology_fingerprint=ontology.fingerprint,
        )


class _SyntheticFieldConstrainedScientist(FakeHypothesisScientist):
    def __init__(self) -> None:
        self.called = 0

    def generate(self, brief: ResearchBrief) -> HypothesisScientistDecision:
        self.called += 1
        ontology = build_requirement_ontology_snapshot()
        requirements = (
            DataRequirement(
                requirement_id="synthetic_parametric_input",
                data_kind=DataKind.SYNTHETIC_PARAMETRIC,
                asset_class=AssetClass.SYNTHETIC,
                resolution=Resolution.NOT_APPLICABLE,
                required_fields=("signal_value", "synthetic_price", "timestamp"),
            ),
            ToolRequirement(
                requirement_id="deterministic_parameter_sensitivity_backtest",
                tool_kind=ToolKind.BACKTEST_EXECUTION,
            ),
        )
        return HypothesisScientistDecision(
            id=new_id(),
            decision_type=HypothesisScientistDecisionType.PROPOSE_HYPOTHESIS,
            research_brief_id=brief.id,
            hypothesis_statement=(
                "For identical synthetic strategy logic, a stricter signal threshold reduces "
                "trade frequency and can improve risk-adjusted performance."
            ),
            hypothesis_rationale=(
                "The bounded smoke test should stay at broad candidate feasibility, but this "
                "fixture intentionally over-asserts primitive field prerequisites to verify "
                "truthful fail-closed behavior."
            ),
            requirements_snapshot=requirements_to_json(requirements),
            independent_variable=DesignVariable.SIGNAL_THRESHOLD,
            independent_variable_direction=ExpectedDirection.INCREASE,
            outcome_claims=(
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
            provider=self.provider,
            model=self.model,
            prompt_version=self.prompt_version,
            ontology_version=ontology.version,
            ontology_fingerprint=ontology.fingerprint,
        )


class _NoValidDesignDesigner(FakeResearchDesigner):
    def __init__(self) -> None:
        super().__init__(prompt_version="v3")
        self.called = 0

    def design(self, context):
        self.called += 1
        decision = super().design(context)
        return replace(
            decision,
            decision_type=ResearchDesignerDecisionType.NO_VALID_DESIGN,
            design_kind=None,
            independent_variables=None,
            dependent_outcomes=None,
            controls=None,
            comparison_intent=None,
            analysis_intent=None,
            falsification_condition=None,
            rationale=None,
            no_valid_design_reason="Candidate cannot be expressed as a bounded V1 design.",
        )


class _ComparatorRejectingValidator(SpecFeasibilityValidator):
    def validate(self, **kwargs):
        decision = super().validate(**kwargs)
        if kwargs["proposed_parameters"]["signal_threshold"] == 2.5:
            return replace(
                decision,
                status=SpecFeasibilityStatus.FAIL,
                validation_notes="comparator_rejected_for_test",
            )
        return decision


def _cycle(
    *,
    tmp_path,
    registry: CapabilityRegistry | None = None,
    scientist=None,
    designer=None,
    governed_materialization: GovernedSpecMaterialization | None = None,
) -> SupervisedResearchCycle:
    store = _store(tmp_path)
    registry = registry or _registry()
    scientist = scientist or FakeHypothesisScientist()
    designer = designer or FakeResearchDesigner()
    return SupervisedResearchCycle(
        store=store,
        registry=registry,
        scientist=scientist,
        designer=designer,
        governed_materialization=governed_materialization,
    )


def test_supervised_cycle_end_to_end_prepares_accepts_executes_and_persists(tmp_path):
    store = _store(tmp_path)
    scientist = _CountingScientist()
    designer = _CountingDesigner()
    cycle = SupervisedResearchCycle(
        store=store,
        registry=_registry(),
        scientist=scientist,
        designer=designer,
    )
    brief = build_supported_supervised_cycle_brief()

    preparation = cycle.prepare(brief)

    assert preparation.status == SupervisedResearchCyclePreparationStatus.AWAITING_HUMAN_ACCEPTANCE
    assert preparation.candidate_id is not None
    assert preparation.hypothesis_claim_set_id is not None
    assert preparation.candidate_feasibility_decision_id is not None
    assert preparation.research_design_intent_id is not None
    assert preparation.research_prediction_plan_id is not None
    assert preparation.initial_experiment_plan_id is not None
    assert preparation.materialization_proposal_id is not None
    assert scientist.called == 1
    assert designer.called == 1

    candidate = store.get_research_candidate(preparation.candidate_id)
    claim_set = store.get_hypothesis_claim_set(preparation.hypothesis_claim_set_id)
    feasibility = store.get_feasibility_decision(preparation.candidate_feasibility_decision_id)
    intents = store.list_research_design_intents(preparation.candidate_id)
    plan = store.get_initial_experiment_plan(preparation.initial_experiment_plan_id)
    proposal = store.get_initial_experiment_plan_proposal(preparation.materialization_proposal_id)
    prediction_plan = store.get_research_prediction_plan(preparation.research_prediction_plan_id)
    assert candidate is not None
    assert claim_set is not None
    assert feasibility is not None
    assert plan is not None
    assert proposal is not None
    assert prediction_plan is not None
    assert len(intents) == 1
    assert plan.candidate_feasibility_decision_id == preparation.candidate_feasibility_decision_id
    assert proposal.candidate_feasibility_decision_id == preparation.candidate_feasibility_decision_id
    assert plan.design_intent_id == preparation.research_design_intent_id
    assert plan.research_prediction_plan_id == preparation.research_prediction_plan_id
    assert proposal.design_intent_id == preparation.research_design_intent_id
    assert prediction_plan.design_intent_id == preparation.research_design_intent_id
    assert brief.research_scope is not None
    assert {item.value for item in brief.research_scope.requested_outcomes} == {"trade_count", "sharpe"}
    assert {item.outcome.value for item in claim_set.claims} == {"trade_count", "sharpe"}
    assert {item.value for item in intents[0].dependent_outcomes} == {"trade_count", "sharpe"}
    assert {item.outcome.value for item in prediction_plan.predictions} == {"trade_count", "sharpe"}
    assert brief.research_scope.independent_variable == claim_set.independent_variable
    assert claim_set.independent_variable in intents[0].independent_variables
    assert prediction_plan.independent_variable == claim_set.independent_variable
    assert len(plan.ordered_conditions) == 2
    baseline, comparator = plan.ordered_conditions
    assert baseline.ordinal == 1
    assert comparator.ordinal == 2
    assert baseline.exact_parameters["signal_threshold"] == 2.0
    assert comparator.exact_parameters["signal_threshold"] == 2.5
    assert baseline.exact_parameters["lookback"] == comparator.exact_parameters["lookback"] == 20
    assert {
        key
        for key in baseline.exact_parameters
        if baseline.exact_parameters[key] != comparator.exact_parameters[key]
    } == {"signal_threshold"}

    execution = cycle.accept_and_execute(preparation.materialization_proposal_id)

    assert execution.status == SupervisedResearchCycleExecutionStatus.COMPLETED
    assert execution.contrast_result_id is not None
    assert execution.scientific_verdict_id is not None
    assert scientist.called == 1
    assert designer.called == 1

    records = store.list_condition_execution_records(plan.id)
    contrast = store.get_parameter_sensitivity_contrast_result(plan.id)
    verdict = store.get_scientific_verdict(execution.scientific_verdict_id)
    assert len(records) == 2
    assert [record.ordinal for record in records] == [1, 2]
    assert contrast is not None
    assert verdict is not None
    assert {outcome.outcome.value for outcome in contrast.outcomes} == {
        "trade_count",
        "sharpe",
    }
    assert verdict.overall_status == ScientificVerdictStatus.FALSIFIED
    assert store.get_hypothesis_scientist_invocations(brief.id)
    assert store.get_research_designer_invocations(preparation.candidate_id)
    with store.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM critic_invocations").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM spec_revision_proposals").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM research_runs").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM scientific_verdicts").fetchone()[0] == 1


def test_no_hypothesis_stops_before_intake_and_design(tmp_path):
    store = _store(tmp_path)
    scientist = _NoHypothesisScientist()
    designer = _CountingDesigner()
    cycle = SupervisedResearchCycle(
        store=store,
        registry=_registry(),
        scientist=scientist,
        designer=designer,
    )
    brief = ResearchBrief.create(
        research_question="General explore the space in an underspecified way.",
        research_scope=_scope(),
    )

    result = cycle.prepare(brief)

    assert result.status == SupervisedResearchCyclePreparationStatus.NO_HYPOTHESIS
    assert result.candidate_id is None
    assert designer.called == 0
    assert store.list_research_candidates() == []
    assert store.get_hypothesis_scientist_invocations(brief.id)
    with store.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM feasibility_decisions").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM research_designer_invocations").fetchone()[0] == 0


def test_blocked_capability_stops_before_design(tmp_path):
    store = _store(tmp_path)
    scientist = _BlockedCapabilityScientist()
    designer = _CountingDesigner()
    cycle = SupervisedResearchCycle(
        store=store,
        registry=_registry(),
        scientist=scientist,
        designer=designer,
    )
    brief = ResearchBrief.create(
        research_question="Does MES order-book imbalance predict one-second futures returns?",
        asset_class_focus="FUTURES",
        instrument_focus=["MES"],
        research_scope=_scope(DesignOutcome.TRADE_COUNT),
    )

    result = cycle.prepare(brief)

    assert result.status == SupervisedResearchCyclePreparationStatus.BLOCKED_CAPABILITY
    assert result.candidate_id is not None
    assert result.candidate_feasibility_decision_id is not None
    assert designer.called == 0
    with store.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM research_designer_invocations").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM initial_experiment_plans").fetchone()[0] == 0


def test_no_valid_design_stops_before_materialization(tmp_path):
    store = _store(tmp_path)
    scientist = _CountingScientist()
    designer = _NoValidDesignDesigner()
    cycle = SupervisedResearchCycle(
        store=store,
        registry=_registry(),
        scientist=scientist,
        designer=designer,
    )
    brief = build_supported_supervised_cycle_brief()

    result = cycle.prepare(brief)

    assert result.status == SupervisedResearchCyclePreparationStatus.NO_VALID_DESIGN
    assert result.candidate_id is not None
    assert result.research_designer_invocation_id is not None
    assert result.research_design_intent_id is None
    with store.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM initial_experiment_plans").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM initial_experiment_plan_proposals").fetchone()[0] == 0


def test_materialization_infeasible_stops_before_acceptance_and_execution(tmp_path):
    store = _store(tmp_path)
    governed_materialization = GovernedSpecMaterialization(
        store=store,
        registry=_registry(),
        materializer=SpecMaterializer(feasibility_validator=_ComparatorRejectingValidator()),
    )
    cycle = SupervisedResearchCycle(
        store=store,
        registry=_registry(),
        scientist=FakeHypothesisScientist(),
        designer=FakeResearchDesigner(),
        governed_materialization=governed_materialization,
    )

    result = cycle.prepare(build_supported_supervised_cycle_brief())

    assert result.status == SupervisedResearchCyclePreparationStatus.MATERIALIZATION_INFEASIBLE
    assert result.initial_experiment_plan_id is not None
    assert result.materialization_proposal_id is not None
    proposal = store.get_initial_experiment_plan_proposal(result.materialization_proposal_id)
    assert proposal is not None
    assert proposal.status == InitialExperimentPlanProposalStatus.REJECTED
    assert store.list_condition_execution_records(result.initial_experiment_plan_id) == []


def test_prepare_valid_plan_does_not_execute_before_explicit_acceptance(tmp_path):
    store = _store(tmp_path)
    cycle = SupervisedResearchCycle(
        store=store,
        registry=_registry(),
        scientist=FakeHypothesisScientist(),
        designer=FakeResearchDesigner(),
    )

    result = cycle.prepare(build_supported_supervised_cycle_brief())

    assert result.status == SupervisedResearchCyclePreparationStatus.AWAITING_HUMAN_ACCEPTANCE
    assert result.initial_experiment_plan_id is not None
    assert store.list_condition_execution_records(result.initial_experiment_plan_id) == []
    assert store.get_parameter_sensitivity_contrast_result(result.initial_experiment_plan_id) is None
    with store.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM scientific_verdicts").fetchone()[0] == 0


def test_acceptance_time_feasibility_failure_returns_typed_failure(tmp_path):
    store = _store(tmp_path)
    cycle = SupervisedResearchCycle(
        store=store,
        registry=_registry(),
        scientist=FakeHypothesisScientist(),
        designer=FakeResearchDesigner(),
    )
    preparation = cycle.prepare(build_supported_supervised_cycle_brief())
    assert preparation.materialization_proposal_id is not None

    disabled_registry = CapabilityRegistry(
        [replace(next(c for c in build_v1_registry().list_capabilities() if c.capability_id == "stub_backtester_v1"), enabled=False)]
    )
    blocked_cycle = SupervisedResearchCycle(
        store=store,
        registry=disabled_registry,
        scientist=FakeHypothesisScientist(),
        designer=FakeResearchDesigner(),
    )

    execution = blocked_cycle.accept_and_execute(preparation.materialization_proposal_id)

    assert execution.status == SupervisedResearchCycleExecutionStatus.ACCEPTANCE_FAILED
    acceptance_decisions = [
        decision
        for decision in store.list_spec_feasibility_decisions(preparation.candidate_id)
        if decision.phase == SpecFeasibilityPhase.ACCEPTANCE
    ]
    assert len(acceptance_decisions) == 2
    assert store.list_condition_execution_records(preparation.initial_experiment_plan_id) == []


def test_v15_tampered_prediction_plan_reference_fails_before_execution(tmp_path):
    store = _store(tmp_path)
    cycle = SupervisedResearchCycle(
        store=store,
        registry=_registry(),
        scientist=FakeHypothesisScientist(),
        designer=FakeResearchDesigner(),
    )
    preparation = cycle.prepare(build_supported_supervised_cycle_brief())
    assert preparation.materialization_proposal_id is not None
    assert preparation.initial_experiment_plan_id is not None

    with store.connect() as conn:
        conn.execute(
            "UPDATE initial_experiment_plans SET research_prediction_plan_id = ? WHERE id = ?",
            ("missing-prediction-plan", preparation.initial_experiment_plan_id),
        )

    execution = cycle.accept_and_execute(preparation.materialization_proposal_id)

    assert execution.status == SupervisedResearchCycleExecutionStatus.ACCEPTANCE_FAILED
    assert "missing ResearchPredictionPlan" in (execution.message or "")
    assert store.list_condition_execution_records(preparation.initial_experiment_plan_id) == []
    with store.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM scientific_verdicts").fetchone()[0] == 0


def test_retry_execution_does_not_duplicate_completed_conditions(tmp_path):
    store = _store(tmp_path)
    cycle = SupervisedResearchCycle(
        store=store,
        registry=_registry(),
        scientist=FakeHypothesisScientist(),
        designer=FakeResearchDesigner(),
    )
    preparation = cycle.prepare(build_supported_supervised_cycle_brief())
    assert preparation.materialization_proposal_id is not None
    assert preparation.initial_experiment_plan_id is not None

    first = cycle.accept_and_execute(preparation.materialization_proposal_id)
    second = cycle.accept_and_execute(preparation.materialization_proposal_id)

    assert first.status == SupervisedResearchCycleExecutionStatus.COMPLETED
    assert second.status == SupervisedResearchCycleExecutionStatus.COMPLETED
    assert first.contrast_result_id == second.contrast_result_id
    assert first.scientific_verdict_id == second.scientific_verdict_id
    assert len(store.list_condition_execution_records(preparation.initial_experiment_plan_id)) == 2
    with store.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM scientific_verdicts").fetchone()[0] == 1


def test_wrong_proposal_id_fails_closed_without_substituting_latest(tmp_path):
    store = _store(tmp_path)
    cycle = SupervisedResearchCycle(
        store=store,
        registry=_registry(),
        scientist=FakeHypothesisScientist(),
        designer=FakeResearchDesigner(),
    )
    first = cycle.prepare(build_supported_supervised_cycle_brief())
    second = cycle.prepare(build_supported_supervised_cycle_brief())
    assert first.materialization_proposal_id is not None
    assert second.materialization_proposal_id is not None

    with pytest.raises(KeyError):
        cycle.accept_and_execute("missing-proposal-id")

    execution = cycle.accept_and_execute(first.materialization_proposal_id)
    assert execution.initial_experiment_plan_id == first.initial_experiment_plan_id
    assert execution.materialization_proposal_id == first.materialization_proposal_id


def test_completed_historical_v014_plan_remains_readable_and_does_not_fabricate_verdict_on_retry(tmp_path):
    store = _store(tmp_path)
    candidate = ResearchCandidate.create(
        hypothesis_statement="Historical threshold hypothesis.",
        hypothesis_rationale="Historical completed plan without precommitted predictions.",
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
    store.save_research_candidate(candidate)
    design_intent = ResearchDesignIntent.create(
        candidate_id=candidate.id,
        design_kind=ResearchDesignKind.PARAMETER_SENSITIVITY,
        independent_variables=(DesignVariable.SIGNAL_THRESHOLD,),
        dependent_outcomes=(DesignOutcome.SHARPE, DesignOutcome.TRADE_COUNT),
        controls=(DesignVariable.LOOKBACK,),
        comparison_intent=ComparisonIntent.CONTRAST_PARAMETER_LEVELS,
        analysis_intent=AnalysisIntent.SENSITIVITY_ANALYSIS,
        falsification_condition="Historical V0.14 design intent.",
        rationale="Historical completed plan without machine-readable predictions.",
        source="historical_v014",
    )
    store.save_research_design_intent(design_intent)
    plan = InitialExperimentPlan(
        id=new_id(),
        candidate_id=candidate.id,
        design_intent_id=design_intent.id,
        candidate_feasibility_decision_id="historical-ready-1",
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
        research_prediction_plan_id=None,
    )
    store.save_initial_experiment_plan(plan)
    proposal = InitialExperimentPlanProposal(
        id="7d4c04d5-9f36-49bc-ab15-8cd630f10999",
        plan_id=plan.id,
        candidate_id=candidate.id,
        design_intent_id=design_intent.id,
        candidate_feasibility_decision_id="historical-ready-1",
        materialization_feasibility_decision_ids=("hist-feasibility-1", "hist-feasibility-2"),
        materialization_trace={},
        status=InitialExperimentPlanProposalStatus.COMPLETED,
        contrast_result_id="historical-contrast-1",
    )
    store.save_initial_experiment_plan_proposal(proposal)
    contrast = ParameterSensitivityContrastResult(
        id="historical-contrast-1",
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
    store.save_parameter_sensitivity_contrast_result(contrast)

    cycle = SupervisedResearchCycle(
        store=store,
        registry=_registry(),
        scientist=FakeHypothesisScientist(),
        designer=FakeResearchDesigner(),
    )

    execution = cycle.accept_and_execute(proposal.id)

    assert execution.status == SupervisedResearchCycleExecutionStatus.COMPLETED
    assert execution.contrast_result_id == contrast.id
    assert execution.scientific_verdict_id is None
    assert store.get_parameter_sensitivity_contrast_result(plan.id).id == contrast.id
    with store.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM scientific_verdicts").fetchone()[0] == 0


def test_supervised_cycle_integration_preserves_prompt_and_ontology_hashes():
    assert build_requirement_ontology_snapshot().version == REQUIREMENT_ONTOLOGY_VERSION
    assert build_requirement_ontology_snapshot().fingerprint == (
        "832885f4763e40b8a379c8c9c475484651b0a0f1c7fb01305d3d37fe4172c917"
    )
    assert build_research_design_ontology_snapshot().fingerprint == (
        "7fd37d3302833d582bde6ad8b17b6b7c1be2d52e8f345b5156037e2c3058002e"
    )
    assert build_current_research_design_ontology_snapshot().fingerprint == (
        "792528b090a549609e03484afdee4ea661ae247e9affe9416e62aae1f7b99183"
    )
    assert hashlib.sha256(get_scientist_instructions("v3").encode("utf-8")).hexdigest() == (
        "aa89aa587b8b26332562b2055eeb2813dff148201d96bec8bf79eed34b93661a"
    )
    assert hashlib.sha256(get_scientist_instructions("v4").encode("utf-8")).hexdigest() == (
        "71f7e593b93ec6568f123209e9183483c6e19e7affbc3824f507dfdf992861ef"
    )
    assert hashlib.sha256(get_scientist_instructions("v5").encode("utf-8")).hexdigest() == (
        "568a07e7467df49401e97120735d0ed650458d0ececb4a8b5cdd33c2e694d3dd"
    )
    assert hashlib.sha256(get_research_designer_instructions("v1").encode("utf-8")).hexdigest() == (
        "8744692f166fdb6058a4597abb6bcbad17489817efc1879c3506643e1d922fac"
    )
    assert hashlib.sha256(get_research_designer_instructions("v2").encode("utf-8")).hexdigest() == (
        "721392d5160f82c8de83eaef67f4c3fc96fc13872bd1823f43b7c681737187cb"
    )
    assert hashlib.sha256(get_research_designer_instructions("v3").encode("utf-8")).hexdigest() == (
        "2f94172fb0219955bced7deab320d778ab4e83fe8c8e57466aeeed707955df36"
    )


def test_live_supervised_cycle_requires_allow_live_api():
    with pytest.raises(RuntimeError, match="--allow-live-api"):
        run_live_supervised_cycle(model="test", allow_live_api=False)


def test_supported_live_fixture_constrains_v1_prerequisite_path():
    brief = build_supported_supervised_cycle_brief()
    text = " ".join(
        [
            brief.research_question,
            *(brief.methodological_constraints or ()),
            *(brief.exclusions or ()),
        ]
    )
    assert "synthetic-parametric dataset as the prerequisite input" in text
    assert "BACKTEST_EXECUTION" in text
    assert "separate synthetic-data-generation tool" in text
    assert "separate statistical-analysis tool" in text
    assert "Do not require particular primitive synthetic data fields" in text
    assert "Leave required_fields unset" in text
    assert "Do not assert required_parameters" in text
    assert "2.0" not in text
    assert "2.5" not in text
    assert "20" not in text
    assert "stub_backtester_v1" not in text
    assert brief.research_scope is not None
    assert brief.research_scope.independent_variable == DesignVariable.SIGNAL_THRESHOLD
    assert [item.value for item in brief.research_scope.requested_outcomes] == ["sharpe", "trade_count"]


def test_supported_fake_candidate_keeps_candidate_feasibility_broad(tmp_path):
    store = _store(tmp_path)
    cycle = SupervisedResearchCycle(
        store=store,
        registry=_registry(),
        scientist=FakeHypothesisScientist(),
        designer=FakeResearchDesigner(),
    )

    preparation = cycle.prepare(build_supported_supervised_cycle_brief())
    candidate = store.get_research_candidate(preparation.candidate_id)

    assert candidate is not None
    data_requirements = [req for req in candidate.requirements if isinstance(req, DataRequirement)]
    tool_requirements = [req for req in candidate.requirements if isinstance(req, ToolRequirement)]

    assert len(data_requirements) == 1
    assert data_requirements[0].data_kind == DataKind.SYNTHETIC_PARAMETRIC
    assert data_requirements[0].required_fields is None
    assert data_requirements[0].required_parameters is None
    assert [req.tool_kind for req in tool_requirements] == [ToolKind.BACKTEST_EXECUTION]
    assert preparation.research_prediction_plan_id is not None


def test_live_runner_preparation_mode_creates_one_plan_and_zero_conditions(tmp_path, monkeypatch):
    store = _store(tmp_path)
    scientist = _CountingScientist()
    designer = _CountingDesigner()

    monkeypatch.setattr(
        "ai_quant_scientist.evals.run_live_supervised_cycle.OpenAIHypothesisScientist",
        lambda model, prompt_version: scientist,
    )
    monkeypatch.setattr(
        "ai_quant_scientist.evals.run_live_supervised_cycle.OpenAIResearchDesigner",
        lambda model, prompt_version: designer,
    )

    out = run_live_supervised_cycle(
        model="test-model",
        allow_live_api=True,
        output_dir=str(tmp_path),
        db_path=str(store.db_path),
    )

    payload = json.loads(open(out, "r", encoding="utf-8").read())
    assert payload["mode"] == "prepare"
    assert payload["canonical_research_scope"]["requested_outcomes"] == ["sharpe", "trade_count"]
    assert payload["preparation_outcome"] == SupervisedResearchCyclePreparationStatus.AWAITING_HUMAN_ACCEPTANCE.value
    assert payload["research_prediction_plan_id"] is not None
    assert payload["structured_predictions"] is not None
    assert payload["materialization_proposal_id"] is not None
    assert scientist.called == 1
    assert designer.called == 1
    assert payload["scientific_verdict"] is None
    with store.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM research_candidates").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM research_design_intents").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM initial_experiment_plans").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM initial_experiment_plan_proposals").fetchone()[0] == 1
    assert payload["execution_records"] == []


def test_supported_live_fixture_still_reaches_awaiting_human_acceptance_with_fake_path(tmp_path):
    cycle = SupervisedResearchCycle(
        store=_store(tmp_path),
        registry=_registry(),
        scientist=FakeHypothesisScientist(),
        designer=FakeResearchDesigner(),
    )
    result = cycle.prepare(build_supported_supervised_cycle_brief())
    assert result.status == SupervisedResearchCyclePreparationStatus.AWAITING_HUMAN_ACCEPTANCE


def test_explicit_synthetic_fields_still_fail_closed_and_artifact_reports_diagnostics(tmp_path):
    store = _store(tmp_path)
    cycle = SupervisedResearchCycle(
        store=store,
        registry=_registry(),
        scientist=_SyntheticFieldConstrainedScientist(),
        designer=FakeResearchDesigner(),
    )

    brief = build_supported_supervised_cycle_brief()
    preparation = cycle.prepare(brief)
    feasibility = store.get_feasibility_decision(preparation.candidate_feasibility_decision_id)
    artifact = _build_preparation_artifact(
        store=store,
        model="test-model",
        brief=brief,
        preparation=preparation,
    )

    assert preparation.status == SupervisedResearchCyclePreparationStatus.BLOCKED_CAPABILITY
    assert preparation.research_designer_invocation_id is None
    assert feasibility is not None
    assert feasibility.satisfied_ids == ("deterministic_parameter_sensitivity_backtest",)
    assert feasibility.unsatisfied_ids == ("synthetic_parametric_input",)
    assert feasibility.reason_codes == (FeasibilityReasonCode.REQUIRED_FIELD_MISSING,)
    assert artifact["candidate_feasibility_decision"]["satisfied_ids"] == [
        "deterministic_parameter_sensitivity_backtest"
    ]
    assert artifact["candidate_feasibility_decision"]["unsatisfied_ids"] == [
        "synthetic_parametric_input"
    ]
    assert artifact["candidate_feasibility_decision"]["reason_codes"] == [
        FeasibilityReasonCode.REQUIRED_FIELD_MISSING.value
    ]


def test_stub_registry_fields_remain_unbroadened_for_smoke_test():
    stub = build_v1_registry().list_capabilities()[0]

    assert "signal_value" not in stub.available_fields
    assert "synthetic_price" not in stub.available_fields
    assert "timestamp" not in stub.available_fields
    assert set(stub.supported_parameters) == {"signal_threshold", "lookback"}


def test_live_runner_acceptance_mode_uses_explicit_existing_proposal_and_zero_ai_calls(tmp_path, monkeypatch):
    store = _store(tmp_path)
    cycle = SupervisedResearchCycle(
        store=store,
        registry=_registry(),
        scientist=FakeHypothesisScientist(),
        designer=FakeResearchDesigner(),
    )
    preparation = cycle.prepare(build_supported_supervised_cycle_brief())
    assert preparation.materialization_proposal_id is not None
    assert preparation.initial_experiment_plan_id is not None

    with store.connect() as conn:
        before_candidates = conn.execute("SELECT COUNT(*) FROM research_candidates").fetchone()[0]
        before_intents = conn.execute("SELECT COUNT(*) FROM research_design_intents").fetchone()[0]
        before_plans = conn.execute("SELECT COUNT(*) FROM initial_experiment_plans").fetchone()[0]
        before_proposals = conn.execute("SELECT COUNT(*) FROM initial_experiment_plan_proposals").fetchone()[0]

    monkeypatch.setattr(
        "ai_quant_scientist.evals.run_live_supervised_cycle.OpenAIHypothesisScientist",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("scientist constructor should not run")),
    )
    monkeypatch.setattr(
        "ai_quant_scientist.evals.run_live_supervised_cycle.OpenAIResearchDesigner",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("designer constructor should not run")),
    )

    out = run_live_supervised_cycle(
        proposal_id=preparation.materialization_proposal_id,
        accept_and_execute=True,
        output_dir=str(tmp_path),
        db_path=str(store.db_path),
    )

    payload = json.loads(open(out, "r", encoding="utf-8").read())
    assert payload["mode"] == "accept_and_execute"
    assert payload["requested_proposal_id"] == preparation.materialization_proposal_id
    assert payload["materialization_proposal_id"] == preparation.materialization_proposal_id
    assert payload["initial_experiment_plan_id"] == preparation.initial_experiment_plan_id
    assert payload["human_acceptance_occurred"] is True
    assert payload["research_prediction_plan_id"] == preparation.research_prediction_plan_id
    assert payload["structured_predictions"] is not None
    assert len(payload["execution_records"]) == 2
    assert payload["contrast_result"] is not None
    assert payload["scientific_verdict"] is not None

    with store.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM research_candidates").fetchone()[0] == before_candidates
        assert conn.execute("SELECT COUNT(*) FROM research_design_intents").fetchone()[0] == before_intents
        assert conn.execute("SELECT COUNT(*) FROM initial_experiment_plans").fetchone()[0] == before_plans
        assert conn.execute("SELECT COUNT(*) FROM initial_experiment_plan_proposals").fetchone()[0] == before_proposals


def test_live_runner_wrong_proposal_id_fails_closed(tmp_path):
    store = _store(tmp_path)
    with pytest.raises(KeyError):
        run_live_supervised_cycle(
            proposal_id="missing-proposal-id",
            accept_and_execute=True,
            output_dir=str(tmp_path),
            db_path=str(store.db_path),
        )


def test_live_runner_main_reports_blocked_capability_without_fake_proposal(tmp_path, monkeypatch, capsys):
    artifact_path = tmp_path / "blocked.json"
    artifact_path.write_text(
        json.dumps(
            {
                "preparation_outcome": "BLOCKED_CAPABILITY",
                "materialization_proposal_id": None,
                "preparation_message": "Candidate feasibility gate returned BLOCKED_CAPABILITY.",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "ai_quant_scientist.evals.run_live_supervised_cycle.run_live_supervised_cycle",
        lambda **kwargs: str(artifact_path),
    )

    rc = live_cycle_main(["--model", "test-model", "--allow-live-api"])
    output = capsys.readouterr().out

    assert rc == 0
    assert "Preparation outcome: BLOCKED_CAPABILITY" in output
    assert "No proposal exists to accept or execute." in output
    assert "Prepared exact proposal_id" not in output
    assert "AWAITING_HUMAN_ACCEPTANCE" not in output


def test_live_runner_main_reports_exact_awaiting_human_acceptance_proposal(tmp_path, monkeypatch, capsys):
    artifact_path = tmp_path / "awaiting.json"
    artifact_path.write_text(
        json.dumps(
            {
                "preparation_outcome": "AWAITING_HUMAN_ACCEPTANCE",
                "materialization_proposal_id": "proposal-123",
                "preparation_message": "Preparation completed.",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "ai_quant_scientist.evals.run_live_supervised_cycle.run_live_supervised_cycle",
        lambda **kwargs: str(artifact_path),
    )

    rc = live_cycle_main(["--model", "test-model", "--allow-live-api"])
    output = capsys.readouterr().out

    assert rc == 0
    assert "Prepared exact proposal_id: proposal-123" in output
    assert "AWAITING_HUMAN_ACCEPTANCE" in output
    assert "No proposal exists to accept or execute." not in output


@pytest.mark.parametrize(
    ("outcome", "message"),
    [
        ("NO_HYPOTHESIS", "Brief is too vague."),
        ("NO_VALID_DESIGN", "Candidate cannot be expressed as a bounded V1 design."),
        ("MATERIALIZATION_INFEASIBLE", "Exact materialization feasibility did not pass."),
    ],
)
def test_live_runner_main_reports_other_stop_states(tmp_path, monkeypatch, capsys, outcome, message):
    artifact_path = tmp_path / f"{outcome}.json"
    artifact_path.write_text(
        json.dumps(
            {
                "preparation_outcome": outcome,
                "materialization_proposal_id": None,
                "preparation_message": message,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "ai_quant_scientist.evals.run_live_supervised_cycle.run_live_supervised_cycle",
        lambda **kwargs: str(artifact_path),
    )

    rc = live_cycle_main(["--model", "test-model", "--allow-live-api"])
    output = capsys.readouterr().out

    assert rc == 0
    assert f"Preparation outcome: {outcome}" in output
    assert f"Message: {message}" in output
    assert "Prepared exact proposal_id" not in output


def test_live_runner_accepting_proposal_a_cannot_execute_proposal_b(tmp_path):
    store = _store(tmp_path)
    cycle = SupervisedResearchCycle(
        store=store,
        registry=_registry(),
        scientist=FakeHypothesisScientist(),
        designer=FakeResearchDesigner(),
    )
    first = cycle.prepare(build_supported_supervised_cycle_brief())
    second = cycle.prepare(build_supported_supervised_cycle_brief())
    assert first.materialization_proposal_id is not None
    assert second.materialization_proposal_id is not None
    assert first.initial_experiment_plan_id is not None
    assert second.initial_experiment_plan_id is not None

    out = run_live_supervised_cycle(
        proposal_id=first.materialization_proposal_id,
        accept_and_execute=True,
        output_dir=str(tmp_path),
        db_path=str(store.db_path),
    )

    payload = json.loads(open(out, "r", encoding="utf-8").read())
    assert payload["materialization_proposal_id"] == first.materialization_proposal_id
    assert payload["initial_experiment_plan_id"] == first.initial_experiment_plan_id
    assert store.get_parameter_sensitivity_contrast_result(first.initial_experiment_plan_id) is not None
    assert payload["scientific_verdict"] is not None
    assert store.get_parameter_sensitivity_contrast_result(second.initial_experiment_plan_id) is None
    second_proposal = store.get_initial_experiment_plan_proposal(second.materialization_proposal_id)
    assert second_proposal is not None
    assert second_proposal.status == InitialExperimentPlanProposalStatus.PROPOSED


def test_preparation_artifact_reconstruction_uses_exact_invocation_ids_not_latest(tmp_path):
    store = _store(tmp_path)
    cycle = SupervisedResearchCycle(
        store=store,
        registry=_registry(),
        scientist=FakeHypothesisScientist(),
        designer=FakeResearchDesigner(),
    )
    brief = build_supported_supervised_cycle_brief()
    preparation = cycle.prepare(brief)
    assert preparation.candidate_id is not None
    assert preparation.research_designer_invocation_id is not None

    original_hypothesis = store.get_hypothesis_scientist_invocation(preparation.hypothesis_scientist_invocation_id)
    original_designer = store.get_research_designer_invocation(preparation.research_designer_invocation_id)
    assert original_hypothesis is not None
    assert original_designer is not None

    store.save_hypothesis_scientist_invocation(
        HypothesisScientistInvocation(
            id=new_id(),
            research_brief_id=brief.id,
            research_brief_snapshot=brief.research_question,
            prompt_version="v3",
            provider="bogus",
            model="bogus",
            raw_response=None,
            parsed_decision_json=json.dumps({"decision_type": "NO_HYPOTHESIS", "ontology_version": "bogus"}),
            validation_status="VALID",
            validation_errors_json=None,
            resulting_candidate_id=None,
            created_at=original_hypothesis.created_at + timedelta(seconds=1),
        )
    )
    store.save_research_designer_invocation(
        ResearchDesignerInvocation(
            id=new_id(),
            candidate_id=preparation.candidate_id,
            candidate_snapshot_json="{}",
            candidate_feasibility_decision_id=preparation.candidate_feasibility_decision_id,
            prompt_version="v1",
            ontology_version="bogus",
            ontology_fingerprint="0" * 64,
            intent_contract_version="research_design_intent_v1",
            provider="bogus",
            model="bogus",
            raw_response=None,
            parsed_decision_json=json.dumps({"decision_type": "NO_VALID_DESIGN", "ontology_version": "bogus"}),
            validation_status="VALID",
            validation_errors_json=None,
            resulting_design_intent_id=None,
            created_at=original_designer.created_at + timedelta(seconds=1),
        )
    )

    artifact = _build_preparation_artifact(
        store=store,
        model="test-model",
        brief=brief,
        preparation=preparation,
    )

    assert artifact["hypothesis_scientist_invocation_id"] == original_hypothesis.id
    assert artifact["research_designer_invocation_id"] == original_designer.id
    assert artifact["canonical_research_scope"]["requested_outcomes"] == ["sharpe", "trade_count"]
    assert artifact["hypothesis_decision"]["decision_type"] == "PROPOSE_HYPOTHESIS"
    assert artifact["designer_decision"]["decision_type"] == "DESIGN"
    assert artifact["research_prediction_plan_id"] == preparation.research_prediction_plan_id
    assert artifact["structured_predictions"] is not None


def test_acceptance_artifact_reloads_persisted_state_and_reports_exact_records(tmp_path):
    store = _store(tmp_path)
    cycle = SupervisedResearchCycle(
        store=store,
        registry=_registry(),
        scientist=FakeHypothesisScientist(),
        designer=FakeResearchDesigner(),
    )
    brief = build_supported_supervised_cycle_brief()
    preparation = cycle.prepare(brief)
    assert preparation.materialization_proposal_id is not None
    assert preparation.initial_experiment_plan_id is not None

    original_hypothesis = store.get_hypothesis_scientist_invocation(preparation.hypothesis_scientist_invocation_id)
    original_designer = store.get_research_designer_invocation(preparation.research_designer_invocation_id)
    assert original_hypothesis is not None
    assert original_designer is not None

    execution = cycle.accept_and_execute(preparation.materialization_proposal_id)
    artifact = _build_acceptance_execution_artifact(
        store=store,
        proposal_id=preparation.materialization_proposal_id,
        execution=execution,
    )

    assert artifact["materialization_proposal_id"] == preparation.materialization_proposal_id
    assert artifact["initial_experiment_plan_id"] == preparation.initial_experiment_plan_id
    assert artifact["human_acceptance_occurred"] is True
    assert artifact["hypothesis_scientist_invocation_id"] == original_hypothesis.id
    assert artifact["research_designer_invocation_id"] == original_designer.id
    assert len(artifact["execution_records"]) == 2
    assert artifact["contrast_result"] is not None
    assert artifact["research_prediction_plan_id"] == preparation.research_prediction_plan_id
    assert artifact["scientific_verdict"] is not None


def test_schema_remains_v12(tmp_path):
    store = _store(tmp_path)
    with store.connect() as conn:
        assert conn.execute("SELECT version FROM schema_version WHERE id = 1").fetchone()[0] == 12


def test_registry_truth_remains_unchanged():
    assert build_v1_registry().fingerprint == "be41e1bf7e9b4b84fb4e8353631da67486ee5b7f84f6fa43eeb52aa3dd754a53"
