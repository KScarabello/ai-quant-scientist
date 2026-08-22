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
    ExperimentCondition,
    ExperimentConditionRole,
    InitialExperimentCompletionRule,
    InitialExperimentPlan,
    InitialExperimentPlanProposal,
    InitialExperimentPlanProposalStatus,
    OutcomeContrast,
    ParameterSensitivityContrastResult,
    ResearchDesignIntent,
    ResearchDesignKind,
    SpecFeasibilityDecision,
    SpecFeasibilityPhase,
    SpecFeasibilityReasonCode,
    SpecFeasibilityStatus,
    SpecMaterializationProposal,
    SpecMaterializationProposalStatus,
    thaw_mapping,
)
from ..models.research_designer import ResearchDesignerInvocation


SCHEMA_VERSION = 9


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
                    created_at TEXT NOT NULL
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
            conn.execute(
                """INSERT OR IGNORE INTO initial_experiment_plans
                   (id, candidate_id, design_intent_id, candidate_feasibility_decision_id,
                    selected_capability_id, design_kind, independent_variable,
                    control_variables_json, dependent_outcomes_json, ordered_condition_ids_json,
                    completion_rule, materializer_version, materialization_policy_version,
                    materialization_policy_fingerprint, registry_version, registry_fingerprint,
                    created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    plan.id,
                    plan.candidate_id,
                    plan.design_intent_id,
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

    # ─── Research designer invocations ───────────────────────────────────────

    def save_research_designer_invocation(self, inv: ResearchDesignerInvocation) -> None:
        with self.connect() as conn:
            conn.execute(
                """INSERT OR IGNORE INTO research_designer_invocations
                   (id, candidate_id, candidate_snapshot_json, candidate_feasibility_decision_id,
                    prompt_version, ontology_version, ontology_fingerprint, intent_contract_version,
                    provider, model, raw_response, parsed_decision_json, validation_status,
                    validation_errors_json, resulting_design_intent_id, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    inv.id,
                    inv.candidate_id,
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

    def save_hypothesis_scientist_invocation(self, inv) -> None:
        with self.connect() as conn:
            conn.execute(
                """INSERT OR IGNORE INTO hypothesis_scientist_invocations
                   (id, research_brief_id, research_brief_snapshot, prompt_version,
                    provider, model, raw_response, parsed_decision_json,
                    validation_status, validation_errors_json, resulting_candidate_id,
                    created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    inv.id, inv.research_brief_id, inv.research_brief_snapshot,
                    inv.prompt_version, inv.provider, inv.model, inv.raw_response,
                    inv.parsed_decision_json, inv.validation_status,
                    inv.validation_errors_json, inv.resulting_candidate_id,
                    inv.created_at.isoformat(),
                ),
            )

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
                created_at=datetime.fromisoformat(r["created_at"]),
            )
            for r in rows
        ]
