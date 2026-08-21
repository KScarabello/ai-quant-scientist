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
)
from ..models.research import new_id
from ..models.enums import ResearchAction, SpecRevisionProposalStatus
from ..models.critic import CriticInvocation, CriticDecision


SCHEMA_VERSION = 5


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
                    connection.execute("UPDATE schema_version SET version = ? WHERE id = 1", (SCHEMA_VERSION,))
                else:
                    raise RuntimeError(f"Unsupported schema version {v}")

    def _dumps(self, value: Any) -> str:
        return json.dumps(value, sort_keys=True, separators=(",", ":"))

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
                id, research_run_id, version, hypothesis_id, parent_spec_id, revision_proposal_id, parameters_json,
                created_at, frozen_at, is_frozen
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                spec.id,
                spec.research_run_id,
                spec.version,
                spec.hypothesis_id,
                spec.parent_spec_id,
                spec.revision_proposal_id,
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
        return ResearchSpec(
            id=row["id"],
            research_run_id=row["research_run_id"],
            version=row["version"],
            hypothesis_id=row["hypothesis_id"],
            parent_spec_id=row["parent_spec_id"],
            revision_proposal_id=row["revision_proposal_id"],
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
                    id, research_run_id, version, hypothesis_id, parent_spec_id, revision_proposal_id, parameters_json,
                    created_at, frozen_at, is_frozen
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    new_spec_id,
                    proposal.research_run_id,
                    next_version,
                    parent["hypothesis_id"],
                    proposal.parent_spec_id,
                    proposal.id,
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
