"""Explicit state-transition policy for research runs."""

from __future__ import annotations

from dataclasses import replace

from ..models.enums import ResearchStage, RunStatus
from ..models.research import ResearchRun


class InvalidTransitionError(ValueError):
    """Raised when a requested stage transition is not allowed."""


class IterationLimitExceededError(ValueError):
    """Raised when discovery work would exceed the configured iteration bound."""


class ResearchTransitionPolicy:
    """Encodes the legal stage graph and bounded discovery rules."""

    _legal_transitions: dict[ResearchStage, set[ResearchStage]] = {
        ResearchStage.IDEA: {ResearchStage.DISCOVERY},
        ResearchStage.DISCOVERY: {ResearchStage.DISCOVERY, ResearchStage.REPLICATION, ResearchStage.REJECTED},
        ResearchStage.REPLICATION: {ResearchStage.REJECTED, ResearchStage.HOLDOUT},
    }

    def validate_transition(self, current_stage: ResearchStage, next_stage: ResearchStage, *, iteration_count: int, max_iterations: int) -> None:
        if current_stage == ResearchStage.REJECTED:
            raise InvalidTransitionError("REJECTED runs are terminal and cannot transition again")

        allowed = self._legal_transitions.get(current_stage, set())
        if next_stage not in allowed:
            raise InvalidTransitionError(f"Illegal transition: {current_stage.value} -> {next_stage.value}")

        if current_stage == ResearchStage.DISCOVERY and next_stage == ResearchStage.DISCOVERY and iteration_count >= max_iterations:
            raise IterationLimitExceededError("Discovery iteration limit has been reached")

    def transition_run(
        self,
        run: ResearchRun,
        next_stage: ResearchStage,
        *,
        increment_iteration: bool = False,
    ) -> ResearchRun:
        self.validate_transition(
            run.stage,
            next_stage,
            iteration_count=run.iteration_count,
            max_iterations=run.max_iterations,
        )

        iteration_count = run.iteration_count + 1 if increment_iteration else run.iteration_count
        status = run.status
        if next_stage == ResearchStage.REJECTED:
            status = RunStatus.REJECTED

        return replace(
            run,
            stage=next_stage,
            status=status,
            iteration_count=iteration_count,
        )
