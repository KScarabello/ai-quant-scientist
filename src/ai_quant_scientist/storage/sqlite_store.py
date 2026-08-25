"""SQLite-backed authoritative storage for research runs and audit records."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from ..models.evaluation import EvaluationDecision, EvaluationRecommendation, ResultEvaluationPolicy
from ..models.enums import ResearchStage, RunStatus
from ..models.research import (
    AuditEvent,
    ExperimentResult,
    Hypothesis,
    ResearchAttempt,
    ResearchRun,
    ResearchSpec,
    SpecRevisionProposal,
    freeze_json_value,
    record_to_state,
    thaw_json_value,
)
from ..models.research import new_id
from ..models.enums import ResearchAction, SpecRevisionProposalStatus
from ..models.critic import CriticInvocation, CriticDecision
from ..models.design import (
    AnalysisIntent,
    ComparisonIntent,
    DesignOutcome,
    DesignVariable,
    ConditionExecutionRecord,
    ConditionExecutionStatus,
    ExpectedDirection,
    ExperimentCondition,
    ExperimentConditionRole,
    InitialExperimentCompletionRule,
    InitialExperimentPlan,
    InitialExperimentPlanProposal,
    InitialExperimentPlanProposalStatus,
    OutcomeContrast,
    OutcomePrediction,
    OutcomeScientificVerdict,
    ParameterSensitivityContrastResult,
    PredictionAggregationRule,
    ResearchDesignIntent,
    ResearchDesignKind,
    ResearchPredictionPlan,
    ScientificVerdict,
    ScientificVerdictStatus,
    PredictionVerdictResult,
    SpecFeasibilityDecision,
    SpecFeasibilityPhase,
    SpecFeasibilityReasonCode,
    SpecFeasibilityStatus,
    SpecMaterializationProposal,
    SpecMaterializationProposalStatus,
    thaw_mapping,
)
from ..models.hypothesis_scientist import (
    HypothesisClaimAggregation,
    HypothesisClaimSet,
    HypothesisScientistInvocation,
)
from ..models.post_verdict_critic import (
    PostVerdictCriticDecisionType,
    PostVerdictCriticInvocation,
    PostVerdictResearchIntent,
    PostVerdictRevisionKind,
)
from ..models.research_continuation import (
    AdaptiveHypothesisLineage,
    ResearchContinuationAttemptStatus,
    ResearchContinuationAuthorization,
    ResearchContinuationAuthorizationStatus,
    ResearchContinuationInvocation,
    ResearchContinuationOrigin,
)
from ..models.research_designer import ResearchDesignerInvocation


SCHEMA_VERSION = 13


class SQLiteStore:
    """Small SQLite store with explicit methods for authoritative research state."""

    def __init__(self, db_path: str | Path = "data/ai_quant_scientist.db") -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(
                """
                PRAGMA foreign_keys = ON;
                CREATE TABLE IF NOT EXISTS schema_version (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    version INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS research_runs (
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
                CREATE TABLE IF NOT EXISTS hypotheses (
                    id TEXT PRIMARY KEY,
                    research_run_id TEXT NOT NULL,
                    statement TEXT NOT NULL,
                    rationale TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (research_run_id) REFERENCES research_runs(id)
                );
                CREATE TABLE IF NOT EXISTS research_specs (
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
                    is_frozen INTEGER NOT NULL,
                    FOREIGN KEY (research_run_id) REFERENCES research_runs(id),
                    FOREIGN KEY (hypothesis_id) REFERENCES hypotheses(id)
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_specs_run_version ON research_specs(research_run_id, version);
                CREATE TABLE IF NOT EXISTS spec_revision_proposals (
                    id TEXT PRIMARY KEY,
                    research_run_id TEXT NOT NULL,
                    parent_spec_id TEXT NOT NULL,
                    trigger_evaluation_id TEXT,
                    proposed_parameters_json TEXT NOT NULL,
                    change_summary TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    change_record_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    accepted_spec_id TEXT,
                    created_at TEXT NOT NULL,
                    decided_at TEXT
                );
                CREATE TABLE IF NOT EXISTS research_attempts (
                    id TEXT PRIMARY KEY,
                    research_run_id TEXT NOT NULL,
                    spec_id TEXT NOT NULL,
                    attempt_number INTEGER NOT NULL,
                    stage TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    completed_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    FOREIGN KEY (research_run_id) REFERENCES research_runs(id),
                    FOREIGN KEY (spec_id) REFERENCES research_specs(id)
                );
                CREATE TABLE IF NOT EXISTS experiment_results (
                    id TEXT PRIMARY KEY,
                    attempt_id TEXT NOT NULL,
                    tool_name TEXT NOT NULL,
                    metrics_json TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    passed INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (attempt_id) REFERENCES research_attempts(id)
                );
                CREATE TABLE IF NOT EXISTS evaluation_decisions (
                    id TEXT PRIMARY KEY,
                    research_run_id TEXT NOT NULL,
                    attempt_id TEXT NOT NULL,
                    result_id TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    recommendation TEXT NOT NULL,
                    reason_codes_json TEXT NOT NULL,
                    metrics_snapshot_json TEXT NOT NULL,
                    policy_snapshot_json TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (research_run_id) REFERENCES research_runs(id),
                    FOREIGN KEY (attempt_id) REFERENCES research_attempts(id),
                    FOREIGN KEY (result_id) REFERENCES experiment_results(id)
                );
                CREATE TABLE IF NOT EXISTS audit_events (
                    id TEXT PRIMARY KEY,
                    research_run_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    action TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    state_before_json TEXT NOT NULL,
                    state_after_json TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (research_run_id) REFERENCES research_runs(id)
                );
                CREATE TABLE IF NOT EXISTS critic_invocations (
                    id TEXT PRIMARY KEY,
                    research_run_id TEXT NOT NULL,
                    evaluation_id TEXT,
                    parent_spec_id TEXT,
                    context_version TEXT NOT NULL,
                    prompt_version TEXT,
                    provider TEXT,
                    model TEXT,
                    context_snapshot_json TEXT,
                    raw_response_text TEXT,
                    parsed_decision_json TEXT,
                    validation_status TEXT,
                    validation_errors_json TEXT,
                    resulting_proposal_id TEXT,
                    created_at TEXT NOT NULL,
                    completed_at TEXT
                );
                CREATE TABLE IF NOT EXISTS research_candidates (
                    id TEXT PRIMARY KEY,
                    hypothesis_statement TEXT NOT NULL,
                    hypothesis_rationale TEXT NOT NULL,
                    source TEXT NOT NULL,
                    requirements_json TEXT NOT NULL,
                    candidate_fingerprint TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS feasibility_decisions (
                    id TEXT PRIMARY KEY,
                    candidate_id TEXT NOT NULL,
                    gate_decision TEXT NOT NULL,
                    gate_version TEXT NOT NULL,
                    registry_version TEXT NOT NULL,
                    registry_fingerprint TEXT NOT NULL,
                    feasibility_result_json TEXT NOT NULL,
                    satisfied_ids_json TEXT NOT NULL,
                    unsatisfied_ids_json TEXT NOT NULL,
                    reason_codes_json TEXT NOT NULL,
                    evaluated_at TEXT NOT NULL,
                    FOREIGN KEY (candidate_id) REFERENCES research_candidates(id)
                );
                CREATE TABLE IF NOT EXISTS hypothesis_scientist_invocations (
                    id TEXT PRIMARY KEY,
                    research_brief_id TEXT NOT NULL,
                    research_brief_snapshot TEXT NOT NULL,
                    prompt_version TEXT NOT NULL,
                    provider TEXT,
                    model TEXT,
                    raw_response TEXT,
                    parsed_decision_json TEXT,
                    validation_status TEXT,
                    validation_errors_json TEXT,
                    resulting_candidate_id TEXT,
                    resulting_claim_set_id TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS hypothesis_claim_sets (
                    id TEXT PRIMARY KEY,
                    candidate_id TEXT NOT NULL UNIQUE,
                    hypothesis_scientist_invocation_id TEXT NOT NULL UNIQUE,
                    independent_variable TEXT NOT NULL,
                    independent_variable_direction TEXT NOT NULL,
                    claims_json TEXT NOT NULL,
                    claim_aggregation TEXT NOT NULL,
                    claim_contract_version TEXT NOT NULL,
                    ontology_version TEXT NOT NULL,
                    ontology_fingerprint TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (candidate_id) REFERENCES research_candidates(id),
                    FOREIGN KEY (hypothesis_scientist_invocation_id) REFERENCES hypothesis_scientist_invocations(id)
                );
                CREATE TABLE IF NOT EXISTS research_design_intents (
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
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (candidate_id) REFERENCES research_candidates(id)
                );
                CREATE TABLE IF NOT EXISTS research_designer_invocations (
                    id TEXT PRIMARY KEY,
                    candidate_id TEXT NOT NULL,
                    hypothesis_claim_set_id TEXT,
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
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (candidate_id) REFERENCES research_candidates(id)
                );
                CREATE TABLE IF NOT EXISTS spec_feasibility_decisions (
                    id TEXT PRIMARY KEY,
                    candidate_id TEXT NOT NULL,
                    design_intent_id TEXT NOT NULL,
                    selected_capability_id TEXT NOT NULL,
                    plan_id TEXT,
                    condition_id TEXT,
                    phase TEXT NOT NULL DEFAULT 'MATERIALIZATION',
                    status TEXT NOT NULL,
                    reason_codes_json TEXT NOT NULL,
                    proposed_parameters_json TEXT NOT NULL,
                    validation_notes TEXT NOT NULL,
                    spec_feasibility_version TEXT NOT NULL,
                    registry_version TEXT NOT NULL,
                    registry_fingerprint TEXT NOT NULL,
                    materializer_version TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (candidate_id) REFERENCES research_candidates(id),
                    FOREIGN KEY (design_intent_id) REFERENCES research_design_intents(id)
                );
                CREATE TABLE IF NOT EXISTS spec_materialization_proposals (
                    id TEXT PRIMARY KEY,
                    candidate_id TEXT NOT NULL,
                    design_intent_id TEXT NOT NULL,
                    candidate_feasibility_decision_id TEXT NOT NULL,
                    selected_capability_id TEXT NOT NULL,
                    proposed_parameters_json TEXT NOT NULL,
                    materializer_version TEXT NOT NULL,
                    materialization_policy_version TEXT NOT NULL,
                    materialization_policy_fingerprint TEXT NOT NULL,
                    materialization_trace_json TEXT NOT NULL,
                    spec_feasibility_decision_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    accepted_spec_id TEXT,
                    resulting_research_run_id TEXT,
                    created_at TEXT NOT NULL,
                    decided_at TEXT,
                    FOREIGN KEY (candidate_id) REFERENCES research_candidates(id),
                    FOREIGN KEY (design_intent_id) REFERENCES research_design_intents(id),
                    FOREIGN KEY (spec_feasibility_decision_id) REFERENCES spec_feasibility_decisions(id)
                );
                CREATE TABLE IF NOT EXISTS initial_experiment_plans (
                    id TEXT PRIMARY KEY,
                    candidate_id TEXT NOT NULL,
                    design_intent_id TEXT NOT NULL,
                    research_prediction_plan_id TEXT,
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
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (candidate_id) REFERENCES research_candidates(id),
                    FOREIGN KEY (design_intent_id) REFERENCES research_design_intents(id)
                );
                CREATE TABLE IF NOT EXISTS initial_experiment_conditions (
                    id TEXT PRIMARY KEY,
                    plan_id TEXT NOT NULL,
                    ordinal INTEGER NOT NULL,
                    role TEXT NOT NULL,
                    exact_parameters_json TEXT NOT NULL,
                    selected_capability_id TEXT NOT NULL,
                    expected_tool_kind TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(plan_id, ordinal),
                    FOREIGN KEY (plan_id) REFERENCES initial_experiment_plans(id)
                );
                CREATE TABLE IF NOT EXISTS initial_experiment_plan_proposals (
                    id TEXT PRIMARY KEY,
                    plan_id TEXT NOT NULL UNIQUE,
                    candidate_id TEXT NOT NULL,
                    design_intent_id TEXT NOT NULL,
                    candidate_feasibility_decision_id TEXT NOT NULL,
                    materialization_feasibility_decision_ids_json TEXT NOT NULL,
                    materialization_trace_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    contrast_result_id TEXT,
                    created_at TEXT NOT NULL,
                    decided_at TEXT,
                    accepted_at TEXT,
                    completed_at TEXT,
                    FOREIGN KEY (plan_id) REFERENCES initial_experiment_plans(id)
                );
                CREATE TABLE IF NOT EXISTS condition_execution_records (
                    id TEXT PRIMARY KEY,
                    plan_id TEXT NOT NULL,
                    condition_id TEXT NOT NULL,
                    ordinal INTEGER NOT NULL,
                    role TEXT NOT NULL,
                    selected_capability_id TEXT NOT NULL,
                    exact_parameters_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    experiment_result_id TEXT NOT NULL,
                    tool_name TEXT NOT NULL,
                    metrics_json TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    passed INTEGER NOT NULL,
                    executed_at TEXT NOT NULL,
                    UNIQUE(plan_id, condition_id),
                    FOREIGN KEY (plan_id) REFERENCES initial_experiment_plans(id),
                    FOREIGN KEY (condition_id) REFERENCES initial_experiment_conditions(id)
                );
                CREATE TABLE IF NOT EXISTS parameter_sensitivity_contrast_results (
                    id TEXT PRIMARY KEY,
                    plan_id TEXT NOT NULL UNIQUE,
                    independent_variable TEXT NOT NULL,
                    baseline_condition_id TEXT NOT NULL,
                    comparator_condition_id TEXT NOT NULL,
                    baseline_parameter_value REAL NOT NULL,
                    comparator_parameter_value REAL NOT NULL,
                    outcomes_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (plan_id) REFERENCES initial_experiment_plans(id),
                    FOREIGN KEY (baseline_condition_id) REFERENCES initial_experiment_conditions(id),
                    FOREIGN KEY (comparator_condition_id) REFERENCES initial_experiment_conditions(id)
                );
                CREATE TABLE IF NOT EXISTS research_prediction_plans (
                    id TEXT PRIMARY KEY,
                    candidate_id TEXT NOT NULL,
                    hypothesis_claim_set_id TEXT,
                    design_intent_id TEXT NOT NULL UNIQUE,
                    research_designer_invocation_id TEXT NOT NULL UNIQUE,
                    prediction_contract_version TEXT NOT NULL,
                    ontology_version TEXT NOT NULL,
                    ontology_fingerprint TEXT NOT NULL,
                    independent_variable TEXT NOT NULL,
                    prediction_aggregation_rule TEXT NOT NULL,
                    predictions_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (candidate_id) REFERENCES research_candidates(id),
                    FOREIGN KEY (design_intent_id) REFERENCES research_design_intents(id),
                    FOREIGN KEY (research_designer_invocation_id) REFERENCES research_designer_invocations(id)
                );
                CREATE TABLE IF NOT EXISTS scientific_verdicts (
                    id TEXT PRIMARY KEY,
                    prediction_plan_id TEXT NOT NULL,
                    design_intent_id TEXT NOT NULL,
                    experiment_plan_id TEXT NOT NULL,
                    contrast_result_id TEXT NOT NULL,
                    verdict_policy_version TEXT NOT NULL,
                    verdict_policy_fingerprint TEXT NOT NULL,
                    overall_status TEXT NOT NULL,
                    per_outcome_verdicts_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(prediction_plan_id, contrast_result_id),
                    FOREIGN KEY (prediction_plan_id) REFERENCES research_prediction_plans(id),
                    FOREIGN KEY (design_intent_id) REFERENCES research_design_intents(id),
                    FOREIGN KEY (experiment_plan_id) REFERENCES initial_experiment_plans(id),
                    FOREIGN KEY (contrast_result_id) REFERENCES parameter_sensitivity_contrast_results(id)
                );
                CREATE TABLE IF NOT EXISTS post_verdict_critic_invocations (
                    id TEXT PRIMARY KEY,
                    scientific_verdict_id TEXT NOT NULL UNIQUE,
                    context_version TEXT NOT NULL,
                    prompt_version TEXT,
                    provider TEXT,
                    model TEXT,
                    context_snapshot_json TEXT NOT NULL,
                    raw_response TEXT,
                    parsed_decision_json TEXT,
                    validation_status TEXT,
                    validation_errors_json TEXT,
                    resulting_intent_id TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (scientific_verdict_id) REFERENCES scientific_verdicts(id)
                );
                CREATE TABLE IF NOT EXISTS post_verdict_research_intents (
                    id TEXT PRIMARY KEY,
                    scientific_verdict_id TEXT NOT NULL UNIQUE,
                    research_brief_id TEXT NOT NULL,
                    hypothesis_claim_set_id TEXT NOT NULL,
                    research_design_intent_id TEXT NOT NULL,
                    research_prediction_plan_id TEXT NOT NULL,
                    contrast_result_id TEXT NOT NULL,
                    critic_invocation_id TEXT NOT NULL UNIQUE,
                    decision TEXT NOT NULL,
                    revision_kind TEXT NOT NULL,
                    diagnosis TEXT NOT NULL,
                    next_step_rationale TEXT NOT NULL,
                    prompt_version TEXT NOT NULL,
                    contract_version TEXT NOT NULL,
                    provider TEXT,
                    model TEXT,
                    research_scope_snapshot_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (scientific_verdict_id) REFERENCES scientific_verdicts(id),
                    FOREIGN KEY (hypothesis_claim_set_id) REFERENCES hypothesis_claim_sets(id),
                    FOREIGN KEY (research_design_intent_id) REFERENCES research_design_intents(id),
                    FOREIGN KEY (research_prediction_plan_id) REFERENCES research_prediction_plans(id),
                    FOREIGN KEY (contrast_result_id) REFERENCES parameter_sensitivity_contrast_results(id),
                    FOREIGN KEY (critic_invocation_id) REFERENCES post_verdict_critic_invocations(id)
                );
                CREATE TABLE IF NOT EXISTS research_continuation_authorizations (
                    id TEXT PRIMARY KEY,
                    post_verdict_research_intent_id TEXT NOT NULL UNIQUE,
                    parent_scientific_verdict_id TEXT NOT NULL,
                    parent_hypothesis_claim_set_id TEXT NOT NULL,
                    parent_candidate_id TEXT NOT NULL,
                    research_scope_snapshot_json TEXT NOT NULL,
                    research_scope_fingerprint TEXT NOT NULL,
                    allowed_revision_kind TEXT NOT NULL,
                    generation_number INTEGER NOT NULL,
                    origin TEXT NOT NULL,
                    authorization_status TEXT NOT NULL,
                    contract_version TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    authorized_at TEXT,
                    FOREIGN KEY (post_verdict_research_intent_id) REFERENCES post_verdict_research_intents(id),
                    FOREIGN KEY (parent_scientific_verdict_id) REFERENCES scientific_verdicts(id),
                    FOREIGN KEY (parent_hypothesis_claim_set_id) REFERENCES hypothesis_claim_sets(id),
                    FOREIGN KEY (parent_candidate_id) REFERENCES research_candidates(id)
                );
                CREATE TABLE IF NOT EXISTS research_continuation_invocations (
                    id TEXT PRIMARY KEY,
                    continuation_authorization_id TEXT NOT NULL UNIQUE,
                    post_verdict_research_intent_id TEXT NOT NULL,
                    parent_scientific_verdict_id TEXT NOT NULL,
                    context_version TEXT NOT NULL,
                    prompt_version TEXT,
                    provider TEXT,
                    model TEXT,
                    context_snapshot_json TEXT NOT NULL,
                    raw_response TEXT,
                    parsed_decision_json TEXT,
                    attempt_status TEXT NOT NULL,
                    validation_errors_json TEXT,
                    resulting_candidate_id TEXT,
                    resulting_claim_set_id TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (continuation_authorization_id) REFERENCES research_continuation_authorizations(id),
                    FOREIGN KEY (post_verdict_research_intent_id) REFERENCES post_verdict_research_intents(id),
                    FOREIGN KEY (parent_scientific_verdict_id) REFERENCES scientific_verdicts(id),
                    FOREIGN KEY (resulting_candidate_id) REFERENCES research_candidates(id),
                    FOREIGN KEY (resulting_claim_set_id) REFERENCES hypothesis_claim_sets(id)
                );
                CREATE TABLE IF NOT EXISTS adaptive_hypothesis_lineages (
                    id TEXT PRIMARY KEY,
                    candidate_id TEXT NOT NULL UNIQUE,
                    hypothesis_claim_set_id TEXT NOT NULL UNIQUE,
                    continuation_authorization_id TEXT NOT NULL UNIQUE,
                    post_verdict_research_intent_id TEXT NOT NULL,
                    parent_scientific_verdict_id TEXT NOT NULL,
                    parent_hypothesis_claim_set_id TEXT NOT NULL,
                    parent_candidate_id TEXT NOT NULL,
                    origin TEXT NOT NULL,
                    generation_number INTEGER NOT NULL,
                    research_scope_snapshot_json TEXT NOT NULL,
                    research_scope_fingerprint TEXT NOT NULL,
                    contract_version TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (candidate_id) REFERENCES research_candidates(id),
                    FOREIGN KEY (hypothesis_claim_set_id) REFERENCES hypothesis_claim_sets(id),
                    FOREIGN KEY (continuation_authorization_id) REFERENCES research_continuation_authorizations(id),
                    FOREIGN KEY (post_verdict_research_intent_id) REFERENCES post_verdict_research_intents(id),
                    FOREIGN KEY (parent_scientific_verdict_id) REFERENCES scientific_verdicts(id),
                    FOREIGN KEY (parent_hypothesis_claim_set_id) REFERENCES hypothesis_claim_sets(id),
                    FOREIGN KEY (parent_candidate_id) REFERENCES research_candidates(id)
                );
                """
            )
            current_version = connection.execute("SELECT version FROM schema_version WHERE id = 1").fetchone()
            def column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
                rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
                return any(r["name"] == column for r in rows)

            if current_version is None:
                # brand new DB -> set to current schema
                connection.execute("INSERT INTO schema_version (id, version) VALUES (1, ?)", (SCHEMA_VERSION,))
            else:
                v = current_version["version"]
                if v == SCHEMA_VERSION:
                    # already current
                    pass
                elif v == 1:
                    # legacy upgrade path from 1 -> current
                    # set to intermediate v3 after applying v2->v3 migrations
                    connection.execute("UPDATE schema_version SET version = ? WHERE id = 1", (3,))
                elif v == 2:
                    # migrate from v2 -> v3: add new columns and tables if missing
                    # research_runs.next_required_action
                    if not column_exists(connection, "research_runs", "next_required_action"):
                        connection.execute("ALTER TABLE research_runs ADD COLUMN next_required_action TEXT NOT NULL DEFAULT 'NONE'")
                    # research_specs.parent_spec_id and revision_proposal_id
                    if not column_exists(connection, "research_specs", "parent_spec_id"):
                        connection.execute("ALTER TABLE research_specs ADD COLUMN parent_spec_id TEXT")
                    if not column_exists(connection, "research_specs", "revision_proposal_id"):
                        connection.execute("ALTER TABLE research_specs ADD COLUMN revision_proposal_id TEXT")
                    # unique index
                    connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_specs_run_version ON research_specs(research_run_id, version)")
                    # spec_revision_proposals table
                    connection.executescript(
                        """
                        CREATE TABLE IF NOT EXISTS spec_revision_proposals (
                            id TEXT PRIMARY KEY,
                            research_run_id TEXT NOT NULL,
                            parent_spec_id TEXT NOT NULL,
                            trigger_evaluation_id TEXT,
                            proposed_parameters_json TEXT NOT NULL,
                            change_summary TEXT NOT NULL,
                            reason TEXT NOT NULL,
                            change_record_json TEXT NOT NULL,
                            status TEXT NOT NULL,
                            accepted_spec_id TEXT,
                            created_at TEXT NOT NULL,
                            decided_at TEXT
                        );
                        """
                    )
                    # record migration to v3
                    connection.execute("UPDATE schema_version SET version = ? WHERE id = 1", (3,))
                elif v == 3:
                    # migrate v3 -> v4: add critic_invocations table
                    connection.executescript(
                        """
                        CREATE TABLE IF NOT EXISTS critic_invocations (
                            id TEXT PRIMARY KEY,
                            research_run_id TEXT NOT NULL,
                            evaluation_id TEXT,
                            parent_spec_id TEXT,
                            context_version TEXT NOT NULL,
                            prompt_version TEXT,
                            provider TEXT,
                            model TEXT,
                            context_snapshot_json TEXT,
                            raw_response_text TEXT,
                            parsed_decision_json TEXT,
                            validation_status TEXT,
                            validation_errors_json TEXT,
                            resulting_proposal_id TEXT,
                            created_at TEXT NOT NULL,
                            completed_at TEXT
                        );
                        """
                    )
                    connection.execute("UPDATE schema_version SET version = ? WHERE id = 1", (4,))
                elif v == 4:
                    # migrate v4 -> v5: add research_candidates and feasibility_decisions
                    connection.executescript(
                        """
                        CREATE TABLE IF NOT EXISTS research_candidates (
                            id TEXT PRIMARY KEY,
                            hypothesis_statement TEXT NOT NULL,
                            hypothesis_rationale TEXT NOT NULL,
                            source TEXT NOT NULL,
                            requirements_json TEXT NOT NULL,
                            candidate_fingerprint TEXT NOT NULL,
                            created_at TEXT NOT NULL
                        );
                        CREATE TABLE IF NOT EXISTS feasibility_decisions (
                            id TEXT PRIMARY KEY,
                            candidate_id TEXT NOT NULL,
                            gate_decision TEXT NOT NULL,
                            gate_version TEXT NOT NULL,
                            registry_version TEXT NOT NULL,
                            registry_fingerprint TEXT NOT NULL,
                            feasibility_result_json TEXT NOT NULL,
                            satisfied_ids_json TEXT NOT NULL,
                            unsatisfied_ids_json TEXT NOT NULL,
                            reason_codes_json TEXT NOT NULL,
                            evaluated_at TEXT NOT NULL,
                            FOREIGN KEY (candidate_id) REFERENCES research_candidates(id)
                        );
                        """
                    )
                    connection.execute("UPDATE schema_version SET version = ? WHERE id = 1", (5,))
                elif v == 5:
                    # migrate v5 -> v6: add hypothesis_scientist_invocations
                    connection.executescript(
                        """
                        CREATE TABLE IF NOT EXISTS hypothesis_scientist_invocations (
                            id TEXT PRIMARY KEY,
                            research_brief_id TEXT NOT NULL,
                            research_brief_snapshot TEXT NOT NULL,
                            prompt_version TEXT NOT NULL,
                            provider TEXT,
                            model TEXT,
                            raw_response TEXT,
                            parsed_decision_json TEXT,
                            validation_status TEXT,
                            validation_errors_json TEXT,
                            resulting_candidate_id TEXT,
                            created_at TEXT NOT NULL
                        );
                        """
                    )
                    connection.execute("UPDATE schema_version SET version = ? WHERE id = 1", (6,))
                elif v == 6:
                    # migrate v6 -> v7: add supervised design/materialization persistence
                    for column_name in (
                        "design_intent_id",
                        "spec_materialization_proposal_id",
                        "selected_capability_id",
                        "materializer_version",
                    ):
                        if not column_exists(connection, "research_specs", column_name):
                            connection.execute(
                                f"ALTER TABLE research_specs ADD COLUMN {column_name} TEXT"
                            )
                    connection.executescript(
                        """
                        CREATE TABLE IF NOT EXISTS research_design_intents (
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
                            created_at TEXT NOT NULL,
                            FOREIGN KEY (candidate_id) REFERENCES research_candidates(id)
                        );
                        CREATE TABLE IF NOT EXISTS spec_feasibility_decisions (
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
                            created_at TEXT NOT NULL,
                            FOREIGN KEY (candidate_id) REFERENCES research_candidates(id),
                            FOREIGN KEY (design_intent_id) REFERENCES research_design_intents(id)
                        );
                        CREATE TABLE IF NOT EXISTS spec_materialization_proposals (
                            id TEXT PRIMARY KEY,
                            candidate_id TEXT NOT NULL,
                            design_intent_id TEXT NOT NULL,
                            candidate_feasibility_decision_id TEXT NOT NULL,
                            selected_capability_id TEXT NOT NULL,
                            proposed_parameters_json TEXT NOT NULL,
                            materializer_version TEXT NOT NULL,
                            materialization_policy_version TEXT NOT NULL,
                            materialization_policy_fingerprint TEXT NOT NULL,
                            materialization_trace_json TEXT NOT NULL,
                            spec_feasibility_decision_id TEXT NOT NULL,
                            status TEXT NOT NULL,
                            accepted_spec_id TEXT,
                            resulting_research_run_id TEXT,
                            created_at TEXT NOT NULL,
                            decided_at TEXT,
                            FOREIGN KEY (candidate_id) REFERENCES research_candidates(id),
                            FOREIGN KEY (design_intent_id) REFERENCES research_design_intents(id),
                            FOREIGN KEY (spec_feasibility_decision_id) REFERENCES spec_feasibility_decisions(id)
                        );
                        """
                    )
                    connection.execute("UPDATE schema_version SET version = ? WHERE id = 1", (7,))
                elif v == 7:
                    if not column_exists(connection, "spec_feasibility_decisions", "plan_id"):
                        connection.execute("ALTER TABLE spec_feasibility_decisions ADD COLUMN plan_id TEXT")
                    if not column_exists(connection, "spec_feasibility_decisions", "condition_id"):
                        connection.execute("ALTER TABLE spec_feasibility_decisions ADD COLUMN condition_id TEXT")
                    if not column_exists(connection, "spec_feasibility_decisions", "phase"):
                        connection.execute(
                            "ALTER TABLE spec_feasibility_decisions ADD COLUMN phase TEXT NOT NULL DEFAULT 'MATERIALIZATION'"
                        )
                    connection.executescript(
                        """
                        CREATE TABLE IF NOT EXISTS initial_experiment_plans (
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
                            created_at TEXT NOT NULL,
                            FOREIGN KEY (candidate_id) REFERENCES research_candidates(id),
                            FOREIGN KEY (design_intent_id) REFERENCES research_design_intents(id)
                        );
                        CREATE TABLE IF NOT EXISTS initial_experiment_conditions (
                            id TEXT PRIMARY KEY,
                            plan_id TEXT NOT NULL,
                            ordinal INTEGER NOT NULL,
                            role TEXT NOT NULL,
                            exact_parameters_json TEXT NOT NULL,
                            selected_capability_id TEXT NOT NULL,
                            expected_tool_kind TEXT NOT NULL,
                            created_at TEXT NOT NULL,
                            UNIQUE(plan_id, ordinal),
                            FOREIGN KEY (plan_id) REFERENCES initial_experiment_plans(id)
                        );
                        CREATE TABLE IF NOT EXISTS initial_experiment_plan_proposals (
                            id TEXT PRIMARY KEY,
                            plan_id TEXT NOT NULL UNIQUE,
                            candidate_id TEXT NOT NULL,
                            design_intent_id TEXT NOT NULL,
                            candidate_feasibility_decision_id TEXT NOT NULL,
                            materialization_feasibility_decision_ids_json TEXT NOT NULL,
                            materialization_trace_json TEXT NOT NULL,
                            status TEXT NOT NULL,
                            contrast_result_id TEXT,
                            created_at TEXT NOT NULL,
                            decided_at TEXT,
                            accepted_at TEXT,
                            completed_at TEXT,
                            FOREIGN KEY (plan_id) REFERENCES initial_experiment_plans(id)
                        );
                        CREATE TABLE IF NOT EXISTS condition_execution_records (
                            id TEXT PRIMARY KEY,
                            plan_id TEXT NOT NULL,
                            condition_id TEXT NOT NULL,
                            ordinal INTEGER NOT NULL,
                            role TEXT NOT NULL,
                            selected_capability_id TEXT NOT NULL,
                            exact_parameters_json TEXT NOT NULL,
                            status TEXT NOT NULL,
                            experiment_result_id TEXT NOT NULL,
                            tool_name TEXT NOT NULL,
                            metrics_json TEXT NOT NULL,
                            summary TEXT NOT NULL,
                            passed INTEGER NOT NULL,
                            executed_at TEXT NOT NULL,
                            UNIQUE(plan_id, condition_id),
                            FOREIGN KEY (plan_id) REFERENCES initial_experiment_plans(id),
                            FOREIGN KEY (condition_id) REFERENCES initial_experiment_conditions(id)
                        );
                        CREATE TABLE IF NOT EXISTS parameter_sensitivity_contrast_results (
                            id TEXT PRIMARY KEY,
                            plan_id TEXT NOT NULL UNIQUE,
                            independent_variable TEXT NOT NULL,
                            baseline_condition_id TEXT NOT NULL,
                            comparator_condition_id TEXT NOT NULL,
                            baseline_parameter_value REAL NOT NULL,
                            comparator_parameter_value REAL NOT NULL,
                            outcomes_json TEXT NOT NULL,
                            created_at TEXT NOT NULL,
                            FOREIGN KEY (plan_id) REFERENCES initial_experiment_plans(id),
                            FOREIGN KEY (baseline_condition_id) REFERENCES initial_experiment_conditions(id),
                            FOREIGN KEY (comparator_condition_id) REFERENCES initial_experiment_conditions(id)
                        );
                        """
                    )
                    if not column_exists(connection, "research_design_intents", "ontology_version"):
                        connection.execute("ALTER TABLE research_design_intents ADD COLUMN ontology_version TEXT")
                    if not column_exists(connection, "research_design_intents", "ontology_fingerprint"):
                        connection.execute("ALTER TABLE research_design_intents ADD COLUMN ontology_fingerprint TEXT")
                    connection.executescript(
                        """
                        CREATE TABLE IF NOT EXISTS research_designer_invocations (
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
                            created_at TEXT NOT NULL,
                            FOREIGN KEY (candidate_id) REFERENCES research_candidates(id)
                        );
                        """
                    )
                    connection.execute("UPDATE schema_version SET version = ? WHERE id = 1", (SCHEMA_VERSION,))
                elif v == 8:
                    if not column_exists(connection, "research_design_intents", "ontology_version"):
                        connection.execute("ALTER TABLE research_design_intents ADD COLUMN ontology_version TEXT")
                    if not column_exists(connection, "research_design_intents", "ontology_fingerprint"):
                        connection.execute("ALTER TABLE research_design_intents ADD COLUMN ontology_fingerprint TEXT")
                    connection.executescript(
                        """
                        CREATE TABLE IF NOT EXISTS research_designer_invocations (
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
                            created_at TEXT NOT NULL,
                            FOREIGN KEY (candidate_id) REFERENCES research_candidates(id)
                        );
                        """
                    )
                    connection.execute("UPDATE schema_version SET version = ? WHERE id = 1", (SCHEMA_VERSION,))
                elif v == 9:
                    if not column_exists(connection, "initial_experiment_plans", "research_prediction_plan_id"):
                        connection.execute(
                            "ALTER TABLE initial_experiment_plans ADD COLUMN research_prediction_plan_id TEXT"
                        )
                    connection.executescript(
                        """
                        CREATE TABLE IF NOT EXISTS research_prediction_plans (
                            id TEXT PRIMARY KEY,
                            candidate_id TEXT NOT NULL,
                            design_intent_id TEXT NOT NULL UNIQUE,
                            research_designer_invocation_id TEXT NOT NULL UNIQUE,
                            prediction_contract_version TEXT NOT NULL,
                            ontology_version TEXT NOT NULL,
                            ontology_fingerprint TEXT NOT NULL,
                            independent_variable TEXT NOT NULL,
                            predictions_json TEXT NOT NULL,
                            created_at TEXT NOT NULL,
                            FOREIGN KEY (candidate_id) REFERENCES research_candidates(id),
                            FOREIGN KEY (design_intent_id) REFERENCES research_design_intents(id),
                            FOREIGN KEY (research_designer_invocation_id) REFERENCES research_designer_invocations(id)
                        );
                        CREATE TABLE IF NOT EXISTS scientific_verdicts (
                            id TEXT PRIMARY KEY,
                            prediction_plan_id TEXT NOT NULL,
                            design_intent_id TEXT NOT NULL,
                            experiment_plan_id TEXT NOT NULL,
                            contrast_result_id TEXT NOT NULL,
                            verdict_policy_version TEXT NOT NULL,
                            verdict_policy_fingerprint TEXT NOT NULL,
                            overall_status TEXT NOT NULL,
                            per_outcome_verdicts_json TEXT NOT NULL,
                            created_at TEXT NOT NULL,
                            UNIQUE(prediction_plan_id, contrast_result_id),
                            FOREIGN KEY (prediction_plan_id) REFERENCES research_prediction_plans(id),
                            FOREIGN KEY (design_intent_id) REFERENCES research_design_intents(id),
                            FOREIGN KEY (experiment_plan_id) REFERENCES initial_experiment_plans(id),
                            FOREIGN KEY (contrast_result_id) REFERENCES parameter_sensitivity_contrast_results(id)
                        );
                        """
                    )
                    connection.execute("UPDATE schema_version SET version = ? WHERE id = 1", (10,))
                elif v == 10:
                    if not column_exists(connection, "hypothesis_scientist_invocations", "resulting_claim_set_id"):
                        connection.execute(
                            "ALTER TABLE hypothesis_scientist_invocations ADD COLUMN resulting_claim_set_id TEXT"
                        )
                    connection.executescript(
                        """
                        CREATE TABLE IF NOT EXISTS hypothesis_claim_sets (
                            id TEXT PRIMARY KEY,
                            candidate_id TEXT NOT NULL UNIQUE,
                            hypothesis_scientist_invocation_id TEXT NOT NULL UNIQUE,
                            independent_variable TEXT NOT NULL,
                            independent_variable_direction TEXT NOT NULL,
                            claims_json TEXT NOT NULL,
                            claim_aggregation TEXT NOT NULL,
                            claim_contract_version TEXT NOT NULL,
                            ontology_version TEXT NOT NULL,
                            ontology_fingerprint TEXT NOT NULL,
                            created_at TEXT NOT NULL,
                            FOREIGN KEY (candidate_id) REFERENCES research_candidates(id),
                            FOREIGN KEY (hypothesis_scientist_invocation_id) REFERENCES hypothesis_scientist_invocations(id)
                        );
                        """
                    )
                    if not column_exists(connection, "research_designer_invocations", "hypothesis_claim_set_id"):
                        connection.execute(
                            "ALTER TABLE research_designer_invocations ADD COLUMN hypothesis_claim_set_id TEXT"
                        )
                    if not column_exists(connection, "research_prediction_plans", "hypothesis_claim_set_id"):
                        connection.execute(
                            "ALTER TABLE research_prediction_plans ADD COLUMN hypothesis_claim_set_id TEXT"
                        )
                    if not column_exists(connection, "research_prediction_plans", "prediction_aggregation_rule"):
                        connection.execute(
                            "ALTER TABLE research_prediction_plans ADD COLUMN prediction_aggregation_rule TEXT NOT NULL DEFAULT 'ALL_PREDICTIONS_REQUIRED'"
                        )
                    connection.execute("UPDATE schema_version SET version = ? WHERE id = 1", (11,))
                elif v == 11:
                    connection.executescript(
                        """
                        CREATE TABLE IF NOT EXISTS post_verdict_critic_invocations (
                            id TEXT PRIMARY KEY,
                            scientific_verdict_id TEXT NOT NULL UNIQUE,
                            context_version TEXT NOT NULL,
                            prompt_version TEXT,
                            provider TEXT,
                            model TEXT,
                            context_snapshot_json TEXT NOT NULL,
                            raw_response TEXT,
                            parsed_decision_json TEXT,
                            validation_status TEXT,
                            validation_errors_json TEXT,
                            resulting_intent_id TEXT,
                            created_at TEXT NOT NULL,
                            FOREIGN KEY (scientific_verdict_id) REFERENCES scientific_verdicts(id)
                        );
                        CREATE TABLE IF NOT EXISTS post_verdict_research_intents (
                            id TEXT PRIMARY KEY,
                            scientific_verdict_id TEXT NOT NULL UNIQUE,
                            research_brief_id TEXT NOT NULL,
                            hypothesis_claim_set_id TEXT NOT NULL,
                            research_design_intent_id TEXT NOT NULL,
                            research_prediction_plan_id TEXT NOT NULL,
                            contrast_result_id TEXT NOT NULL,
                            critic_invocation_id TEXT NOT NULL UNIQUE,
                            decision TEXT NOT NULL,
                            revision_kind TEXT NOT NULL,
                            diagnosis TEXT NOT NULL,
                            next_step_rationale TEXT NOT NULL,
                            prompt_version TEXT NOT NULL,
                            contract_version TEXT NOT NULL,
                            provider TEXT,
                            model TEXT,
                            research_scope_snapshot_json TEXT NOT NULL,
                            created_at TEXT NOT NULL,
                            FOREIGN KEY (scientific_verdict_id) REFERENCES scientific_verdicts(id),
                            FOREIGN KEY (hypothesis_claim_set_id) REFERENCES hypothesis_claim_sets(id),
                            FOREIGN KEY (research_design_intent_id) REFERENCES research_design_intents(id),
                            FOREIGN KEY (research_prediction_plan_id) REFERENCES research_prediction_plans(id),
                            FOREIGN KEY (contrast_result_id) REFERENCES parameter_sensitivity_contrast_results(id),
                            FOREIGN KEY (critic_invocation_id) REFERENCES post_verdict_critic_invocations(id)
                        );
                        CREATE TABLE IF NOT EXISTS research_continuation_authorizations (
                            id TEXT PRIMARY KEY,
                            post_verdict_research_intent_id TEXT NOT NULL UNIQUE,
                            parent_scientific_verdict_id TEXT NOT NULL,
                            parent_hypothesis_claim_set_id TEXT NOT NULL,
                            parent_candidate_id TEXT NOT NULL,
                            research_scope_snapshot_json TEXT NOT NULL,
                            research_scope_fingerprint TEXT NOT NULL,
                            allowed_revision_kind TEXT NOT NULL,
                            generation_number INTEGER NOT NULL,
                            origin TEXT NOT NULL,
                            authorization_status TEXT NOT NULL,
                            contract_version TEXT NOT NULL,
                            created_at TEXT NOT NULL,
                            authorized_at TEXT,
                            FOREIGN KEY (post_verdict_research_intent_id) REFERENCES post_verdict_research_intents(id),
                            FOREIGN KEY (parent_scientific_verdict_id) REFERENCES scientific_verdicts(id),
                            FOREIGN KEY (parent_hypothesis_claim_set_id) REFERENCES hypothesis_claim_sets(id),
                            FOREIGN KEY (parent_candidate_id) REFERENCES research_candidates(id)
                        );
                        CREATE TABLE IF NOT EXISTS research_continuation_invocations (
                            id TEXT PRIMARY KEY,
                            continuation_authorization_id TEXT NOT NULL UNIQUE,
                            post_verdict_research_intent_id TEXT NOT NULL,
                            parent_scientific_verdict_id TEXT NOT NULL,
                            context_version TEXT NOT NULL,
                            prompt_version TEXT,
                            provider TEXT,
                            model TEXT,
                            context_snapshot_json TEXT NOT NULL,
                            raw_response TEXT,
                            parsed_decision_json TEXT,
                            attempt_status TEXT NOT NULL,
                            validation_errors_json TEXT,
                            resulting_candidate_id TEXT,
                            resulting_claim_set_id TEXT,
                            created_at TEXT NOT NULL,
                            FOREIGN KEY (continuation_authorization_id) REFERENCES research_continuation_authorizations(id),
                            FOREIGN KEY (post_verdict_research_intent_id) REFERENCES post_verdict_research_intents(id),
                            FOREIGN KEY (parent_scientific_verdict_id) REFERENCES scientific_verdicts(id),
                            FOREIGN KEY (resulting_candidate_id) REFERENCES research_candidates(id),
                            FOREIGN KEY (resulting_claim_set_id) REFERENCES hypothesis_claim_sets(id)
                        );
                        CREATE TABLE IF NOT EXISTS adaptive_hypothesis_lineages (
                            id TEXT PRIMARY KEY,
                            candidate_id TEXT NOT NULL UNIQUE,
                            hypothesis_claim_set_id TEXT NOT NULL UNIQUE,
                            continuation_authorization_id TEXT NOT NULL UNIQUE,
                            post_verdict_research_intent_id TEXT NOT NULL,
                            parent_scientific_verdict_id TEXT NOT NULL,
                            parent_hypothesis_claim_set_id TEXT NOT NULL,
                            parent_candidate_id TEXT NOT NULL,
                            origin TEXT NOT NULL,
                            generation_number INTEGER NOT NULL,
                            research_scope_snapshot_json TEXT NOT NULL,
                            research_scope_fingerprint TEXT NOT NULL,
                            contract_version TEXT NOT NULL,
                            created_at TEXT NOT NULL,
                            FOREIGN KEY (candidate_id) REFERENCES research_candidates(id),
                            FOREIGN KEY (hypothesis_claim_set_id) REFERENCES hypothesis_claim_sets(id),
                            FOREIGN KEY (continuation_authorization_id) REFERENCES research_continuation_authorizations(id),
                            FOREIGN KEY (post_verdict_research_intent_id) REFERENCES post_verdict_research_intents(id),
                            FOREIGN KEY (parent_scientific_verdict_id) REFERENCES scientific_verdicts(id),
                            FOREIGN KEY (parent_hypothesis_claim_set_id) REFERENCES hypothesis_claim_sets(id),
                            FOREIGN KEY (parent_candidate_id) REFERENCES research_candidates(id)
                        );
                        """
                    )
                    connection.execute("UPDATE schema_version SET version = ? WHERE id = 1", (SCHEMA_VERSION,))
                elif v == 12:
                    connection.executescript(
                        """
                        CREATE TABLE IF NOT EXISTS research_continuation_authorizations (
                            id TEXT PRIMARY KEY,
                            post_verdict_research_intent_id TEXT NOT NULL UNIQUE,
                            parent_scientific_verdict_id TEXT NOT NULL,
                            parent_hypothesis_claim_set_id TEXT NOT NULL,
                            parent_candidate_id TEXT NOT NULL,
                            research_scope_snapshot_json TEXT NOT NULL,
                            research_scope_fingerprint TEXT NOT NULL,
                            allowed_revision_kind TEXT NOT NULL,
                            generation_number INTEGER NOT NULL,
                            origin TEXT NOT NULL,
                            authorization_status TEXT NOT NULL,
                            contract_version TEXT NOT NULL,
                            created_at TEXT NOT NULL,
                            authorized_at TEXT,
                            FOREIGN KEY (post_verdict_research_intent_id) REFERENCES post_verdict_research_intents(id),
                            FOREIGN KEY (parent_scientific_verdict_id) REFERENCES scientific_verdicts(id),
                            FOREIGN KEY (parent_hypothesis_claim_set_id) REFERENCES hypothesis_claim_sets(id),
                            FOREIGN KEY (parent_candidate_id) REFERENCES research_candidates(id)
                        );
                        CREATE TABLE IF NOT EXISTS research_continuation_invocations (
                            id TEXT PRIMARY KEY,
                            continuation_authorization_id TEXT NOT NULL UNIQUE,
                            post_verdict_research_intent_id TEXT NOT NULL,
                            parent_scientific_verdict_id TEXT NOT NULL,
                            context_version TEXT NOT NULL,
                            prompt_version TEXT,
                            provider TEXT,
                            model TEXT,
                            context_snapshot_json TEXT NOT NULL,
                            raw_response TEXT,
                            parsed_decision_json TEXT,
                            attempt_status TEXT NOT NULL,
                            validation_errors_json TEXT,
                            resulting_candidate_id TEXT,
                            resulting_claim_set_id TEXT,
                            created_at TEXT NOT NULL,
                            FOREIGN KEY (continuation_authorization_id) REFERENCES research_continuation_authorizations(id),
                            FOREIGN KEY (post_verdict_research_intent_id) REFERENCES post_verdict_research_intents(id),
                            FOREIGN KEY (parent_scientific_verdict_id) REFERENCES scientific_verdicts(id),
                            FOREIGN KEY (resulting_candidate_id) REFERENCES research_candidates(id),
                            FOREIGN KEY (resulting_claim_set_id) REFERENCES hypothesis_claim_sets(id)
                        );
                        CREATE TABLE IF NOT EXISTS adaptive_hypothesis_lineages (
                            id TEXT PRIMARY KEY,
                            candidate_id TEXT NOT NULL UNIQUE,
                            hypothesis_claim_set_id TEXT NOT NULL UNIQUE,
                            continuation_authorization_id TEXT NOT NULL UNIQUE,
                            post_verdict_research_intent_id TEXT NOT NULL,
                            parent_scientific_verdict_id TEXT NOT NULL,
                            parent_hypothesis_claim_set_id TEXT NOT NULL,
                            parent_candidate_id TEXT NOT NULL,
                            origin TEXT NOT NULL,
                            generation_number INTEGER NOT NULL,
                            research_scope_snapshot_json TEXT NOT NULL,
                            research_scope_fingerprint TEXT NOT NULL,
                            contract_version TEXT NOT NULL,
                            created_at TEXT NOT NULL,
                            FOREIGN KEY (candidate_id) REFERENCES research_candidates(id),
                            FOREIGN KEY (hypothesis_claim_set_id) REFERENCES hypothesis_claim_sets(id),
                            FOREIGN KEY (continuation_authorization_id) REFERENCES research_continuation_authorizations(id),
                            FOREIGN KEY (post_verdict_research_intent_id) REFERENCES post_verdict_research_intents(id),
                            FOREIGN KEY (parent_scientific_verdict_id) REFERENCES scientific_verdicts(id),
                            FOREIGN KEY (parent_hypothesis_claim_set_id) REFERENCES hypothesis_claim_sets(id),
                            FOREIGN KEY (parent_candidate_id) REFERENCES research_candidates(id)
                        );
                        """
                    )
                    connection.execute("UPDATE schema_version SET version = ? WHERE id = 1", (SCHEMA_VERSION,))
                else:
                    raise RuntimeError(f"Unsupported schema version {v}")

    def _dumps(self, value: Any) -> str:
        return json.dumps(thaw_json_value(value), sort_keys=True, separators=(",", ":"))

    def _loads(self, value: str | None) -> Any:
        return None if value is None else json.loads(value)

    def _insert_hypothesis(self, connection: sqlite3.Connection, hypothesis: Hypothesis) -> None:
        connection.execute(
            """
            INSERT INTO hypotheses (id, research_run_id, statement, rationale, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                hypothesis.id,
                hypothesis.research_run_id,
                hypothesis.statement,
                hypothesis.rationale,
                hypothesis.created_at.isoformat(),
            ),
        )

    def _insert_spec(self, connection: sqlite3.Connection, spec: ResearchSpec) -> None:
        connection.execute(
            """
            INSERT INTO research_specs (
                id, research_run_id, version, hypothesis_id, parent_spec_id, revision_proposal_id,
                design_intent_id, spec_materialization_proposal_id, selected_capability_id, materializer_version,
                parameters_json, created_at, frozen_at, is_frozen
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                spec.id,
                spec.research_run_id,
                spec.version,
                spec.hypothesis_id,
                spec.parent_spec_id,
                spec.revision_proposal_id,
                spec.design_intent_id,
                spec.spec_materialization_proposal_id,
                spec.selected_capability_id,
                spec.materializer_version,
                self._dumps(spec.parameters),
                spec.created_at.isoformat(),
                None if spec.frozen_at is None else spec.frozen_at.isoformat(),
                1 if spec.is_frozen else 0,
            ),
        )

    class FrozenSpecMutationError(RuntimeError):
        pass

    def _insert_run(self, connection: sqlite3.Connection, run: ResearchRun) -> None:
        connection.execute(
            """
            INSERT INTO research_runs (
                id, stage, status, hypothesis_id, active_spec_id, next_required_action,
                iteration_count, max_iterations, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run.id,
                run.stage.value,
                run.status.value,
                run.hypothesis_id,
                run.active_spec_id,
                run.next_required_action.value,
                run.iteration_count,
                run.max_iterations,
                run.created_at.isoformat(),
                run.updated_at.isoformat(),
            ),
        )

    def _update_run(self, connection: sqlite3.Connection, run: ResearchRun) -> None:
        connection.execute(
            """
            UPDATE research_runs
            SET stage = ?, status = ?, hypothesis_id = ?, active_spec_id = ?, next_required_action = ?,
                iteration_count = ?, max_iterations = ?, created_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                run.stage.value,
                run.status.value,
                run.hypothesis_id,
                run.active_spec_id,
                run.next_required_action.value,
                run.iteration_count,
                run.max_iterations,
                run.created_at.isoformat(),
                run.updated_at.isoformat(),
                run.id,
            ),
        )

    def _insert_attempt(self, connection: sqlite3.Connection, attempt: ResearchAttempt) -> None:
        connection.execute(
            """
            INSERT INTO research_attempts (
                id, research_run_id, spec_id, attempt_number, stage,
                started_at, completed_at, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                attempt.id,
                attempt.research_run_id,
                attempt.spec_id,
                attempt.attempt_number,
                attempt.stage.value,
                attempt.started_at.isoformat(),
                attempt.completed_at.isoformat(),
                attempt.status,
            ),
        )

    def _insert_result(self, connection: sqlite3.Connection, result: ExperimentResult) -> None:
        connection.execute(
            """
            INSERT INTO experiment_results (
                id, attempt_id, tool_name, metrics_json, summary, passed, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                result.id,
                result.attempt_id,
                result.tool_name,
                self._dumps(result.metrics),
                result.summary,
                1 if result.passed else 0,
                result.created_at.isoformat(),
            ),
        )

    def _insert_evaluation(self, connection: sqlite3.Connection, decision: EvaluationDecision) -> None:
        connection.execute(
            """
            INSERT INTO evaluation_decisions (
                id, research_run_id, attempt_id, result_id, stage, recommendation,
                reason_codes_json, metrics_snapshot_json, policy_snapshot_json, summary, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                decision.id,
                decision.research_run_id,
                decision.attempt_id,
                decision.result_id,
                decision.stage.value,
                decision.recommendation.value,
                self._dumps(list(decision.reason_codes)),
                self._dumps(decision.metrics_snapshot),
                self._dumps(decision.policy_snapshot),
                decision.summary,
                decision.created_at.isoformat(),
            ),
        )

    def _insert_audit_event(self, connection: sqlite3.Connection, event: AuditEvent) -> None:
        connection.execute(
            """
            INSERT INTO audit_events (
                id, research_run_id, event_type, action, reason,
                state_before_json, state_after_json, metadata_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.id,
                event.research_run_id,
                event.event_type,
                event.action,
                event.reason,
                self._dumps(event.state_before),
                self._dumps(event.state_after),
                self._dumps(event.metadata),
                event.created_at.isoformat(),
            ),
        )

    def _insert_critic_invocation(self, connection: sqlite3.Connection, inv: CriticInvocation) -> None:
        connection.execute(
            """
            INSERT INTO critic_invocations (
                id, research_run_id, evaluation_id, parent_spec_id, context_version, prompt_version,
                provider, model, context_snapshot_json, raw_response_text, parsed_decision_json,
                validation_status, validation_errors_json, resulting_proposal_id, created_at, completed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                inv.id,
                inv.research_run_id,
                inv.evaluation_id,
                inv.parent_spec_id,
                inv.context_version,
                inv.prompt_version,
                inv.provider,
                inv.model,
                None if inv.context_snapshot is None else self._dumps(inv.context_snapshot),
                inv.raw_response,
                None if inv.parsed_decision is None else self._dumps(inv.parsed_decision),
                inv.validation_status,
                None if inv.validation_errors is None else self._dumps(inv.validation_errors),
                inv.resulting_proposal_id,
                inv.created_at.isoformat(),
                None if inv.completed_at is None else inv.completed_at.isoformat(),
            ),
        )

    def _update_critic_invocation(self, connection: sqlite3.Connection, inv: CriticInvocation) -> None:
        connection.execute(
            """
            UPDATE critic_invocations
            SET research_run_id = ?, evaluation_id = ?, parent_spec_id = ?, context_version = ?, prompt_version = ?,
                provider = ?, model = ?, context_snapshot_json = ?, raw_response_text = ?, parsed_decision_json = ?,
                validation_status = ?, validation_errors_json = ?, resulting_proposal_id = ?, created_at = ?, completed_at = ?
            WHERE id = ?
            """,
            (
                inv.research_run_id,
                inv.evaluation_id,
                inv.parent_spec_id,
                inv.context_version,
                inv.prompt_version,
                inv.provider,
                inv.model,
                None if inv.context_snapshot is None else self._dumps(inv.context_snapshot),
                inv.raw_response,
                None if inv.parsed_decision is None else self._dumps(inv.parsed_decision),
                inv.validation_status,
                None if inv.validation_errors is None else self._dumps(inv.validation_errors),
                inv.resulting_proposal_id,
                inv.created_at.isoformat(),
                None if inv.completed_at is None else inv.completed_at.isoformat(),
                inv.id,
            ),
        )

    def save_hypothesis(self, hypothesis: Hypothesis) -> Hypothesis:
        with self.connect() as connection:
            self._insert_hypothesis(connection, hypothesis)
        return hypothesis

    def save_spec(self, spec: ResearchSpec) -> ResearchSpec:
        with self.connect() as connection:
            # if spec already exists, do not allow mutation of frozen specs
            row = connection.execute("SELECT * FROM research_specs WHERE id = ?", (spec.id,)).fetchone()
            if row is not None:
                # compare stored parameters
                existing_params = self._loads(row["parameters_json"])
                is_frozen = bool(row["is_frozen"])
                if is_frozen and existing_params != spec.parameters:
                    raise SQLiteStore.FrozenSpecMutationError("Cannot modify parameters of a frozen ResearchSpec")
                # no-op if identical, otherwise allow insert to fail for version uniqueness
                return spec
            self._insert_spec(connection, spec)
        return spec

    def save_run(self, run: ResearchRun) -> ResearchRun:
        with self.connect() as connection:
            self._insert_run(connection, run)
        return run

    def update_research_run(self, run: ResearchRun) -> ResearchRun:
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE research_runs
                SET stage = ?, status = ?, hypothesis_id = ?, active_spec_id = ?, next_required_action = ?,
                    iteration_count = ?, max_iterations = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    run.stage.value,
                    run.status.value,
                    run.hypothesis_id,
                    run.active_spec_id,
                    run.next_required_action.value,
                    run.iteration_count,
                    run.max_iterations,
                    run.updated_at.isoformat(),
                    run.id,
                ),
            )
        return run

    def save_attempt(self, attempt: ResearchAttempt) -> ResearchAttempt:
        with self.connect() as connection:
            self._insert_attempt(connection, attempt)
        return attempt

    def save_result(self, result: ExperimentResult) -> ExperimentResult:
        with self.connect() as connection:
            self._insert_result(connection, result)
        return result

    def save_evaluation_decision(self, decision: EvaluationDecision) -> EvaluationDecision:
        with self.connect() as connection:
            self._insert_evaluation(connection, decision)
        return decision

    def save_critic_invocation(self, inv: "CriticInvocation") -> "CriticInvocation":
        with self.connect() as connection:
            # if exists, update, else insert
            existing = connection.execute("SELECT 1 FROM critic_invocations WHERE id = ?", (inv.id,)).fetchone()
            if existing:
                self._update_critic_invocation(connection, inv)
            else:
                self._insert_critic_invocation(connection, inv)
        return inv

    def get_critic_invocation(self, inv_id: str) -> "CriticInvocation" | None:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM critic_invocations WHERE id = ?", (inv_id,)).fetchone()
        if row is None:
            return None
        return CriticInvocation(
            id=row["id"],
            research_run_id=row["research_run_id"],
            evaluation_id=row["evaluation_id"],
            parent_spec_id=row["parent_spec_id"],
            context_version=row["context_version"],
            prompt_version=row["prompt_version"],
            provider=row["provider"],
            model=row["model"],
            context_snapshot=self._loads(row["context_snapshot_json"]),
            raw_response=row["raw_response_text"],
            parsed_decision=self._loads(row["parsed_decision_json"]),
            validation_status=row["validation_status"],
            validation_errors=self._loads(row["validation_errors_json"]),
            resulting_proposal_id=row["resulting_proposal_id"],
            created_at=datetime.fromisoformat(row["created_at"]),
            completed_at=None if row["completed_at"] is None else datetime.fromisoformat(row["completed_at"]),
        )

    def save_post_verdict_critic_invocation(
        self,
        invocation: PostVerdictCriticInvocation,
    ) -> PostVerdictCriticInvocation:
        with self.connect() as connection:
            connection.execute(
                """INSERT OR IGNORE INTO post_verdict_critic_invocations
                   (id, scientific_verdict_id, context_version, prompt_version,
                    provider, model, context_snapshot_json, raw_response,
                    parsed_decision_json, validation_status, validation_errors_json,
                    resulting_intent_id, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    invocation.id,
                    invocation.scientific_verdict_id,
                    invocation.context_version,
                    invocation.prompt_version,
                    invocation.provider,
                    invocation.model,
                    invocation.context_snapshot_json,
                    invocation.raw_response,
                    invocation.parsed_decision_json,
                    invocation.validation_status,
                    invocation.validation_errors_json,
                    invocation.resulting_intent_id,
                    invocation.created_at.isoformat(),
                ),
            )
        return invocation

    def try_create_post_verdict_critic_invocation(
        self,
        invocation: PostVerdictCriticInvocation,
    ) -> bool:
        with self.connect() as connection:
            cursor = connection.execute(
                """INSERT OR IGNORE INTO post_verdict_critic_invocations
                   (id, scientific_verdict_id, context_version, prompt_version,
                    provider, model, context_snapshot_json, raw_response,
                    parsed_decision_json, validation_status, validation_errors_json,
                    resulting_intent_id, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    invocation.id,
                    invocation.scientific_verdict_id,
                    invocation.context_version,
                    invocation.prompt_version,
                    invocation.provider,
                    invocation.model,
                    invocation.context_snapshot_json,
                    invocation.raw_response,
                    invocation.parsed_decision_json,
                    invocation.validation_status,
                    invocation.validation_errors_json,
                    invocation.resulting_intent_id,
                    invocation.created_at.isoformat(),
                ),
            )
        return cursor.rowcount == 1

    def update_post_verdict_critic_invocation(
        self,
        invocation: PostVerdictCriticInvocation,
    ) -> None:
        with self.connect() as connection:
            cursor = connection.execute(
                """UPDATE post_verdict_critic_invocations
                   SET scientific_verdict_id = ?,
                       context_version = ?,
                       prompt_version = ?,
                       provider = ?,
                       model = ?,
                       context_snapshot_json = ?,
                       raw_response = ?,
                       parsed_decision_json = ?,
                       validation_status = ?,
                       validation_errors_json = ?,
                       resulting_intent_id = ?,
                       created_at = ?
                   WHERE id = ?""",
                (
                    invocation.scientific_verdict_id,
                    invocation.context_version,
                    invocation.prompt_version,
                    invocation.provider,
                    invocation.model,
                    invocation.context_snapshot_json,
                    invocation.raw_response,
                    invocation.parsed_decision_json,
                    invocation.validation_status,
                    invocation.validation_errors_json,
                    invocation.resulting_intent_id,
                    invocation.created_at.isoformat(),
                    invocation.id,
                ),
            )
        if cursor.rowcount != 1:
            raise ValueError(
                f"PostVerdictCriticInvocation not found for update: {invocation.id!r}"
            )

    def get_post_verdict_critic_invocation(self, invocation_id: str) -> PostVerdictCriticInvocation | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM post_verdict_critic_invocations WHERE id = ?",
                (invocation_id,),
            ).fetchone()
        if row is None:
            return None
        return PostVerdictCriticInvocation(
            id=row["id"],
            scientific_verdict_id=row["scientific_verdict_id"],
            context_version=row["context_version"],
            prompt_version=row["prompt_version"],
            provider=row["provider"],
            model=row["model"],
            context_snapshot_json=row["context_snapshot_json"],
            raw_response=row["raw_response"],
            parsed_decision_json=row["parsed_decision_json"],
            validation_status=row["validation_status"],
            validation_errors_json=row["validation_errors_json"],
            resulting_intent_id=row["resulting_intent_id"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    def get_post_verdict_critic_invocation_by_scientific_verdict_id(
        self,
        scientific_verdict_id: str,
    ) -> PostVerdictCriticInvocation | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT id FROM post_verdict_critic_invocations WHERE scientific_verdict_id = ?",
                (scientific_verdict_id,),
            ).fetchone()
        if row is None:
            return None
        return self.get_post_verdict_critic_invocation(row["id"])

    def record_audit_event(self, event: AuditEvent) -> AuditEvent:
        with self.connect() as connection:
            self._insert_audit_event(connection, event)
        return event

    def create_research_bundle(self, run: ResearchRun, hypothesis: Hypothesis, spec: ResearchSpec, event: AuditEvent) -> ResearchRun:
        with self.connect() as connection:
            self._insert_run(connection, run)
            self._insert_hypothesis(connection, hypothesis)
            self._insert_spec(connection, spec)
            self._insert_audit_event(connection, event)
        return run

    def create_attempt_and_result(self, attempt: ResearchAttempt, result: ExperimentResult, event: AuditEvent, run: ResearchRun) -> ResearchRun:
        with self.connect() as connection:
            self._insert_attempt(connection, attempt)
            self._insert_result(connection, result)
            # do not update the research run here; callers may be inserting attempts/results
            # as test helpers and we must not overwrite run state set elsewhere
            if event is not None:
                self._insert_audit_event(connection, event)
        return run

    def record_discovery_outcome(
        self,
        attempt: ResearchAttempt,
        result: ExperimentResult,
        decision: EvaluationDecision,
        event: AuditEvent,
        run: ResearchRun,
    ) -> ResearchRun:
        with self.connect() as connection:
            self._insert_attempt(connection, attempt)
            self._insert_result(connection, result)
            self._insert_evaluation(connection, decision)
            self._update_run(connection, run)
            if event is not None:
                self._insert_audit_event(connection, event)
        return run

    def get_research_run(self, run_id: str) -> ResearchRun | None:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM research_runs WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            return None
        return ResearchRun(
            id=row["id"],
            stage=ResearchStage(row["stage"]),
            status=RunStatus(row["status"]),
            next_required_action=ResearchAction(row["next_required_action"]),
            hypothesis_id=row["hypothesis_id"],
            active_spec_id=row["active_spec_id"],
            iteration_count=row["iteration_count"],
            max_iterations=row["max_iterations"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    def get_hypothesis(self, hypothesis_id: str) -> Hypothesis | None:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM hypotheses WHERE id = ?", (hypothesis_id,)).fetchone()
        if row is None:
            return None
        return Hypothesis(
            id=row["id"],
            research_run_id=row["research_run_id"],
            statement=row["statement"],
            rationale=row["rationale"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    def get_spec(self, spec_id: str) -> ResearchSpec | None:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM research_specs WHERE id = ?", (spec_id,)).fetchone()
        if row is None:
            return None
        row_keys = set(row.keys())
        return ResearchSpec(
            id=row["id"],
            research_run_id=row["research_run_id"],
            version=row["version"],
            hypothesis_id=row["hypothesis_id"],
            parent_spec_id=row["parent_spec_id"],
            revision_proposal_id=row["revision_proposal_id"],
            design_intent_id=row["design_intent_id"] if "design_intent_id" in row_keys else None,
            spec_materialization_proposal_id=(
                row["spec_materialization_proposal_id"]
                if "spec_materialization_proposal_id" in row_keys
                else None
            ),
            selected_capability_id=(
                row["selected_capability_id"] if "selected_capability_id" in row_keys else None
            ),
            materializer_version=(
                row["materializer_version"] if "materializer_version" in row_keys else None
            ),
            parameters=self._loads(row["parameters_json"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            frozen_at=None if row["frozen_at"] is None else datetime.fromisoformat(row["frozen_at"]),
            is_frozen=bool(row["is_frozen"]),
        )

    def create_spec_revision_proposal(self, proposal: "SpecRevisionProposal") -> "SpecRevisionProposal":
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO spec_revision_proposals (
                    id, research_run_id, parent_spec_id, trigger_evaluation_id,
                    proposed_parameters_json, change_summary, reason, change_record_json,
                    status, accepted_spec_id, created_at, decided_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    proposal.id,
                    proposal.research_run_id,
                    proposal.parent_spec_id,
                    proposal.trigger_evaluation_id,
                    self._dumps(proposal.proposed_parameters),
                    proposal.change_summary,
                    proposal.reason,
                    self._dumps(proposal.change_record),
                    proposal.status.value,
                    proposal.accepted_spec_id,
                    proposal.created_at.isoformat(),
                    None if proposal.decided_at is None else proposal.decided_at.isoformat(),
                ),
            )
        return proposal

    def get_spec_revision_proposal(self, proposal_id: str) -> "SpecRevisionProposal" | None:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM spec_revision_proposals WHERE id = ?", (proposal_id,)).fetchone()
        if row is None:
            return None
        return SpecRevisionProposal(
            id=row["id"],
            research_run_id=row["research_run_id"],
            parent_spec_id=row["parent_spec_id"],
            trigger_evaluation_id=row["trigger_evaluation_id"],
            proposed_parameters=self._loads(row["proposed_parameters_json"]),
            change_summary=row["change_summary"],
            reason=row["reason"],
            change_record=self._loads(row["change_record_json"]),
            status=SpecRevisionProposalStatus(row["status"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            decided_at=None if row["decided_at"] is None else datetime.fromisoformat(row["decided_at"]),
            accepted_spec_id=row["accepted_spec_id"],
        )

    def accept_spec_revision_proposal(self, proposal_id: str) -> SpecRevisionProposal:
        """Accept a proposal: create new frozen ResearchSpec, update run active_spec_id, and mark proposal accepted."""
        from ..models.research import SpecRevisionProposal as _SpecRev

        with self.connect() as connection:
            # load proposal
            row = connection.execute("SELECT * FROM spec_revision_proposals WHERE id = ?", (proposal_id,)).fetchone()
            if row is None:
                raise KeyError(f"Unknown proposal: {proposal_id}")
            proposal = self.get_spec_revision_proposal(proposal_id)

            # load parent spec and validate same run
            parent = connection.execute("SELECT * FROM research_specs WHERE id = ?", (proposal.parent_spec_id,)).fetchone()
            if parent is None:
                raise KeyError(f"Unknown parent spec: {proposal.parent_spec_id}")
            if parent["research_run_id"] != proposal.research_run_id:
                raise ValueError("Parent spec must belong to the same research run")

            # determine next version
            row_ver = connection.execute(
                "SELECT MAX(version) as v FROM research_specs WHERE research_run_id = ?",
                (proposal.research_run_id,),
            ).fetchone()
            next_version = 1 if row_ver["v"] is None else (row_ver["v"] + 1)

            # create new spec
            new_spec_id = new_id()
            connection.execute(
                """
                INSERT INTO research_specs (
                    id, research_run_id, version, hypothesis_id, parent_spec_id, revision_proposal_id,
                    design_intent_id, spec_materialization_proposal_id, selected_capability_id, materializer_version,
                    parameters_json, created_at, frozen_at, is_frozen
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    new_spec_id,
                    proposal.research_run_id,
                    next_version,
                    parent["hypothesis_id"],
                    proposal.parent_spec_id,
                    proposal.id,
                    parent["design_intent_id"] if "design_intent_id" in parent.keys() else None,
                    parent["spec_materialization_proposal_id"] if "spec_materialization_proposal_id" in parent.keys() else None,
                    parent["selected_capability_id"] if "selected_capability_id" in parent.keys() else None,
                    parent["materializer_version"] if "materializer_version" in parent.keys() else None,
                    self._dumps(proposal.proposed_parameters),
                    proposal.created_at.isoformat(),
                    proposal.created_at.isoformat(),
                    1,
                ),
            )

            # update proposal as accepted
            connection.execute(
                "UPDATE spec_revision_proposals SET status = ?, accepted_spec_id = ?, decided_at = ? WHERE id = ?",
                (SpecRevisionProposalStatus.ACCEPTED.value, new_spec_id, datetime.now().isoformat(), proposal.id),
            )

            # update run active_spec_id and clear next_required_action
            connection.execute(
                "UPDATE research_runs SET active_spec_id = ?, next_required_action = ? WHERE id = ?",
                (new_spec_id, ResearchAction.NONE.value, proposal.research_run_id),
            )

        return self.get_spec_revision_proposal(proposal_id)

    def get_attempts(self, run_id: str) -> list[ResearchAttempt]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM research_attempts WHERE research_run_id = ? ORDER BY attempt_number, started_at, id",
                (run_id,),
            ).fetchall()
        attempts: list[ResearchAttempt] = []
        for row in rows:
            attempts.append(
                ResearchAttempt(
                    id=row["id"],
                    research_run_id=row["research_run_id"],
                    spec_id=row["spec_id"],
                    attempt_number=row["attempt_number"],
                    stage=ResearchStage(row["stage"]),
                    started_at=datetime.fromisoformat(row["started_at"]),
                    completed_at=datetime.fromisoformat(row["completed_at"]),
                    status=row["status"],
                )
            )
        return attempts

    def get_result_for_attempt(self, attempt_id: str) -> ExperimentResult | None:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM experiment_results WHERE attempt_id = ?", (attempt_id,)).fetchone()
        if row is None:
            return None
        return ExperimentResult(
            id=row["id"],
            attempt_id=row["attempt_id"],
            tool_name=row["tool_name"],
            metrics=self._loads(row["metrics_json"]),
            summary=row["summary"],
            passed=bool(row["passed"]),
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    def get_evaluation_decision(self, decision_id: str) -> EvaluationDecision | None:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM evaluation_decisions WHERE id = ?", (decision_id,)).fetchone()
        if row is None:
            return None
        return EvaluationDecision(
            id=row["id"],
            research_run_id=row["research_run_id"],
            attempt_id=row["attempt_id"],
            result_id=row["result_id"],
            stage=ResearchStage(row["stage"]),
            recommendation=EvaluationRecommendation(row["recommendation"]),
            reason_codes=tuple(self._loads(row["reason_codes_json"])),
            metrics_snapshot=self._loads(row["metrics_snapshot_json"]),
            policy_snapshot=self._loads(row["policy_snapshot_json"]),
            summary=row["summary"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    def list_evaluation_decisions(self, run_id: str) -> list[EvaluationDecision]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM evaluation_decisions WHERE research_run_id = ? ORDER BY created_at, rowid",
                (run_id,),
            ).fetchall()
        return [
            EvaluationDecision(
                id=row["id"],
                research_run_id=row["research_run_id"],
                attempt_id=row["attempt_id"],
                result_id=row["result_id"],
                stage=ResearchStage(row["stage"]),
                recommendation=EvaluationRecommendation(row["recommendation"]),
                reason_codes=tuple(self._loads(row["reason_codes_json"])),
                metrics_snapshot=self._loads(row["metrics_snapshot_json"]),
                policy_snapshot=self._loads(row["policy_snapshot_json"]),
                summary=row["summary"],
                created_at=datetime.fromisoformat(row["created_at"]),
            )
            for row in rows
        ]

    def get_latest_evaluation_decision(self, run_id: str) -> EvaluationDecision | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM evaluation_decisions WHERE research_run_id = ? ORDER BY created_at DESC, rowid DESC LIMIT 1",
                (run_id,),
            ).fetchone()
        if row is None:
            return None
        return EvaluationDecision(
            id=row["id"],
            research_run_id=row["research_run_id"],
            attempt_id=row["attempt_id"],
            result_id=row["result_id"],
            stage=ResearchStage(row["stage"]),
            recommendation=EvaluationRecommendation(row["recommendation"]),
            reason_codes=tuple(self._loads(row["reason_codes_json"])),
            metrics_snapshot=self._loads(row["metrics_snapshot_json"]),
            policy_snapshot=self._loads(row["policy_snapshot_json"]),
            summary=row["summary"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    def list_audit_events(self, run_id: str) -> list[AuditEvent]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM audit_events WHERE research_run_id = ? ORDER BY created_at, rowid",
                (run_id,),
            ).fetchall()
        events: list[AuditEvent] = []
        for row in rows:
            events.append(
                AuditEvent(
                    id=row["id"],
                    research_run_id=row["research_run_id"],
                    event_type=row["event_type"],
                    action=row["action"],
                    reason=row["reason"],
                    state_before=self._loads(row["state_before_json"]),
                    state_after=self._loads(row["state_after_json"]),
                    metadata=self._loads(row["metadata_json"]),
                    created_at=datetime.fromisoformat(row["created_at"]),
                )
            )
        return events

    # ─── Research candidates ──────────────────────────────────────────────────

    def save_research_candidate(self, candidate) -> None:
        """Persist a ResearchCandidate.  Idempotent: duplicate id is silently skipped."""
        from ..capabilities.serialization import (
            requirements_to_json,
            compute_candidate_fingerprint,
        )
        fingerprint = compute_candidate_fingerprint(
            candidate.hypothesis_statement,
            candidate.hypothesis_rationale,
            candidate.requirements,
        )
        with self.connect() as conn:
            conn.execute(
                """INSERT OR IGNORE INTO research_candidates
                   (id, hypothesis_statement, hypothesis_rationale, source,
                    requirements_json, candidate_fingerprint, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    candidate.id,
                    candidate.hypothesis_statement,
                    candidate.hypothesis_rationale,
                    candidate.source,
                    requirements_to_json(candidate.requirements),
                    fingerprint,
                    candidate.created_at.isoformat(),
                ),
            )

    def save_hypothesis_claim_set(self, claim_set: HypothesisClaimSet) -> None:
        with self.connect() as conn:
            conn.execute(
                """INSERT OR IGNORE INTO hypothesis_claim_sets
                   (id, candidate_id, hypothesis_scientist_invocation_id, independent_variable,
                    independent_variable_direction, claims_json, claim_aggregation,
                    claim_contract_version, ontology_version, ontology_fingerprint, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    claim_set.id,
                    claim_set.candidate_id,
                    claim_set.hypothesis_scientist_invocation_id,
                    claim_set.independent_variable.value,
                    claim_set.independent_variable_direction.value,
                    self._dumps(
                        [
                            {
                                "outcome": item.outcome.value,
                                "expected_direction": item.expected_direction.value,
                            }
                            for item in claim_set.claims
                        ]
                    ),
                    claim_set.claim_aggregation.value,
                    claim_set.claim_contract_version,
                    claim_set.ontology_version,
                    claim_set.ontology_fingerprint,
                    claim_set.created_at.isoformat(),
                ),
            )

    def _row_to_hypothesis_claim_set(self, row: sqlite3.Row) -> HypothesisClaimSet:
        return HypothesisClaimSet(
            id=row["id"],
            candidate_id=row["candidate_id"],
            hypothesis_scientist_invocation_id=row["hypothesis_scientist_invocation_id"],
            independent_variable=DesignVariable(row["independent_variable"]),
            independent_variable_direction=ExpectedDirection(row["independent_variable_direction"]),
            claims=tuple(
                OutcomePrediction(
                    outcome=DesignOutcome(item["outcome"]),
                    expected_direction=ExpectedDirection(item["expected_direction"]),
                )
                for item in self._loads(row["claims_json"])
            ),
            claim_aggregation=HypothesisClaimAggregation(row["claim_aggregation"]),
            claim_contract_version=row["claim_contract_version"],
            ontology_version=row["ontology_version"],
            ontology_fingerprint=row["ontology_fingerprint"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    def get_hypothesis_claim_set(self, claim_set_id: str) -> HypothesisClaimSet | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM hypothesis_claim_sets WHERE id = ?",
                (claim_set_id,),
            ).fetchone()
        if row is None:
            return None
        return self._row_to_hypothesis_claim_set(row)

    def get_hypothesis_claim_set_by_candidate_id(self, candidate_id: str) -> HypothesisClaimSet | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM hypothesis_claim_sets WHERE candidate_id = ?",
                (candidate_id,),
            ).fetchone()
        if row is None:
            return None
        return self._row_to_hypothesis_claim_set(row)

    def get_hypothesis_claim_set_by_invocation_id(self, invocation_id: str) -> HypothesisClaimSet | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM hypothesis_claim_sets WHERE hypothesis_scientist_invocation_id = ?",
                (invocation_id,),
            ).fetchone()
        if row is None:
            return None
        return self._row_to_hypothesis_claim_set(row)

    def get_research_candidate(self, candidate_id: str):
        """Return a ResearchCandidate or None if not found."""
        from ..capabilities.gate import ResearchCandidate
        from ..capabilities.serialization import requirements_from_json
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM research_candidates WHERE id = ?", (candidate_id,)
            ).fetchone()
        if row is None:
            return None
        return ResearchCandidate(
            id=row["id"],
            hypothesis_statement=row["hypothesis_statement"],
            hypothesis_rationale=row["hypothesis_rationale"],
            source=row["source"],
            requirements=requirements_from_json(row["requirements_json"]),
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    def list_research_candidates(self) -> list:
        """Return all persisted ResearchCandidates ordered by created_at."""
        from ..capabilities.gate import ResearchCandidate
        from ..capabilities.serialization import requirements_from_json
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM research_candidates ORDER BY created_at, rowid"
            ).fetchall()
        return [
            ResearchCandidate(
                id=r["id"],
                hypothesis_statement=r["hypothesis_statement"],
                hypothesis_rationale=r["hypothesis_rationale"],
                source=r["source"],
                requirements=requirements_from_json(r["requirements_json"]),
                created_at=datetime.fromisoformat(r["created_at"]),
            )
            for r in rows
        ]

    def save_feasibility_decision(self, decision) -> None:
        """Persist a ResearchFeasibilityDecision.  Never overwrites prior decisions."""
        from ..capabilities.serialization import feasibility_result_to_dict
        import json as _json
        fr = decision.feasibility_result
        snapshot = feasibility_result_to_dict(fr)
        with self.connect() as conn:
            conn.execute(
                """INSERT OR IGNORE INTO feasibility_decisions
                   (id, candidate_id, gate_decision, gate_version, registry_version,
                    registry_fingerprint, feasibility_result_json,
                    satisfied_ids_json, unsatisfied_ids_json, reason_codes_json,
                    evaluated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    decision.id,
                    decision.candidate_id,
                    decision.decision.value,
                    decision.gate_version,
                    decision.registry_version,
                    decision.registry_fingerprint,
                    _json.dumps(snapshot, sort_keys=True),
                    _json.dumps(list(fr.satisfied_ids)),
                    _json.dumps(list(fr.unsatisfied_ids)),
                    _json.dumps([r.value for r in fr.reason_codes]),
                    decision.evaluated_at.isoformat(),
                ),
            )

    def get_feasibility_decisions(self, candidate_id: str) -> list:
        """Return all feasibility decisions for a candidate, oldest first."""
        from ..capabilities.gate import GateDecision
        from ..capabilities.models import FeasibilityReasonCode
        from ..capabilities.intake import StoredFeasibilityDecision
        import json as _json
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM feasibility_decisions WHERE candidate_id = ? ORDER BY evaluated_at, rowid",
                (candidate_id,),
            ).fetchall()
        result = []
        for row in rows:
            reason_codes = tuple(
                FeasibilityReasonCode[c] for c in _json.loads(row["reason_codes_json"])
            )
            result.append(StoredFeasibilityDecision(
                id=row["id"],
                candidate_id=row["candidate_id"],
                gate_decision=GateDecision[row["gate_decision"]],
                gate_version=row["gate_version"],
                registry_version=row["registry_version"],
                registry_fingerprint=row["registry_fingerprint"],
                satisfied_ids=tuple(_json.loads(row["satisfied_ids_json"])),
                unsatisfied_ids=tuple(_json.loads(row["unsatisfied_ids_json"])),
                reason_codes=reason_codes,
                feasibility_snapshot=_json.loads(row["feasibility_result_json"]),
                evaluated_at=datetime.fromisoformat(row["evaluated_at"]),
            ))
        return result

    def get_feasibility_decision_by_id(self, decision_id: str):
        from ..capabilities.gate import GateDecision
        from ..capabilities.models import FeasibilityReasonCode
        from ..capabilities.intake import StoredFeasibilityDecision
        import json as _json

        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM feasibility_decisions WHERE id = ?",
                (decision_id,),
            ).fetchone()
        if row is None:
            return None
        return StoredFeasibilityDecision(
            id=row["id"],
            candidate_id=row["candidate_id"],
            gate_decision=GateDecision[row["gate_decision"]],
            gate_version=row["gate_version"],
            registry_version=row["registry_version"],
            registry_fingerprint=row["registry_fingerprint"],
            satisfied_ids=tuple(_json.loads(row["satisfied_ids_json"])),
            unsatisfied_ids=tuple(_json.loads(row["unsatisfied_ids_json"])),
            reason_codes=tuple(
                FeasibilityReasonCode[item] for item in _json.loads(row["reason_codes_json"])
            ),
            feasibility_snapshot=_json.loads(row["feasibility_result_json"]),
            evaluated_at=datetime.fromisoformat(row["evaluated_at"]),
        )

    def get_feasibility_decision(self, decision_id: str):
        return self.get_feasibility_decision_by_id(decision_id)

    def get_latest_feasibility_decision(self, candidate_id: str):
        decisions = self.get_feasibility_decisions(candidate_id)
        return decisions[-1] if decisions else None

    # ─── Research design intents ──────────────────────────────────────────────

    def save_research_design_intent(self, design_intent: ResearchDesignIntent) -> None:
        with self.connect() as conn:
            conn.execute(
                """INSERT OR IGNORE INTO research_design_intents
                   (id, candidate_id, design_kind, independent_variables_json,
                    dependent_outcomes_json, controls_json, comparison_intent,
                    analysis_intent, falsification_condition, rationale, source,
                    provider, model, prompt_version, ontology_version,
                    ontology_fingerprint, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    design_intent.id,
                    design_intent.candidate_id,
                    design_intent.design_kind.value,
                    self._dumps([item.value for item in design_intent.independent_variables]),
                    self._dumps([item.value for item in design_intent.dependent_outcomes]),
                    self._dumps([item.value for item in design_intent.controls]),
                    design_intent.comparison_intent.value,
                    design_intent.analysis_intent.value,
                    design_intent.falsification_condition,
                    design_intent.rationale,
                    design_intent.source,
                    design_intent.provider,
                    design_intent.model,
                    design_intent.prompt_version,
                    design_intent.ontology_version,
                    design_intent.ontology_fingerprint,
                    design_intent.created_at.isoformat(),
                ),
            )

    def save_governed_research_design_bundle(
        self,
        *,
        invocation: ResearchDesignerInvocation,
        design_intent: ResearchDesignIntent | None = None,
        prediction_plan: ResearchPredictionPlan | None = None,
    ) -> None:
        if invocation.hypothesis_claim_set_id is not None:
            claim_set = self.get_hypothesis_claim_set(invocation.hypothesis_claim_set_id)
            if claim_set is None:
                raise ValueError("ResearchDesignerInvocation hypothesis_claim_set_id references a missing HypothesisClaimSet")
            if claim_set.candidate_id != invocation.candidate_id:
                raise ValueError("ResearchDesignerInvocation hypothesis_claim_set_id must belong to the same candidate")
        if design_intent is None:
            if invocation.resulting_design_intent_id is not None:
                raise ValueError(
                    "ResearchDesignerInvocation resulting_design_intent_id must be None when no authoritative design intent is saved"
                )
            if prediction_plan is not None:
                raise ValueError("ResearchPredictionPlan cannot be saved without an authoritative ResearchDesignIntent")
        else:
            if invocation.resulting_design_intent_id != design_intent.id:
                raise ValueError(
                    "ResearchDesignerInvocation resulting_design_intent_id must match the authoritative ResearchDesignIntent"
                )
            if invocation.candidate_id != design_intent.candidate_id:
                raise ValueError("ResearchDesignerInvocation candidate_id must match the authoritative ResearchDesignIntent")
            if prediction_plan is not None:
                if prediction_plan.design_intent_id != design_intent.id:
                    raise ValueError("ResearchPredictionPlan design_intent_id must match the authoritative ResearchDesignIntent")
                if prediction_plan.candidate_id != design_intent.candidate_id:
                    raise ValueError("ResearchPredictionPlan candidate_id must match the authoritative ResearchDesignIntent")
                if prediction_plan.research_designer_invocation_id != invocation.id:
                    raise ValueError(
                        "ResearchPredictionPlan research_designer_invocation_id must match the authoritative ResearchDesignerInvocation"
                    )
                if invocation.hypothesis_claim_set_id != prediction_plan.hypothesis_claim_set_id:
                    raise ValueError(
                        "ResearchPredictionPlan hypothesis_claim_set_id must match the authoritative ResearchDesignerInvocation"
                    )

        with self.connect() as conn:
            if design_intent is not None:
                conn.execute(
                    """INSERT INTO research_design_intents
                       (id, candidate_id, design_kind, independent_variables_json,
                        dependent_outcomes_json, controls_json, comparison_intent,
                        analysis_intent, falsification_condition, rationale, source,
                        provider, model, prompt_version, ontology_version,
                        ontology_fingerprint, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        design_intent.id,
                        design_intent.candidate_id,
                        design_intent.design_kind.value,
                        self._dumps([item.value for item in design_intent.independent_variables]),
                        self._dumps([item.value for item in design_intent.dependent_outcomes]),
                        self._dumps([item.value for item in design_intent.controls]),
                        design_intent.comparison_intent.value,
                        design_intent.analysis_intent.value,
                        design_intent.falsification_condition,
                        design_intent.rationale,
                        design_intent.source,
                        design_intent.provider,
                        design_intent.model,
                        design_intent.prompt_version,
                        design_intent.ontology_version,
                        design_intent.ontology_fingerprint,
                        design_intent.created_at.isoformat(),
                    ),
                )
            conn.execute(
                """INSERT INTO research_designer_invocations
                   (id, candidate_id, hypothesis_claim_set_id, candidate_snapshot_json, candidate_feasibility_decision_id,
                    prompt_version, ontology_version, ontology_fingerprint, intent_contract_version,
                    provider, model, raw_response, parsed_decision_json, validation_status,
                    validation_errors_json, resulting_design_intent_id, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    invocation.id,
                    invocation.candidate_id,
                    invocation.hypothesis_claim_set_id,
                    invocation.candidate_snapshot_json,
                    invocation.candidate_feasibility_decision_id,
                    invocation.prompt_version,
                    invocation.ontology_version,
                    invocation.ontology_fingerprint,
                    invocation.intent_contract_version,
                    invocation.provider,
                    invocation.model,
                    invocation.raw_response,
                    invocation.parsed_decision_json,
                    invocation.validation_status,
                    invocation.validation_errors_json,
                    invocation.resulting_design_intent_id,
                    invocation.created_at.isoformat(),
                ),
            )
            if prediction_plan is not None:
                conn.execute(
                    """INSERT INTO research_prediction_plans
                       (id, candidate_id, hypothesis_claim_set_id, design_intent_id, research_designer_invocation_id,
                        prediction_contract_version, ontology_version, ontology_fingerprint,
                        independent_variable, prediction_aggregation_rule, predictions_json, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        prediction_plan.id,
                        prediction_plan.candidate_id,
                        prediction_plan.hypothesis_claim_set_id,
                        prediction_plan.design_intent_id,
                        prediction_plan.research_designer_invocation_id,
                        prediction_plan.prediction_contract_version,
                        prediction_plan.ontology_version,
                        prediction_plan.ontology_fingerprint,
                        prediction_plan.independent_variable.value,
                        prediction_plan.prediction_aggregation_rule.value,
                        self._dumps(
                            [
                                {
                                    "outcome": item.outcome.value,
                                    "expected_direction": item.expected_direction.value,
                                }
                                for item in prediction_plan.predictions
                            ]
                        ),
                        prediction_plan.created_at.isoformat(),
                    ),
                )

    def save_research_prediction_plan(self, prediction_plan: ResearchPredictionPlan) -> None:
        with self.connect() as conn:
            conn.execute(
                """INSERT OR IGNORE INTO research_prediction_plans
                   (id, candidate_id, hypothesis_claim_set_id, design_intent_id, research_designer_invocation_id,
                    prediction_contract_version, ontology_version, ontology_fingerprint,
                    independent_variable, prediction_aggregation_rule, predictions_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    prediction_plan.id,
                    prediction_plan.candidate_id,
                    prediction_plan.hypothesis_claim_set_id,
                    prediction_plan.design_intent_id,
                    prediction_plan.research_designer_invocation_id,
                    prediction_plan.prediction_contract_version,
                    prediction_plan.ontology_version,
                    prediction_plan.ontology_fingerprint,
                    prediction_plan.independent_variable.value,
                    prediction_plan.prediction_aggregation_rule.value,
                    self._dumps(
                        [
                            {
                                "outcome": item.outcome.value,
                                "expected_direction": item.expected_direction.value,
                            }
                            for item in prediction_plan.predictions
                        ]
                    ),
                    prediction_plan.created_at.isoformat(),
                ),
            )

    def _row_to_research_prediction_plan(self, row: sqlite3.Row) -> ResearchPredictionPlan:
        return ResearchPredictionPlan(
            id=row["id"],
            candidate_id=row["candidate_id"],
            hypothesis_claim_set_id=(
                row["hypothesis_claim_set_id"]
                if "hypothesis_claim_set_id" in row.keys()
                else None
            ),
            design_intent_id=row["design_intent_id"],
            research_designer_invocation_id=row["research_designer_invocation_id"],
            prediction_contract_version=row["prediction_contract_version"],
            ontology_version=row["ontology_version"],
            ontology_fingerprint=row["ontology_fingerprint"],
            independent_variable=DesignVariable(row["independent_variable"]),
            prediction_aggregation_rule=PredictionAggregationRule(
                row["prediction_aggregation_rule"]
                if "prediction_aggregation_rule" in row.keys()
                else PredictionAggregationRule.ALL_PREDICTIONS_REQUIRED.value
            ),
            predictions=tuple(
                OutcomePrediction(
                    outcome=DesignOutcome(item["outcome"]),
                    expected_direction=ExpectedDirection(item["expected_direction"]),
                )
                for item in self._loads(row["predictions_json"])
            ),
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    def get_research_prediction_plan(self, prediction_plan_id: str) -> ResearchPredictionPlan | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM research_prediction_plans WHERE id = ?",
                (prediction_plan_id,),
            ).fetchone()
        if row is None:
            return None
        return self._row_to_research_prediction_plan(row)

    def get_research_prediction_plan_by_design_intent_id(
        self,
        design_intent_id: str,
    ) -> ResearchPredictionPlan | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT id FROM research_prediction_plans WHERE design_intent_id = ?",
                (design_intent_id,),
            ).fetchone()
        if row is None:
            return None
        return self.get_research_prediction_plan(row["id"])

    def get_research_design_intent(self, design_intent_id: str) -> ResearchDesignIntent | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM research_design_intents WHERE id = ?",
                (design_intent_id,),
            ).fetchone()
        if row is None:
            return None
        return ResearchDesignIntent(
            id=row["id"],
            candidate_id=row["candidate_id"],
            design_kind=ResearchDesignKind(row["design_kind"]),
            independent_variables=tuple(
                DesignVariable(item) for item in self._loads(row["independent_variables_json"])
            ),
            dependent_outcomes=tuple(
                DesignOutcome(item) for item in self._loads(row["dependent_outcomes_json"])
            ),
            controls=tuple(DesignVariable(item) for item in self._loads(row["controls_json"])),
            comparison_intent=ComparisonIntent(row["comparison_intent"]),
            analysis_intent=AnalysisIntent(row["analysis_intent"]),
            falsification_condition=row["falsification_condition"],
            rationale=row["rationale"],
            source=row["source"],
            provider=row["provider"],
            model=row["model"],
            prompt_version=row["prompt_version"],
            ontology_version=row["ontology_version"] if "ontology_version" in row.keys() else None,
            ontology_fingerprint=row["ontology_fingerprint"] if "ontology_fingerprint" in row.keys() else None,
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    def list_research_design_intents(self, candidate_id: str | None = None) -> list[ResearchDesignIntent]:
        with self.connect() as conn:
            if candidate_id is None:
                rows = conn.execute(
                    "SELECT * FROM research_design_intents ORDER BY created_at, rowid"
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM research_design_intents WHERE candidate_id = ? ORDER BY created_at, rowid",
                    (candidate_id,),
                ).fetchall()
        return [
            ResearchDesignIntent(
                id=row["id"],
                candidate_id=row["candidate_id"],
                design_kind=ResearchDesignKind(row["design_kind"]),
                independent_variables=tuple(
                    DesignVariable(item) for item in self._loads(row["independent_variables_json"])
                ),
                dependent_outcomes=tuple(
                    DesignOutcome(item) for item in self._loads(row["dependent_outcomes_json"])
                ),
                controls=tuple(DesignVariable(item) for item in self._loads(row["controls_json"])),
                comparison_intent=ComparisonIntent(row["comparison_intent"]),
                analysis_intent=AnalysisIntent(row["analysis_intent"]),
                falsification_condition=row["falsification_condition"],
                rationale=row["rationale"],
                source=row["source"],
                provider=row["provider"],
                model=row["model"],
                prompt_version=row["prompt_version"],
                ontology_version=row["ontology_version"] if "ontology_version" in row.keys() else None,
                ontology_fingerprint=row["ontology_fingerprint"] if "ontology_fingerprint" in row.keys() else None,
                created_at=datetime.fromisoformat(row["created_at"]),
            )
            for row in rows
        ]

    # ─── Exact spec feasibility ───────────────────────────────────────────────

    def save_spec_feasibility_decision(self, decision: SpecFeasibilityDecision) -> None:
        with self.connect() as conn:
            conn.execute(
                """INSERT OR IGNORE INTO spec_feasibility_decisions
                   (id, candidate_id, design_intent_id, selected_capability_id,
                    plan_id, condition_id, phase,
                    status, reason_codes_json, proposed_parameters_json,
                    validation_notes, spec_feasibility_version, registry_version,
                    registry_fingerprint, materializer_version, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    decision.id,
                    decision.candidate_id,
                    decision.design_intent_id,
                    decision.selected_capability_id,
                    decision.plan_id,
                    decision.condition_id,
                    decision.phase.value,
                    decision.status.value,
                    self._dumps([item.value for item in decision.reason_codes]),
                    self._dumps(decision.proposed_parameters),
                    decision.validation_notes,
                    decision.spec_feasibility_version,
                    decision.registry_version,
                    decision.registry_fingerprint,
                    decision.materializer_version,
                    decision.created_at.isoformat(),
                ),
            )

    def get_spec_feasibility_decision(self, decision_id: str) -> SpecFeasibilityDecision | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM spec_feasibility_decisions WHERE id = ?",
                (decision_id,),
            ).fetchone()
        if row is None:
            return None
        return SpecFeasibilityDecision(
            id=row["id"],
            candidate_id=row["candidate_id"],
            design_intent_id=row["design_intent_id"],
            selected_capability_id=row["selected_capability_id"],
            status=SpecFeasibilityStatus(row["status"]),
            reason_codes=tuple(
                SpecFeasibilityReasonCode(item)
                for item in self._loads(row["reason_codes_json"])
            ),
            proposed_parameters=self._loads(row["proposed_parameters_json"]),
            validation_notes=row["validation_notes"],
            spec_feasibility_version=row["spec_feasibility_version"],
            registry_version=row["registry_version"],
            registry_fingerprint=row["registry_fingerprint"],
            materializer_version=row["materializer_version"],
            plan_id=row["plan_id"] if "plan_id" in row.keys() else None,
            condition_id=row["condition_id"] if "condition_id" in row.keys() else None,
            phase=SpecFeasibilityPhase(
                row["phase"] if "phase" in row.keys() and row["phase"] is not None else "MATERIALIZATION"
            ),
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    def list_spec_feasibility_decisions(self, candidate_id: str | None = None) -> list[SpecFeasibilityDecision]:
        with self.connect() as conn:
            if candidate_id is None:
                rows = conn.execute(
                    "SELECT * FROM spec_feasibility_decisions ORDER BY created_at, rowid"
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM spec_feasibility_decisions WHERE candidate_id = ? ORDER BY created_at, rowid",
                    (candidate_id,),
                ).fetchall()
        return [
            SpecFeasibilityDecision(
                id=row["id"],
                candidate_id=row["candidate_id"],
                design_intent_id=row["design_intent_id"],
                selected_capability_id=row["selected_capability_id"],
                status=SpecFeasibilityStatus(row["status"]),
                reason_codes=tuple(
                    SpecFeasibilityReasonCode(item)
                    for item in self._loads(row["reason_codes_json"])
                ),
                proposed_parameters=self._loads(row["proposed_parameters_json"]),
                validation_notes=row["validation_notes"],
                spec_feasibility_version=row["spec_feasibility_version"],
                registry_version=row["registry_version"],
                registry_fingerprint=row["registry_fingerprint"],
                materializer_version=row["materializer_version"],
                plan_id=row["plan_id"] if "plan_id" in row.keys() else None,
                condition_id=row["condition_id"] if "condition_id" in row.keys() else None,
                phase=SpecFeasibilityPhase(
                    row["phase"] if "phase" in row.keys() and row["phase"] is not None else "MATERIALIZATION"
                ),
                created_at=datetime.fromisoformat(row["created_at"]),
            )
            for row in rows
        ]

    # ─── Initial spec materialization proposals ───────────────────────────────

    def save_spec_materialization_proposal(self, proposal: SpecMaterializationProposal) -> None:
        with self.connect() as conn:
            conn.execute(
                """INSERT OR IGNORE INTO spec_materialization_proposals
                   (id, candidate_id, design_intent_id, candidate_feasibility_decision_id,
                    selected_capability_id, proposed_parameters_json, materializer_version,
                    materialization_policy_version, materialization_policy_fingerprint,
                    materialization_trace_json, spec_feasibility_decision_id, status,
                    accepted_spec_id, resulting_research_run_id, created_at, decided_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    proposal.id,
                    proposal.candidate_id,
                    proposal.design_intent_id,
                    proposal.candidate_feasibility_decision_id,
                    proposal.selected_capability_id,
                    self._dumps(proposal.proposed_parameters),
                    proposal.materializer_version,
                    proposal.materialization_policy_version,
                    proposal.materialization_policy_fingerprint,
                    self._dumps(proposal.materialization_trace),
                    proposal.spec_feasibility_decision_id,
                    proposal.status.value,
                    proposal.accepted_spec_id,
                    proposal.resulting_research_run_id,
                    proposal.created_at.isoformat(),
                    None if proposal.decided_at is None else proposal.decided_at.isoformat(),
                ),
            )

    def get_spec_materialization_proposal(self, proposal_id: str) -> SpecMaterializationProposal | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM spec_materialization_proposals WHERE id = ?",
                (proposal_id,),
            ).fetchone()
        if row is None:
            return None
        return SpecMaterializationProposal(
            id=row["id"],
            candidate_id=row["candidate_id"],
            design_intent_id=row["design_intent_id"],
            candidate_feasibility_decision_id=row["candidate_feasibility_decision_id"],
            selected_capability_id=row["selected_capability_id"],
            proposed_parameters=self._loads(row["proposed_parameters_json"]),
            materializer_version=row["materializer_version"],
            materialization_policy_version=row["materialization_policy_version"],
            materialization_policy_fingerprint=row["materialization_policy_fingerprint"],
            materialization_trace=self._loads(row["materialization_trace_json"]),
            spec_feasibility_decision_id=row["spec_feasibility_decision_id"],
            status=SpecMaterializationProposalStatus(row["status"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            decided_at=None if row["decided_at"] is None else datetime.fromisoformat(row["decided_at"]),
            accepted_spec_id=row["accepted_spec_id"],
            resulting_research_run_id=row["resulting_research_run_id"],
        )

    def list_spec_materialization_proposals(self, candidate_id: str | None = None) -> list[SpecMaterializationProposal]:
        with self.connect() as conn:
            if candidate_id is None:
                rows = conn.execute(
                    "SELECT * FROM spec_materialization_proposals ORDER BY created_at, rowid"
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM spec_materialization_proposals WHERE candidate_id = ? ORDER BY created_at, rowid",
                    (candidate_id,),
                ).fetchall()
        return [
            SpecMaterializationProposal(
                id=row["id"],
                candidate_id=row["candidate_id"],
                design_intent_id=row["design_intent_id"],
                candidate_feasibility_decision_id=row["candidate_feasibility_decision_id"],
                selected_capability_id=row["selected_capability_id"],
                proposed_parameters=self._loads(row["proposed_parameters_json"]),
                materializer_version=row["materializer_version"],
                materialization_policy_version=row["materialization_policy_version"],
                materialization_policy_fingerprint=row["materialization_policy_fingerprint"],
                materialization_trace=self._loads(row["materialization_trace_json"]),
                spec_feasibility_decision_id=row["spec_feasibility_decision_id"],
                status=SpecMaterializationProposalStatus(row["status"]),
                created_at=datetime.fromisoformat(row["created_at"]),
                decided_at=None if row["decided_at"] is None else datetime.fromisoformat(row["decided_at"]),
                accepted_spec_id=row["accepted_spec_id"],
                resulting_research_run_id=row["resulting_research_run_id"],
            )
            for row in rows
        ]

    def accept_spec_materialization_proposal(
        self,
        proposal_id: str,
        *,
        max_iterations: int,
    ) -> SpecMaterializationProposal:
        from ..capabilities.gate import GateDecision
        from ..capabilities.serialization import requirements_to_json

        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM spec_materialization_proposals WHERE id = ?",
                (proposal_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"Unknown proposal: {proposal_id}")

            proposal = self.get_spec_materialization_proposal(proposal_id)
            if proposal.status != SpecMaterializationProposalStatus.PROPOSED:
                raise ValueError("Spec materialization proposal is not awaiting acceptance")

            candidate = self.get_research_candidate(proposal.candidate_id)
            if candidate is None:
                raise KeyError(f"Unknown candidate: {proposal.candidate_id}")
            design_intent = self.get_research_design_intent(proposal.design_intent_id)
            if design_intent is None:
                raise KeyError(f"Unknown design intent: {proposal.design_intent_id}")
            feasibility = self.get_spec_feasibility_decision(proposal.spec_feasibility_decision_id)
            if feasibility is None:
                raise KeyError(
                    f"Unknown spec feasibility decision: {proposal.spec_feasibility_decision_id}"
                )
            if not feasibility.is_pass:
                raise ValueError("Cannot accept a proposal whose exact spec feasibility failed")

            candidate_feasibility = self.get_feasibility_decision_by_id(
                proposal.candidate_feasibility_decision_id
            )
            if candidate_feasibility is None:
                raise KeyError("Candidate feasibility decision not found")
            if candidate_feasibility.gate_decision != GateDecision.READY_FOR_SPEC:
                raise ValueError("Candidate is not authorized READY_FOR_SPEC")

            run_id = new_id()
            hypothesis = Hypothesis(
                id=new_id(),
                research_run_id=run_id,
                statement=candidate.hypothesis_statement,
                rationale=candidate.hypothesis_rationale,
            )
            spec = ResearchSpec(
                id=new_id(),
                research_run_id=run_id,
                version=1,
                hypothesis_id=hypothesis.id,
                parameters=dict(proposal.proposed_parameters),
                design_intent_id=design_intent.id,
                spec_materialization_proposal_id=proposal.id,
                selected_capability_id=proposal.selected_capability_id,
                materializer_version=proposal.materializer_version,
            )
            run = ResearchRun(
                id=run_id,
                stage=ResearchStage.IDEA,
                status=RunStatus.ACTIVE,
                next_required_action=ResearchAction.NONE,
                hypothesis_id=hypothesis.id,
                active_spec_id=spec.id,
                iteration_count=0,
                max_iterations=max_iterations,
                created_at=spec.created_at,
                updated_at=spec.created_at,
            )
            audit_event = AuditEvent(
                id=new_id(),
                research_run_id=run.id,
                event_type="INITIAL_SPEC_ACCEPTED",
                action="accept_spec_materialization_proposal",
                reason="Accepted deterministic initial spec proposal and created executable research run",
                state_before={},
                state_after=record_to_state(run),
                metadata={
                    "candidate": {
                        **record_to_state(candidate),
                        "requirements": self._loads(requirements_to_json(candidate.requirements)),
                    },
                    "design_intent": record_to_state(design_intent),
                    "proposal": record_to_state(proposal),
                    "spec_feasibility_decision": record_to_state(feasibility),
                    "hypothesis": record_to_state(hypothesis),
                    "spec": record_to_state(spec),
                },
            )

            self._insert_run(connection, run)
            self._insert_hypothesis(connection, hypothesis)
            self._insert_spec(connection, spec)
            self._insert_audit_event(connection, audit_event)
            connection.execute(
                """
                UPDATE spec_materialization_proposals
                SET status = ?, accepted_spec_id = ?, resulting_research_run_id = ?, decided_at = ?
                WHERE id = ?
                """,
                (
                    SpecMaterializationProposalStatus.ACCEPTED.value,
                    spec.id,
                    run.id,
                    spec.created_at.isoformat(),
                    proposal.id,
                ),
            )

        accepted = self.get_spec_materialization_proposal(proposal_id)
        assert accepted is not None
        return accepted

    # ─── Initial experiment plans (V0.13A.1) ──────────────────────────────────

    def save_initial_experiment_plan(self, plan: InitialExperimentPlan) -> None:
        with self.connect() as conn:
            self._validate_research_prediction_plan_linkage(conn, plan)
            conn.execute(
                """INSERT OR IGNORE INTO initial_experiment_plans
                   (id, candidate_id, design_intent_id, research_prediction_plan_id,
                    candidate_feasibility_decision_id,
                    selected_capability_id, design_kind, independent_variable,
                    control_variables_json, dependent_outcomes_json, ordered_condition_ids_json,
                    completion_rule, materializer_version, materialization_policy_version,
                    materialization_policy_fingerprint, registry_version, registry_fingerprint,
                    created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    plan.id,
                    plan.candidate_id,
                    plan.design_intent_id,
                    plan.research_prediction_plan_id,
                    plan.candidate_feasibility_decision_id,
                    plan.selected_capability_id,
                    plan.design_kind.value,
                    plan.independent_variable.value,
                    self._dumps([item.value for item in plan.control_variables]),
                    self._dumps([item.value for item in plan.dependent_outcomes]),
                    self._dumps([item.id for item in plan.ordered_conditions]),
                    plan.completion_rule.value,
                    plan.materializer_version,
                    plan.materialization_policy_version,
                    plan.materialization_policy_fingerprint,
                    plan.registry_version,
                    plan.registry_fingerprint,
                    plan.created_at.isoformat(),
                ),
            )
            for condition in plan.ordered_conditions:
                conn.execute(
                    """INSERT OR IGNORE INTO initial_experiment_conditions
                       (id, plan_id, ordinal, role, exact_parameters_json,
                        selected_capability_id, expected_tool_kind, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        condition.id,
                        plan.id,
                        condition.ordinal,
                        condition.role.value,
                        self._dumps(condition.exact_parameters),
                        condition.selected_capability_id,
                        condition.expected_tool_kind,
                        condition.created_at.isoformat(),
                    ),
                )

    def _validate_research_prediction_plan_linkage(
        self,
        conn: sqlite3.Connection,
        plan: InitialExperimentPlan,
    ) -> ResearchPredictionPlan | None:
        if plan.research_prediction_plan_id is None:
            return None
        row = conn.execute(
            "SELECT * FROM research_prediction_plans WHERE id = ?",
            (plan.research_prediction_plan_id,),
        ).fetchone()
        if row is None:
            raise ValueError("InitialExperimentPlan references a missing ResearchPredictionPlan")
        prediction_plan = self._row_to_research_prediction_plan(row)
        if prediction_plan.candidate_id != plan.candidate_id:
            raise ValueError("ResearchPredictionPlan candidate_id must match the InitialExperimentPlan candidate_id")
        if prediction_plan.hypothesis_claim_set_id is not None:
            claim_set = self.get_hypothesis_claim_set(prediction_plan.hypothesis_claim_set_id)
            if claim_set is None:
                raise ValueError("ResearchPredictionPlan references a missing HypothesisClaimSet")
            if claim_set.candidate_id != plan.candidate_id:
                raise ValueError("HypothesisClaimSet candidate_id must match the InitialExperimentPlan candidate_id")
        if prediction_plan.design_intent_id != plan.design_intent_id:
            raise ValueError("ResearchPredictionPlan design_intent_id must match the InitialExperimentPlan design_intent_id")
        if prediction_plan.independent_variable != plan.independent_variable:
            raise ValueError(
                "ResearchPredictionPlan independent_variable must match the InitialExperimentPlan independent_variable"
            )
        prediction_outcomes = tuple(sorted((item.outcome for item in prediction_plan.predictions), key=lambda item: item.value))
        plan_outcomes = tuple(sorted(plan.dependent_outcomes, key=lambda item: item.value))
        if prediction_outcomes != plan_outcomes:
            raise ValueError(
                "ResearchPredictionPlan predicted outcomes must match the InitialExperimentPlan dependent_outcomes exactly"
            )
        return prediction_plan

    def validate_research_prediction_plan_linkage(
        self,
        plan: InitialExperimentPlan,
    ) -> ResearchPredictionPlan | None:
        with self.connect() as conn:
            return self._validate_research_prediction_plan_linkage(conn, plan)

    def get_initial_experiment_plan(self, plan_id: str) -> InitialExperimentPlan | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM initial_experiment_plans WHERE id = ?",
                (plan_id,),
            ).fetchone()
            if row is None:
                return None
            condition_rows = conn.execute(
                "SELECT * FROM initial_experiment_conditions WHERE plan_id = ? ORDER BY ordinal",
                (plan_id,),
            ).fetchall()
        conditions = tuple(
            ExperimentCondition(
                id=condition_row["id"],
                ordinal=condition_row["ordinal"],
                role=ExperimentConditionRole(condition_row["role"]),
                exact_parameters=self._loads(condition_row["exact_parameters_json"]),
                selected_capability_id=condition_row["selected_capability_id"],
                expected_tool_kind=condition_row["expected_tool_kind"],
                created_at=datetime.fromisoformat(condition_row["created_at"]),
            )
            for condition_row in condition_rows
        )
        return InitialExperimentPlan(
            id=row["id"],
            candidate_id=row["candidate_id"],
            design_intent_id=row["design_intent_id"],
            research_prediction_plan_id=(
                row["research_prediction_plan_id"]
                if "research_prediction_plan_id" in row.keys()
                else None
            ),
            candidate_feasibility_decision_id=row["candidate_feasibility_decision_id"],
            selected_capability_id=row["selected_capability_id"],
            design_kind=ResearchDesignKind(row["design_kind"]),
            independent_variable=DesignVariable(row["independent_variable"]),
            control_variables=tuple(
                DesignVariable(item) for item in self._loads(row["control_variables_json"])
            ),
            dependent_outcomes=tuple(
                DesignOutcome(item) for item in self._loads(row["dependent_outcomes_json"])
            ),
            ordered_conditions=conditions,
            completion_rule=InitialExperimentCompletionRule(row["completion_rule"]),
            materializer_version=row["materializer_version"],
            materialization_policy_version=row["materialization_policy_version"],
            materialization_policy_fingerprint=row["materialization_policy_fingerprint"],
            registry_version=row["registry_version"],
            registry_fingerprint=row["registry_fingerprint"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    def save_initial_experiment_plan_proposal(self, proposal: InitialExperimentPlanProposal) -> None:
        with self.connect() as conn:
            conn.execute(
                """INSERT OR IGNORE INTO initial_experiment_plan_proposals
                   (id, plan_id, candidate_id, design_intent_id,
                    candidate_feasibility_decision_id, materialization_feasibility_decision_ids_json,
                    materialization_trace_json, status, contrast_result_id,
                    created_at, decided_at, accepted_at, completed_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    proposal.id,
                    proposal.plan_id,
                    proposal.candidate_id,
                    proposal.design_intent_id,
                    proposal.candidate_feasibility_decision_id,
                    self._dumps(list(proposal.materialization_feasibility_decision_ids)),
                    self._dumps(proposal.materialization_trace),
                    proposal.status.value,
                    proposal.contrast_result_id,
                    proposal.created_at.isoformat(),
                    None if proposal.decided_at is None else proposal.decided_at.isoformat(),
                    None if proposal.accepted_at is None else proposal.accepted_at.isoformat(),
                    None if proposal.completed_at is None else proposal.completed_at.isoformat(),
                ),
            )

    def get_initial_experiment_plan_proposal(self, proposal_id: str) -> InitialExperimentPlanProposal | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM initial_experiment_plan_proposals WHERE id = ?",
                (proposal_id,),
            ).fetchone()
        if row is None:
            return None
        return InitialExperimentPlanProposal(
            id=row["id"],
            plan_id=row["plan_id"],
            candidate_id=row["candidate_id"],
            design_intent_id=row["design_intent_id"],
            candidate_feasibility_decision_id=row["candidate_feasibility_decision_id"],
            materialization_feasibility_decision_ids=tuple(
                self._loads(row["materialization_feasibility_decision_ids_json"])
            ),
            materialization_trace=self._loads(row["materialization_trace_json"]),
            status=InitialExperimentPlanProposalStatus(row["status"]),
            contrast_result_id=row["contrast_result_id"],
            created_at=datetime.fromisoformat(row["created_at"]),
            decided_at=None if row["decided_at"] is None else datetime.fromisoformat(row["decided_at"]),
            accepted_at=None if row["accepted_at"] is None else datetime.fromisoformat(row["accepted_at"]),
            completed_at=None if row["completed_at"] is None else datetime.fromisoformat(row["completed_at"]),
        )

    def get_initial_experiment_plan_proposal_by_plan_id(
        self,
        plan_id: str,
    ) -> InitialExperimentPlanProposal | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT id FROM initial_experiment_plan_proposals WHERE plan_id = ?",
                (plan_id,),
            ).fetchone()
        if row is None:
            return None
        return self.get_initial_experiment_plan_proposal(row["id"])

    def update_initial_experiment_plan_proposal_status(
        self,
        proposal_id: str,
        status: InitialExperimentPlanProposalStatus,
        *,
        decided_at: datetime | None = None,
        accepted_at: datetime | None = None,
        completed_at: datetime | None = None,
        contrast_result_id: str | None = None,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE initial_experiment_plan_proposals
                SET status = ?, decided_at = COALESCE(?, decided_at),
                    accepted_at = COALESCE(?, accepted_at),
                    completed_at = COALESCE(?, completed_at),
                    contrast_result_id = COALESCE(?, contrast_result_id)
                WHERE id = ?
                """,
                (
                    status.value,
                    None if decided_at is None else decided_at.isoformat(),
                    None if accepted_at is None else accepted_at.isoformat(),
                    None if completed_at is None else completed_at.isoformat(),
                    contrast_result_id,
                    proposal_id,
                ),
            )

    def accept_initial_experiment_plan_proposal(
        self,
        proposal_id: str,
        *,
        registry,
        feasibility_validator,
        materializer_version: str,
        current_policy_version: str,
        current_policy_fingerprint: str,
    ) -> InitialExperimentPlanProposal:
        from ..capabilities.gate import GateDecision

        fresh_decisions: list[SpecFeasibilityDecision] = []
        failure: str | None = None
        accepted_at: datetime | None = None
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM initial_experiment_plan_proposals WHERE id = ?",
                (proposal_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"Unknown proposal: {proposal_id}")
            proposal = self.get_initial_experiment_plan_proposal(proposal_id)
            assert proposal is not None
            if proposal.status != InitialExperimentPlanProposalStatus.PROPOSED:
                raise ValueError("Initial experiment plan proposal is not awaiting acceptance")
            plan = self.get_initial_experiment_plan(proposal.plan_id)
            if plan is None:
                raise KeyError(f"Unknown plan: {proposal.plan_id}")
            self._validate_research_prediction_plan_linkage(conn, plan)
            if current_policy_version != plan.materialization_policy_version:
                raise ValueError("Materialization policy version drift requires rematerialization")
            if current_policy_fingerprint != plan.materialization_policy_fingerprint:
                raise ValueError("Materialization policy fingerprint drift requires rematerialization")
            candidate_feasibility = self.get_feasibility_decision_by_id(plan.candidate_feasibility_decision_id)
            if candidate_feasibility is None:
                raise KeyError("Candidate feasibility decision not found")
            if candidate_feasibility.candidate_id != plan.candidate_id:
                raise ValueError("Candidate feasibility decision does not belong to plan candidate")
            if candidate_feasibility.gate_decision != GateDecision.READY_FOR_SPEC:
                raise ValueError("Candidate is not authorized READY_FOR_SPEC")
            for condition in plan.ordered_conditions:
                decision = feasibility_validator.validate(
                    candidate_id=plan.candidate_id,
                    design_intent_id=plan.design_intent_id,
                    selected_capability_id=condition.selected_capability_id,
                    proposed_parameters=condition.exact_parameters,
                    registry=registry,
                    materializer_version=materializer_version,
                    plan_id=plan.id,
                    condition_id=condition.id,
                    phase=SpecFeasibilityPhase.ACCEPTANCE,
                )
                fresh_decisions.append(decision)
                conn.execute(
                    """INSERT OR IGNORE INTO spec_feasibility_decisions
                       (id, candidate_id, design_intent_id, selected_capability_id,
                        plan_id, condition_id, phase, status, reason_codes_json, proposed_parameters_json,
                        validation_notes, spec_feasibility_version, registry_version,
                        registry_fingerprint, materializer_version, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        decision.id,
                        decision.candidate_id,
                        decision.design_intent_id,
                        decision.selected_capability_id,
                        decision.plan_id,
                        decision.condition_id,
                        decision.phase.value,
                        decision.status.value,
                        self._dumps([item.value for item in decision.reason_codes]),
                        self._dumps(decision.proposed_parameters),
                        decision.validation_notes,
                        decision.spec_feasibility_version,
                        decision.registry_version,
                        decision.registry_fingerprint,
                        decision.materializer_version,
                        decision.created_at.isoformat(),
                    ),
                )
            if not all(decision.is_pass for decision in fresh_decisions):
                failure = "Current exact feasibility no longer passes for every required condition"
            else:
                accepted_at = fresh_decisions[-1].created_at
                conn.execute(
                    """
                    UPDATE initial_experiment_plan_proposals
                    SET status = ?, decided_at = ?, accepted_at = ?
                    WHERE id = ?
                    """,
                    (
                        InitialExperimentPlanProposalStatus.ACCEPTED.value,
                        accepted_at.isoformat(),
                        accepted_at.isoformat(),
                        proposal.id,
                    ),
                )
        if failure is not None:
            raise ValueError(failure)
        accepted = self.get_initial_experiment_plan_proposal(proposal_id)
        assert accepted is not None
        return accepted

    def save_condition_execution_record(self, record: ConditionExecutionRecord) -> None:
        with self.connect() as conn:
            conn.execute(
                """INSERT OR IGNORE INTO condition_execution_records
                   (id, plan_id, condition_id, ordinal, role, selected_capability_id,
                    exact_parameters_json, status, experiment_result_id, tool_name, metrics_json,
                    summary, passed, executed_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    record.id,
                    record.plan_id,
                    record.condition_id,
                    record.ordinal,
                    record.role.value,
                    record.selected_capability_id,
                    self._dumps(record.exact_parameters),
                    record.status.value,
                    record.experiment_result_id,
                    record.tool_name,
                    self._dumps(record.metrics),
                    record.summary,
                    1 if record.passed else 0,
                    record.executed_at.isoformat(),
                ),
            )

    def list_condition_execution_records(self, plan_id: str) -> list[ConditionExecutionRecord]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM condition_execution_records WHERE plan_id = ? ORDER BY ordinal",
                (plan_id,),
            ).fetchall()
        return [
            ConditionExecutionRecord(
                id=row["id"],
                plan_id=row["plan_id"],
                condition_id=row["condition_id"],
                ordinal=row["ordinal"],
                role=ExperimentConditionRole(row["role"]),
                selected_capability_id=row["selected_capability_id"],
                exact_parameters=self._loads(row["exact_parameters_json"]),
                status=ConditionExecutionStatus(row["status"]),
                experiment_result_id=row["experiment_result_id"],
                tool_name=row["tool_name"],
                metrics=self._loads(row["metrics_json"]),
                summary=row["summary"],
                passed=bool(row["passed"]),
                executed_at=datetime.fromisoformat(row["executed_at"]),
            )
            for row in rows
        ]

    def save_parameter_sensitivity_contrast_result(
        self,
        result: ParameterSensitivityContrastResult,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """INSERT OR IGNORE INTO parameter_sensitivity_contrast_results
                   (id, plan_id, independent_variable, baseline_condition_id, comparator_condition_id,
                    baseline_parameter_value, comparator_parameter_value, outcomes_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    result.id,
                    result.plan_id,
                    result.independent_variable.value,
                    result.baseline_condition_id,
                    result.comparator_condition_id,
                    result.baseline_parameter_value,
                    result.comparator_parameter_value,
                    self._dumps(
                        [
                            {
                                "outcome": outcome.outcome.value,
                                "baseline_value": outcome.baseline_value,
                                "comparator_value": outcome.comparator_value,
                                "delta": outcome.delta,
                                "baseline_condition_id": outcome.baseline_condition_id,
                                "comparator_condition_id": outcome.comparator_condition_id,
                            }
                            for outcome in result.outcomes
                        ]
                    ),
                    result.created_at.isoformat(),
                ),
            )

    def get_parameter_sensitivity_contrast_result_by_id(
        self,
        contrast_result_id: str,
    ) -> ParameterSensitivityContrastResult | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM parameter_sensitivity_contrast_results WHERE id = ?",
                (contrast_result_id,),
            ).fetchone()
        if row is None:
            return None
        return ParameterSensitivityContrastResult(
            id=row["id"],
            plan_id=row["plan_id"],
            independent_variable=DesignVariable(row["independent_variable"]),
            baseline_condition_id=row["baseline_condition_id"],
            comparator_condition_id=row["comparator_condition_id"],
            baseline_parameter_value=row["baseline_parameter_value"],
            comparator_parameter_value=row["comparator_parameter_value"],
            outcomes=tuple(
                OutcomeContrast(
                    outcome=DesignOutcome(item["outcome"]),
                    baseline_value=item["baseline_value"],
                    comparator_value=item["comparator_value"],
                    delta=item["delta"],
                    baseline_condition_id=item["baseline_condition_id"],
                    comparator_condition_id=item["comparator_condition_id"],
                )
                for item in self._loads(row["outcomes_json"])
            ),
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    def get_parameter_sensitivity_contrast_result(
        self,
        plan_id: str,
    ) -> ParameterSensitivityContrastResult | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM parameter_sensitivity_contrast_results WHERE plan_id = ?",
                (plan_id,),
            ).fetchone()
        if row is None:
            return None
        return ParameterSensitivityContrastResult(
            id=row["id"],
            plan_id=row["plan_id"],
            independent_variable=DesignVariable(row["independent_variable"]),
            baseline_condition_id=row["baseline_condition_id"],
            comparator_condition_id=row["comparator_condition_id"],
            baseline_parameter_value=row["baseline_parameter_value"],
            comparator_parameter_value=row["comparator_parameter_value"],
            outcomes=tuple(
                OutcomeContrast(
                    outcome=DesignOutcome(item["outcome"]),
                    baseline_value=item["baseline_value"],
                    comparator_value=item["comparator_value"],
                    delta=item["delta"],
                    baseline_condition_id=item["baseline_condition_id"],
                    comparator_condition_id=item["comparator_condition_id"],
                )
                for item in self._loads(row["outcomes_json"])
            ),
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    def save_scientific_verdict(self, verdict: ScientificVerdict) -> None:
        with self.connect() as conn:
            conn.execute(
                """INSERT OR IGNORE INTO scientific_verdicts
                   (id, prediction_plan_id, design_intent_id, experiment_plan_id, contrast_result_id,
                    verdict_policy_version, verdict_policy_fingerprint, overall_status,
                    per_outcome_verdicts_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    verdict.id,
                    verdict.prediction_plan_id,
                    verdict.design_intent_id,
                    verdict.experiment_plan_id,
                    verdict.contrast_result_id,
                    verdict.verdict_policy_version,
                    verdict.verdict_policy_fingerprint,
                    verdict.overall_status.value,
                    self._dumps(
                        [
                            {
                                "outcome": item.outcome.value,
                                "expected_direction": item.expected_direction.value,
                                "observed_direction": (
                                    None if item.observed_direction is None else item.observed_direction.value
                                ),
                                "baseline_value": item.baseline_value,
                                "comparator_value": item.comparator_value,
                                "delta": item.delta,
                                "result": item.result.value,
                            }
                            for item in verdict.per_outcome_verdicts
                        ]
                    ),
                    verdict.created_at.isoformat(),
                ),
            )

    def get_scientific_verdict(self, verdict_id: str) -> ScientificVerdict | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM scientific_verdicts WHERE id = ?",
                (verdict_id,),
            ).fetchone()
        if row is None:
            return None
        return ScientificVerdict(
            id=row["id"],
            prediction_plan_id=row["prediction_plan_id"],
            design_intent_id=row["design_intent_id"],
            experiment_plan_id=row["experiment_plan_id"],
            contrast_result_id=row["contrast_result_id"],
            verdict_policy_version=row["verdict_policy_version"],
            verdict_policy_fingerprint=row["verdict_policy_fingerprint"],
            overall_status=ScientificVerdictStatus(row["overall_status"]),
            per_outcome_verdicts=tuple(
                OutcomeScientificVerdict(
                    outcome=DesignOutcome(item["outcome"]),
                    expected_direction=ExpectedDirection(item["expected_direction"]),
                    observed_direction=(
                        None if item["observed_direction"] is None else ExpectedDirection(item["observed_direction"])
                    ),
                    baseline_value=item["baseline_value"],
                    comparator_value=item["comparator_value"],
                    delta=item["delta"],
                    result=PredictionVerdictResult(item["result"]),
                )
                for item in self._loads(row["per_outcome_verdicts_json"])
            ),
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    def get_scientific_verdict_by_prediction_plan_and_contrast_result(
        self,
        *,
        prediction_plan_id: str,
        contrast_result_id: str,
    ) -> ScientificVerdict | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT id FROM scientific_verdicts
                WHERE prediction_plan_id = ? AND contrast_result_id = ?
                """,
                (prediction_plan_id, contrast_result_id),
            ).fetchone()
        if row is None:
            return None
        return self.get_scientific_verdict(row["id"])

    def save_post_verdict_bundle(
        self,
        *,
        invocation: PostVerdictCriticInvocation,
        intent: PostVerdictResearchIntent,
    ) -> None:
        if invocation.scientific_verdict_id != intent.scientific_verdict_id:
            raise ValueError("PostVerdictCriticInvocation and PostVerdictResearchIntent must share scientific_verdict_id")
        if invocation.id != intent.critic_invocation_id:
            raise ValueError("PostVerdictResearchIntent critic_invocation_id must match the authoritative invocation")
        if invocation.resulting_intent_id != intent.id:
            raise ValueError("PostVerdictCriticInvocation resulting_intent_id must match the authoritative intent")
        with self.connect() as conn:
            cursor = conn.execute(
                """UPDATE post_verdict_critic_invocations
                   SET scientific_verdict_id = ?,
                       context_version = ?,
                       prompt_version = ?,
                       provider = ?,
                       model = ?,
                       context_snapshot_json = ?,
                       raw_response = ?,
                       parsed_decision_json = ?,
                       validation_status = ?,
                       validation_errors_json = ?,
                       resulting_intent_id = ?,
                       created_at = ?
                   WHERE id = ?""",
                (
                    invocation.scientific_verdict_id,
                    invocation.context_version,
                    invocation.prompt_version,
                    invocation.provider,
                    invocation.model,
                    invocation.context_snapshot_json,
                    invocation.raw_response,
                    invocation.parsed_decision_json,
                    invocation.validation_status,
                    invocation.validation_errors_json,
                    invocation.resulting_intent_id,
                    invocation.created_at.isoformat(),
                    invocation.id,
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError(
                    "PostVerdictCriticInvocation reservation must exist before saving the authoritative intent"
                )
            conn.execute(
                """INSERT INTO post_verdict_research_intents
                   (id, scientific_verdict_id, research_brief_id, hypothesis_claim_set_id,
                    research_design_intent_id, research_prediction_plan_id, contrast_result_id,
                    critic_invocation_id, decision, revision_kind, diagnosis,
                    next_step_rationale, prompt_version, contract_version, provider,
                    model, research_scope_snapshot_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    intent.id,
                    intent.scientific_verdict_id,
                    intent.research_brief_id,
                    intent.hypothesis_claim_set_id,
                    intent.research_design_intent_id,
                    intent.research_prediction_plan_id,
                    intent.contrast_result_id,
                    intent.critic_invocation_id,
                    intent.decision.value,
                    intent.revision_kind.value,
                    intent.diagnosis,
                    intent.next_step_rationale,
                    intent.prompt_version,
                    intent.contract_version,
                    intent.provider,
                    intent.model,
                    self._dumps(intent.research_scope_payload()),
                    intent.created_at.isoformat(),
                ),
            )

    def get_post_verdict_research_intent(self, intent_id: str) -> PostVerdictResearchIntent | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM post_verdict_research_intents WHERE id = ?",
                (intent_id,),
            ).fetchone()
        if row is None:
            return None
        return PostVerdictResearchIntent(
            id=row["id"],
            scientific_verdict_id=row["scientific_verdict_id"],
            research_brief_id=row["research_brief_id"],
            hypothesis_claim_set_id=row["hypothesis_claim_set_id"],
            research_design_intent_id=row["research_design_intent_id"],
            research_prediction_plan_id=row["research_prediction_plan_id"],
            contrast_result_id=row["contrast_result_id"],
            critic_invocation_id=row["critic_invocation_id"],
            decision=PostVerdictCriticDecisionType(row["decision"]),
            revision_kind=PostVerdictRevisionKind(row["revision_kind"]),
            diagnosis=row["diagnosis"],
            next_step_rationale=row["next_step_rationale"],
            prompt_version=row["prompt_version"],
            contract_version=row["contract_version"],
            provider=row["provider"],
            model=row["model"],
            research_scope_snapshot=self._loads(row["research_scope_snapshot_json"]),
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    def get_post_verdict_research_intent_by_scientific_verdict_id(
        self,
        scientific_verdict_id: str,
    ) -> PostVerdictResearchIntent | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT id FROM post_verdict_research_intents WHERE scientific_verdict_id = ?",
                (scientific_verdict_id,),
            ).fetchone()
        if row is None:
            return None
        return self.get_post_verdict_research_intent(row["id"])

    def save_research_continuation_authorization(
        self,
        authorization: ResearchContinuationAuthorization,
    ) -> ResearchContinuationAuthorization:
        with self.connect() as conn:
            conn.execute(
                """INSERT OR IGNORE INTO research_continuation_authorizations
                   (id, post_verdict_research_intent_id, parent_scientific_verdict_id,
                    parent_hypothesis_claim_set_id, parent_candidate_id,
                    research_scope_snapshot_json, research_scope_fingerprint,
                    allowed_revision_kind, generation_number, origin,
                    authorization_status, contract_version, created_at, authorized_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    authorization.id,
                    authorization.post_verdict_research_intent_id,
                    authorization.parent_scientific_verdict_id,
                    authorization.parent_hypothesis_claim_set_id,
                    authorization.parent_candidate_id,
                    self._dumps(authorization.research_scope_payload()),
                    authorization.research_scope_fingerprint,
                    authorization.allowed_revision_kind.value,
                    authorization.generation_number,
                    authorization.origin.value,
                    authorization.authorization_status.value,
                    authorization.contract_version,
                    authorization.created_at.isoformat(),
                    None if authorization.authorized_at is None else authorization.authorized_at.isoformat(),
                ),
            )
        return authorization

    def update_research_continuation_authorization(
        self,
        authorization: ResearchContinuationAuthorization,
    ) -> None:
        with self.connect() as conn:
            cursor = conn.execute(
                """UPDATE research_continuation_authorizations
                   SET post_verdict_research_intent_id = ?,
                       parent_scientific_verdict_id = ?,
                       parent_hypothesis_claim_set_id = ?,
                       parent_candidate_id = ?,
                       research_scope_snapshot_json = ?,
                       research_scope_fingerprint = ?,
                       allowed_revision_kind = ?,
                       generation_number = ?,
                       origin = ?,
                       authorization_status = ?,
                       contract_version = ?,
                       created_at = ?,
                       authorized_at = ?
                   WHERE id = ?""",
                (
                    authorization.post_verdict_research_intent_id,
                    authorization.parent_scientific_verdict_id,
                    authorization.parent_hypothesis_claim_set_id,
                    authorization.parent_candidate_id,
                    self._dumps(authorization.research_scope_payload()),
                    authorization.research_scope_fingerprint,
                    authorization.allowed_revision_kind.value,
                    authorization.generation_number,
                    authorization.origin.value,
                    authorization.authorization_status.value,
                    authorization.contract_version,
                    authorization.created_at.isoformat(),
                    None if authorization.authorized_at is None else authorization.authorized_at.isoformat(),
                    authorization.id,
                ),
            )
        if cursor.rowcount != 1:
            raise ValueError(
                f"ResearchContinuationAuthorization not found for update: {authorization.id!r}"
            )

    def get_research_continuation_authorization(
        self,
        authorization_id: str,
    ) -> ResearchContinuationAuthorization | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM research_continuation_authorizations WHERE id = ?",
                (authorization_id,),
            ).fetchone()
        if row is None:
            return None
        return ResearchContinuationAuthorization(
            id=row["id"],
            post_verdict_research_intent_id=row["post_verdict_research_intent_id"],
            parent_scientific_verdict_id=row["parent_scientific_verdict_id"],
            parent_hypothesis_claim_set_id=row["parent_hypothesis_claim_set_id"],
            parent_candidate_id=row["parent_candidate_id"],
            research_scope_snapshot=self._loads(row["research_scope_snapshot_json"]),
            research_scope_fingerprint=row["research_scope_fingerprint"],
            allowed_revision_kind=PostVerdictRevisionKind(row["allowed_revision_kind"]),
            generation_number=row["generation_number"],
            origin=ResearchContinuationOrigin(row["origin"]),
            authorization_status=ResearchContinuationAuthorizationStatus(row["authorization_status"]),
            contract_version=row["contract_version"],
            created_at=datetime.fromisoformat(row["created_at"]),
            authorized_at=(
                None if row["authorized_at"] is None else datetime.fromisoformat(row["authorized_at"])
            ),
        )

    def get_research_continuation_authorization_by_post_verdict_research_intent_id(
        self,
        post_verdict_research_intent_id: str,
    ) -> ResearchContinuationAuthorization | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT id FROM research_continuation_authorizations WHERE post_verdict_research_intent_id = ?",
                (post_verdict_research_intent_id,),
            ).fetchone()
        if row is None:
            return None
        return self.get_research_continuation_authorization(row["id"])

    def try_reserve_research_continuation_invocation(
        self,
        invocation: ResearchContinuationInvocation,
    ) -> bool:
        with self.connect() as conn:
            cursor = conn.execute(
                """UPDATE research_continuation_authorizations
                   SET authorization_status = ?
                   WHERE id = ? AND authorization_status = ?""",
                (
                    ResearchContinuationAuthorizationStatus.CONSUMED.value,
                    invocation.continuation_authorization_id,
                    ResearchContinuationAuthorizationStatus.AUTHORIZED.value,
                ),
            )
            if cursor.rowcount != 1:
                return False
            conn.execute(
                """INSERT INTO research_continuation_invocations
                   (id, continuation_authorization_id, post_verdict_research_intent_id,
                    parent_scientific_verdict_id, context_version, prompt_version,
                    provider, model, context_snapshot_json, raw_response,
                    parsed_decision_json, attempt_status, validation_errors_json,
                    resulting_candidate_id, resulting_claim_set_id, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    invocation.id,
                    invocation.continuation_authorization_id,
                    invocation.post_verdict_research_intent_id,
                    invocation.parent_scientific_verdict_id,
                    invocation.context_version,
                    invocation.prompt_version,
                    invocation.provider,
                    invocation.model,
                    invocation.context_snapshot_json,
                    invocation.raw_response,
                    invocation.parsed_decision_json,
                    invocation.attempt_status.value,
                    invocation.validation_errors_json,
                    invocation.resulting_candidate_id,
                    invocation.resulting_claim_set_id,
                    invocation.created_at.isoformat(),
                ),
            )
        return True

    def update_research_continuation_invocation(
        self,
        invocation: ResearchContinuationInvocation,
    ) -> None:
        with self.connect() as conn:
            cursor = conn.execute(
                """UPDATE research_continuation_invocations
                   SET continuation_authorization_id = ?,
                       post_verdict_research_intent_id = ?,
                       parent_scientific_verdict_id = ?,
                       context_version = ?,
                       prompt_version = ?,
                       provider = ?,
                       model = ?,
                       context_snapshot_json = ?,
                       raw_response = ?,
                       parsed_decision_json = ?,
                       attempt_status = ?,
                       validation_errors_json = ?,
                       resulting_candidate_id = ?,
                       resulting_claim_set_id = ?,
                       created_at = ?
                   WHERE id = ?""",
                (
                    invocation.continuation_authorization_id,
                    invocation.post_verdict_research_intent_id,
                    invocation.parent_scientific_verdict_id,
                    invocation.context_version,
                    invocation.prompt_version,
                    invocation.provider,
                    invocation.model,
                    invocation.context_snapshot_json,
                    invocation.raw_response,
                    invocation.parsed_decision_json,
                    invocation.attempt_status.value,
                    invocation.validation_errors_json,
                    invocation.resulting_candidate_id,
                    invocation.resulting_claim_set_id,
                    invocation.created_at.isoformat(),
                    invocation.id,
                ),
            )
        if cursor.rowcount != 1:
            raise ValueError(
                f"ResearchContinuationInvocation not found for update: {invocation.id!r}"
            )

    def get_research_continuation_invocation(
        self,
        invocation_id: str,
    ) -> ResearchContinuationInvocation | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM research_continuation_invocations WHERE id = ?",
                (invocation_id,),
            ).fetchone()
        if row is None:
            return None
        return ResearchContinuationInvocation(
            id=row["id"],
            continuation_authorization_id=row["continuation_authorization_id"],
            post_verdict_research_intent_id=row["post_verdict_research_intent_id"],
            parent_scientific_verdict_id=row["parent_scientific_verdict_id"],
            context_version=row["context_version"],
            prompt_version=row["prompt_version"],
            provider=row["provider"],
            model=row["model"],
            context_snapshot_json=row["context_snapshot_json"],
            raw_response=row["raw_response"],
            parsed_decision_json=row["parsed_decision_json"],
            attempt_status=ResearchContinuationAttemptStatus(row["attempt_status"]),
            validation_errors_json=row["validation_errors_json"],
            resulting_candidate_id=row["resulting_candidate_id"],
            resulting_claim_set_id=row["resulting_claim_set_id"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    def get_research_continuation_invocation_by_authorization_id(
        self,
        continuation_authorization_id: str,
    ) -> ResearchContinuationInvocation | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT id FROM research_continuation_invocations WHERE continuation_authorization_id = ?",
                (continuation_authorization_id,),
            ).fetchone()
        if row is None:
            return None
        return self.get_research_continuation_invocation(row["id"])

    def save_adaptive_hypothesis_lineage(
        self,
        lineage: AdaptiveHypothesisLineage,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """INSERT OR IGNORE INTO adaptive_hypothesis_lineages
                   (id, candidate_id, hypothesis_claim_set_id, continuation_authorization_id,
                    post_verdict_research_intent_id, parent_scientific_verdict_id,
                    parent_hypothesis_claim_set_id, parent_candidate_id, origin,
                    generation_number, research_scope_snapshot_json, research_scope_fingerprint,
                    contract_version, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    lineage.id,
                    lineage.candidate_id,
                    lineage.hypothesis_claim_set_id,
                    lineage.continuation_authorization_id,
                    lineage.post_verdict_research_intent_id,
                    lineage.parent_scientific_verdict_id,
                    lineage.parent_hypothesis_claim_set_id,
                    lineage.parent_candidate_id,
                    lineage.origin.value,
                    lineage.generation_number,
                    self._dumps(lineage.research_scope_payload()),
                    lineage.research_scope_fingerprint,
                    lineage.contract_version,
                    lineage.created_at.isoformat(),
                ),
            )

    def get_adaptive_hypothesis_lineage_by_candidate_id(
        self,
        candidate_id: str,
    ) -> AdaptiveHypothesisLineage | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM adaptive_hypothesis_lineages WHERE candidate_id = ?",
                (candidate_id,),
            ).fetchone()
        if row is None:
            return None
        return AdaptiveHypothesisLineage(
            id=row["id"],
            candidate_id=row["candidate_id"],
            hypothesis_claim_set_id=row["hypothesis_claim_set_id"],
            continuation_authorization_id=row["continuation_authorization_id"],
            post_verdict_research_intent_id=row["post_verdict_research_intent_id"],
            parent_scientific_verdict_id=row["parent_scientific_verdict_id"],
            parent_hypothesis_claim_set_id=row["parent_hypothesis_claim_set_id"],
            parent_candidate_id=row["parent_candidate_id"],
            origin=ResearchContinuationOrigin(row["origin"]),
            generation_number=row["generation_number"],
            research_scope_snapshot=self._loads(row["research_scope_snapshot_json"]),
            research_scope_fingerprint=row["research_scope_fingerprint"],
            contract_version=row["contract_version"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    def save_research_continuation_success_bundle(
        self,
        *,
        invocation: ResearchContinuationInvocation,
        candidate,
        claim_set: HypothesisClaimSet,
        lineage: AdaptiveHypothesisLineage,
    ) -> None:
        if invocation.resulting_candidate_id != candidate.id:
            raise ValueError(
                "ResearchContinuationInvocation resulting_candidate_id must match the authoritative ResearchCandidate"
            )
        if invocation.resulting_claim_set_id != claim_set.id:
            raise ValueError(
                "ResearchContinuationInvocation resulting_claim_set_id must match the authoritative HypothesisClaimSet"
            )
        if claim_set.candidate_id != candidate.id:
            raise ValueError("HypothesisClaimSet candidate_id must match the authoritative ResearchCandidate")
        if claim_set.hypothesis_scientist_invocation_id != invocation.id:
            raise ValueError(
                "HypothesisClaimSet hypothesis_scientist_invocation_id must match the continuation invocation id"
            )
        if lineage.candidate_id != candidate.id or lineage.hypothesis_claim_set_id != claim_set.id:
            raise ValueError("AdaptiveHypothesisLineage must point to the authoritative child candidate and claim set")
        with self.connect() as conn:
            from ..capabilities.serialization import (
                compute_candidate_fingerprint,
                requirements_to_json,
            )

            fingerprint = compute_candidate_fingerprint(
                candidate.hypothesis_statement,
                candidate.hypothesis_rationale,
                candidate.requirements,
            )
            conn.execute(
                """INSERT INTO research_candidates
                   (id, hypothesis_statement, hypothesis_rationale, source,
                    requirements_json, candidate_fingerprint, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    candidate.id,
                    candidate.hypothesis_statement,
                    candidate.hypothesis_rationale,
                    candidate.source,
                    requirements_to_json(candidate.requirements),
                    fingerprint,
                    candidate.created_at.isoformat(),
                ),
            )
            conn.execute(
                """INSERT INTO hypothesis_scientist_invocations
                   (id, research_brief_id, research_brief_snapshot, prompt_version,
                    provider, model, raw_response, parsed_decision_json,
                    validation_status, validation_errors_json, resulting_candidate_id,
                    resulting_claim_set_id, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    invocation.id,
                    invocation.continuation_authorization_id,
                    invocation.context_snapshot_json,
                    invocation.prompt_version or "v6",
                    invocation.provider,
                    invocation.model,
                    invocation.raw_response,
                    invocation.parsed_decision_json,
                    "VALID",
                    invocation.validation_errors_json,
                    invocation.resulting_candidate_id,
                    invocation.resulting_claim_set_id,
                    invocation.created_at.isoformat(),
                ),
            )
            conn.execute(
                """INSERT INTO hypothesis_claim_sets
                   (id, candidate_id, hypothesis_scientist_invocation_id, independent_variable,
                    independent_variable_direction, claims_json, claim_aggregation,
                    claim_contract_version, ontology_version, ontology_fingerprint, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    claim_set.id,
                    claim_set.candidate_id,
                    claim_set.hypothesis_scientist_invocation_id,
                    claim_set.independent_variable.value,
                    claim_set.independent_variable_direction.value,
                    self._dumps(
                        [
                            {
                                "outcome": item.outcome.value,
                                "expected_direction": item.expected_direction.value,
                            }
                            for item in claim_set.claims
                        ]
                    ),
                    claim_set.claim_aggregation.value,
                    claim_set.claim_contract_version,
                    claim_set.ontology_version,
                    claim_set.ontology_fingerprint,
                    claim_set.created_at.isoformat(),
                ),
            )
            conn.execute(
                """UPDATE research_continuation_invocations
                   SET raw_response = ?,
                       parsed_decision_json = ?,
                       attempt_status = ?,
                       validation_errors_json = ?,
                       resulting_candidate_id = ?,
                       resulting_claim_set_id = ?
                   WHERE id = ?""",
                (
                    invocation.raw_response,
                    invocation.parsed_decision_json,
                    invocation.attempt_status.value,
                    invocation.validation_errors_json,
                    invocation.resulting_candidate_id,
                    invocation.resulting_claim_set_id,
                    invocation.id,
                ),
            )
            conn.execute(
                """INSERT INTO adaptive_hypothesis_lineages
                   (id, candidate_id, hypothesis_claim_set_id, continuation_authorization_id,
                    post_verdict_research_intent_id, parent_scientific_verdict_id,
                    parent_hypothesis_claim_set_id, parent_candidate_id, origin,
                    generation_number, research_scope_snapshot_json, research_scope_fingerprint,
                    contract_version, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    lineage.id,
                    lineage.candidate_id,
                    lineage.hypothesis_claim_set_id,
                    lineage.continuation_authorization_id,
                    lineage.post_verdict_research_intent_id,
                    lineage.parent_scientific_verdict_id,
                    lineage.parent_hypothesis_claim_set_id,
                    lineage.parent_candidate_id,
                    lineage.origin.value,
                    lineage.generation_number,
                    self._dumps(lineage.research_scope_payload()),
                    lineage.research_scope_fingerprint,
                    lineage.contract_version,
                    lineage.created_at.isoformat(),
                ),
            )

    # ─── Research designer invocations ───────────────────────────────────────

    def save_research_designer_invocation(self, inv: ResearchDesignerInvocation) -> None:
        with self.connect() as conn:
            conn.execute(
                """INSERT OR IGNORE INTO research_designer_invocations
                   (id, candidate_id, hypothesis_claim_set_id, candidate_snapshot_json, candidate_feasibility_decision_id,
                    prompt_version, ontology_version, ontology_fingerprint, intent_contract_version,
                    provider, model, raw_response, parsed_decision_json, validation_status,
                    validation_errors_json, resulting_design_intent_id, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    inv.id,
                    inv.candidate_id,
                    inv.hypothesis_claim_set_id,
                    inv.candidate_snapshot_json,
                    inv.candidate_feasibility_decision_id,
                    inv.prompt_version,
                    inv.ontology_version,
                    inv.ontology_fingerprint,
                    inv.intent_contract_version,
                    inv.provider,
                    inv.model,
                    inv.raw_response,
                    inv.parsed_decision_json,
                    inv.validation_status,
                    inv.validation_errors_json,
                    inv.resulting_design_intent_id,
                    inv.created_at.isoformat(),
                ),
            )

    def get_research_designer_invocation(self, invocation_id: str) -> ResearchDesignerInvocation | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM research_designer_invocations WHERE id = ?",
                (invocation_id,),
            ).fetchone()
        if row is None:
            return None
        return ResearchDesignerInvocation(
            id=row["id"],
            candidate_id=row["candidate_id"],
            hypothesis_claim_set_id=(
                row["hypothesis_claim_set_id"]
                if "hypothesis_claim_set_id" in row.keys()
                else None
            ),
            candidate_snapshot_json=row["candidate_snapshot_json"],
            candidate_feasibility_decision_id=row["candidate_feasibility_decision_id"],
            prompt_version=row["prompt_version"],
            ontology_version=row["ontology_version"],
            ontology_fingerprint=row["ontology_fingerprint"],
            intent_contract_version=row["intent_contract_version"],
            provider=row["provider"],
            model=row["model"],
            raw_response=row["raw_response"],
            parsed_decision_json=row["parsed_decision_json"],
            validation_status=row["validation_status"],
            validation_errors_json=row["validation_errors_json"],
            resulting_design_intent_id=row["resulting_design_intent_id"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    def find_research_designer_invocation_by_resulting_design_intent_id(
        self,
        design_intent_id: str,
    ) -> ResearchDesignerInvocation | None:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT id FROM research_designer_invocations
                WHERE resulting_design_intent_id = ?
                ORDER BY created_at, rowid
                """,
                (design_intent_id,),
            ).fetchall()
        if not rows:
            return None
        if len(rows) != 1:
            raise RuntimeError(
                f"Expected exactly one ResearchDesignerInvocation for design_intent_id={design_intent_id!r}"
            )
        return self.get_research_designer_invocation(rows[0]["id"])

    def get_research_designer_invocations(self, candidate_id: str) -> list[ResearchDesignerInvocation]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM research_designer_invocations WHERE candidate_id = ? ORDER BY created_at, rowid",
                (candidate_id,),
            ).fetchall()
        return [
            ResearchDesignerInvocation(
                id=row["id"],
                candidate_id=row["candidate_id"],
                hypothesis_claim_set_id=(
                    row["hypothesis_claim_set_id"]
                    if "hypothesis_claim_set_id" in row.keys()
                    else None
                ),
                candidate_snapshot_json=row["candidate_snapshot_json"],
                candidate_feasibility_decision_id=row["candidate_feasibility_decision_id"],
                prompt_version=row["prompt_version"],
                ontology_version=row["ontology_version"],
                ontology_fingerprint=row["ontology_fingerprint"],
                intent_contract_version=row["intent_contract_version"],
                provider=row["provider"],
                model=row["model"],
                raw_response=row["raw_response"],
                parsed_decision_json=row["parsed_decision_json"],
                validation_status=row["validation_status"],
                validation_errors_json=row["validation_errors_json"],
                resulting_design_intent_id=row["resulting_design_intent_id"],
                created_at=datetime.fromisoformat(row["created_at"]),
            )
            for row in rows
        ]

    # ─── Hypothesis scientist invocations ─────────────────────────────────────

    def save_governed_hypothesis_bundle(
        self,
        *,
        invocation: HypothesisScientistInvocation,
        candidate=None,
        claim_set: HypothesisClaimSet | None = None,
    ) -> None:
        if candidate is None:
            if invocation.resulting_candidate_id is not None:
                raise ValueError(
                    "HypothesisScientistInvocation resulting_candidate_id must be None when no authoritative candidate is saved"
                )
            if claim_set is not None or invocation.resulting_claim_set_id is not None:
                raise ValueError("HypothesisClaimSet cannot be saved without an authoritative ResearchCandidate")
        else:
            if invocation.resulting_candidate_id != candidate.id:
                raise ValueError(
                    "HypothesisScientistInvocation resulting_candidate_id must match the authoritative ResearchCandidate"
                )
            if invocation.prompt_version == "v4" and claim_set is None:
                raise ValueError("V4 Hypothesis Scientist persistence requires an authoritative HypothesisClaimSet")
            if claim_set is not None:
                if invocation.resulting_claim_set_id != claim_set.id:
                    raise ValueError(
                        "HypothesisScientistInvocation resulting_claim_set_id must match the authoritative HypothesisClaimSet"
                    )
                if claim_set.candidate_id != candidate.id:
                    raise ValueError("HypothesisClaimSet candidate_id must match the authoritative ResearchCandidate")
                if claim_set.hypothesis_scientist_invocation_id != invocation.id:
                    raise ValueError(
                        "HypothesisClaimSet hypothesis_scientist_invocation_id must match the authoritative HypothesisScientistInvocation"
                    )

        with self.connect() as conn:
            if candidate is not None:
                from ..capabilities.serialization import (
                    compute_candidate_fingerprint,
                    requirements_to_json,
                )

                fingerprint = compute_candidate_fingerprint(
                    candidate.hypothesis_statement,
                    candidate.hypothesis_rationale,
                    candidate.requirements,
                )
                conn.execute(
                    """INSERT INTO research_candidates
                       (id, hypothesis_statement, hypothesis_rationale, source,
                        requirements_json, candidate_fingerprint, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        candidate.id,
                        candidate.hypothesis_statement,
                        candidate.hypothesis_rationale,
                        candidate.source,
                        requirements_to_json(candidate.requirements),
                        fingerprint,
                        candidate.created_at.isoformat(),
                    ),
                )
            conn.execute(
                """INSERT INTO hypothesis_scientist_invocations
                   (id, research_brief_id, research_brief_snapshot, prompt_version,
                    provider, model, raw_response, parsed_decision_json,
                    validation_status, validation_errors_json, resulting_candidate_id,
                    resulting_claim_set_id, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    invocation.id,
                    invocation.research_brief_id,
                    invocation.research_brief_snapshot,
                    invocation.prompt_version,
                    invocation.provider,
                    invocation.model,
                    invocation.raw_response,
                    invocation.parsed_decision_json,
                    invocation.validation_status,
                    invocation.validation_errors_json,
                    invocation.resulting_candidate_id,
                    invocation.resulting_claim_set_id,
                    invocation.created_at.isoformat(),
                ),
            )
            if claim_set is not None:
                conn.execute(
                    """INSERT INTO hypothesis_claim_sets
                       (id, candidate_id, hypothesis_scientist_invocation_id, independent_variable,
                        independent_variable_direction, claims_json, claim_aggregation,
                        claim_contract_version, ontology_version, ontology_fingerprint, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        claim_set.id,
                        claim_set.candidate_id,
                        claim_set.hypothesis_scientist_invocation_id,
                        claim_set.independent_variable.value,
                        claim_set.independent_variable_direction.value,
                        self._dumps(
                            [
                                {
                                    "outcome": item.outcome.value,
                                    "expected_direction": item.expected_direction.value,
                                }
                                for item in claim_set.claims
                            ]
                        ),
                        claim_set.claim_aggregation.value,
                        claim_set.claim_contract_version,
                        claim_set.ontology_version,
                        claim_set.ontology_fingerprint,
                        claim_set.created_at.isoformat(),
                    ),
                )

    def save_hypothesis_scientist_invocation(self, inv) -> None:
        with self.connect() as conn:
            conn.execute(
                """INSERT OR IGNORE INTO hypothesis_scientist_invocations
                   (id, research_brief_id, research_brief_snapshot, prompt_version,
                    provider, model, raw_response, parsed_decision_json,
                    validation_status, validation_errors_json, resulting_candidate_id,
                    resulting_claim_set_id, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    inv.id, inv.research_brief_id, inv.research_brief_snapshot,
                    inv.prompt_version, inv.provider, inv.model, inv.raw_response,
                    inv.parsed_decision_json, inv.validation_status,
                    inv.validation_errors_json, inv.resulting_candidate_id,
                    inv.resulting_claim_set_id,
                    inv.created_at.isoformat(),
                ),
            )

    def get_hypothesis_scientist_invocation(self, invocation_id: str):
        from ..models.hypothesis_scientist import HypothesisScientistInvocation

        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM hypothesis_scientist_invocations WHERE id = ?",
                (invocation_id,),
            ).fetchone()
        if row is None:
            return None
        return HypothesisScientistInvocation(
            id=row["id"],
            research_brief_id=row["research_brief_id"],
            research_brief_snapshot=row["research_brief_snapshot"],
            prompt_version=row["prompt_version"],
            provider=row["provider"],
            model=row["model"],
            raw_response=row["raw_response"],
            parsed_decision_json=row["parsed_decision_json"],
            validation_status=row["validation_status"],
            validation_errors_json=row["validation_errors_json"],
            resulting_candidate_id=row["resulting_candidate_id"],
            resulting_claim_set_id=(
                row["resulting_claim_set_id"]
                if "resulting_claim_set_id" in row.keys()
                else None
            ),
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    def find_hypothesis_scientist_invocation_by_resulting_candidate_id(self, candidate_id: str):
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT id FROM hypothesis_scientist_invocations
                WHERE resulting_candidate_id = ?
                ORDER BY created_at, rowid
                """,
                (candidate_id,),
            ).fetchall()
        if not rows:
            return None
        if len(rows) != 1:
            raise RuntimeError(
                f"Expected exactly one HypothesisScientistInvocation for candidate_id={candidate_id!r}"
            )
        return self.get_hypothesis_scientist_invocation(rows[0]["id"])

    def get_hypothesis_scientist_invocations(self, brief_id: str) -> list:
        """Return all scientist invocations for a brief, oldest first."""
        from ..models.hypothesis_scientist import HypothesisScientistInvocation
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM hypothesis_scientist_invocations WHERE research_brief_id = ? ORDER BY created_at, rowid",
                (brief_id,),
            ).fetchall()
        return [
            HypothesisScientistInvocation(
                id=r["id"],
                research_brief_id=r["research_brief_id"],
                research_brief_snapshot=r["research_brief_snapshot"],
                prompt_version=r["prompt_version"],
                provider=r["provider"],
                model=r["model"],
                raw_response=r["raw_response"],
                parsed_decision_json=r["parsed_decision_json"],
                validation_status=r["validation_status"],
                validation_errors_json=r["validation_errors_json"],
                resulting_candidate_id=r["resulting_candidate_id"],
                resulting_claim_set_id=(
                    r["resulting_claim_set_id"]
                    if "resulting_claim_set_id" in r.keys()
                    else None
                ),
                created_at=datetime.fromisoformat(r["created_at"]),
            )
            for r in rows
        ]
