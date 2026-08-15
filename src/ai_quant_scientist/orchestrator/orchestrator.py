"""Deterministic research orchestrator for V0."""

from __future__ import annotations

from dataclasses import replace

from ..evaluation.result_evaluator import ResultEvaluator
from ..models.evaluation import EvaluationDecision, EvaluationRecommendation, ResultEvaluationPolicy
from ..models.enums import ResearchStage, RunStatus, ResearchAction
from ..models.research import AuditEvent, Hypothesis, ResearchAttempt, ResearchRun, new_id, record_to_state, utcnow
from ..policies.transitions import IterationLimitExceededError, ResearchTransitionPolicy
from ..services.spec_builder import SpecBuilder
from ..storage.sqlite_store import SQLiteStore
from ..tools.base import ResearchTool


class NoNextActionError(RuntimeError):
    """Raised when the orchestrator has no V0 action for the current stage."""


class ResearchOrchestrator:
    """Coordinates persistence, policy validation, and deterministic tool execution."""

    def __init__(
        self,
        *,
        store: SQLiteStore,
        transition_policy: ResearchTransitionPolicy,
        spec_builder: SpecBuilder,
        research_tool: ResearchTool,
        result_evaluator: ResultEvaluator,
        evaluation_policy: ResultEvaluationPolicy,
    ) -> None:
        self.store = store
        self.transition_policy = transition_policy
        self.spec_builder = spec_builder
        self.research_tool = research_tool
        self.result_evaluator = result_evaluator
        self.evaluation_policy = evaluation_policy

    def create_research(
        self,
        *,
        hypothesis_statement: str,
        rationale: str,
        parameters: dict[str, object],
        max_iterations: int,
    ) -> ResearchRun:
        run_id = new_id()
        hypothesis = Hypothesis(
            id=new_id(),
            research_run_id=run_id,
            statement=hypothesis_statement,
            rationale=rationale,
        )
        spec = self.spec_builder.build(research_run_id=run_id, hypothesis=hypothesis, parameters=parameters)
        run = ResearchRun(
            id=run_id,
            stage=ResearchStage.IDEA,
            status=RunStatus.ACTIVE,
            next_required_action=ResearchAction.NONE,
            hypothesis_id=hypothesis.id,
            active_spec_id=spec.id,
            iteration_count=0,
            max_iterations=max_iterations,
            created_at=utcnow(),
            updated_at=utcnow(),
        )
        audit_event = AuditEvent(
            id=new_id(),
            research_run_id=run.id,
            event_type="RUN_CREATED",
            action="create_research",
            reason="Created research run with immutable hypothesis and frozen spec",
            state_before={},
            state_after=record_to_state(run),
            metadata={
                "hypothesis": record_to_state(hypothesis),
                "spec": record_to_state(spec),
                "parameters": dict(parameters),
            },
        )
        self.store.create_research_bundle(run, hypothesis, spec, audit_event)
        return run

    def get_state(self, run_id: str) -> ResearchRun | None:
        return self.store.get_research_run(run_id)

    def run_next_step(self, run_id: str) -> ResearchRun:
        run = self.store.get_research_run(run_id)
        if run is None:
            raise KeyError(f"Unknown research run: {run_id}")

        if run.stage == ResearchStage.IDEA:
            return self._advance_from_idea(run)

        if run.stage == ResearchStage.DISCOVERY:
            return self._execute_discovery_step(run)

        raise NoNextActionError(f"No V0 action is defined for stage {run.stage.value}")

    def _advance_from_idea(self, run: ResearchRun) -> ResearchRun:
        next_run = self.transition_policy.transition_run(run, ResearchStage.DISCOVERY)
        next_run = replace(next_run, updated_at=utcnow())
        audit_event = AuditEvent(
            id=new_id(),
            research_run_id=run.id,
            event_type="STAGE_TRANSITION",
            action="enter_discovery",
            reason="Initial transition from IDEA into DISCOVERY",
            state_before=record_to_state(run),
            state_after=record_to_state(next_run),
            metadata={"transition": "IDEA->DISCOVERY"},
        )
        self.store.update_research_run(next_run)
        self.store.record_audit_event(audit_event)
        return next_run

    def _execute_discovery_step(self, run: ResearchRun) -> ResearchRun:
        if run.next_required_action == ResearchAction.REVISION_REQUIRED:
            raise NoNextActionError("Revision required before next discovery attempt")

        if run.iteration_count >= run.max_iterations:
            raise IterationLimitExceededError("Discovery iteration limit has been reached")

        spec = self.store.get_spec(run.active_spec_id)
        if spec is None:
            raise KeyError(f"Unknown spec: {run.active_spec_id}")
        if not spec.is_frozen:
            raise ValueError("Active research spec must be frozen before execution")

        started_at = utcnow()
        attempt = ResearchAttempt(
            id=new_id(),
            research_run_id=run.id,
            spec_id=spec.id,
            attempt_number=run.iteration_count + 1,
            stage=run.stage,
            started_at=started_at,
            completed_at=started_at,
            status="COMPLETED",
        )
        result = self.research_tool.run(spec=spec, attempt_id=attempt.id)
        decision = self.result_evaluator.evaluate(
            run=run,
            attempt=attempt,
            result=result,
            policy=self.evaluation_policy,
        )
        next_stage = self._next_stage_for_evaluation(run=run, decision=decision)
        next_run = self.transition_policy.transition_run(run, next_stage, increment_iteration=True)
        # If evaluator recommended ITERATE but iterations remain, require an explicit spec revision
        if decision.recommendation == EvaluationRecommendation.ITERATE and run.iteration_count + 1 < run.max_iterations:
            next_run = replace(next_run, next_required_action=ResearchAction.REVISION_REQUIRED)
        next_run = replace(next_run, updated_at=utcnow())

        audit_event = AuditEvent(
            id=new_id(),
            research_run_id=run.id,
            event_type="RESULT_EVALUATED",
            action=f"run_{self.research_tool.name}",
            reason="Executed deterministic discovery tool, evaluated the evidence, and applied a legal transition",
            state_before=record_to_state(run),
            state_after=record_to_state(next_run),
            metadata={
                "attempt": record_to_state(attempt),
                "result": record_to_state(result),
                "evaluation": record_to_state(decision),
                "transition": f"DISCOVERY->{next_stage.value}",
            },
        )

        self.store.record_discovery_outcome(attempt, result, decision, audit_event, next_run)
        return next_run

    def _next_stage_for_evaluation(self, *, run: ResearchRun, decision: EvaluationDecision) -> ResearchStage:
        if decision.recommendation == EvaluationRecommendation.PROMOTE:
            return ResearchStage.REPLICATION
        if decision.recommendation == EvaluationRecommendation.REJECT:
            return ResearchStage.REJECTED
        if run.iteration_count + 1 >= run.max_iterations:
            return ResearchStage.REJECTED
        return ResearchStage.DISCOVERY
