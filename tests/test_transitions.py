from __future__ import annotations

from datetime import datetime, timezone

import pytest

from ai_quant_scientist.models.enums import ResearchStage, RunStatus
from ai_quant_scientist.models.research import ResearchRun
from ai_quant_scientist.policies.transitions import InvalidTransitionError, IterationLimitExceededError, ResearchTransitionPolicy


def make_run(stage: ResearchStage = ResearchStage.IDEA, iteration_count: int = 0, max_iterations: int = 3) -> ResearchRun:
    now = datetime.now(timezone.utc)
    return ResearchRun(
        id="run-1",
        stage=stage,
        status=RunStatus.ACTIVE,
        hypothesis_id="hyp-1",
        active_spec_id="spec-1",
        iteration_count=iteration_count,
        max_iterations=max_iterations,
        created_at=now,
        updated_at=now,
    )


def test_legal_transitions_pass() -> None:
    policy = ResearchTransitionPolicy()
    policy.validate_transition(ResearchStage.IDEA, ResearchStage.DISCOVERY, iteration_count=0, max_iterations=3)
    policy.validate_transition(ResearchStage.DISCOVERY, ResearchStage.REPLICATION, iteration_count=1, max_iterations=3)
    policy.validate_transition(ResearchStage.DISCOVERY, ResearchStage.REJECTED, iteration_count=1, max_iterations=3)


def test_illegal_transitions_fail() -> None:
    policy = ResearchTransitionPolicy()
    with pytest.raises(InvalidTransitionError):
        policy.validate_transition(ResearchStage.IDEA, ResearchStage.PRODUCTION, iteration_count=0, max_iterations=3)

    with pytest.raises(InvalidTransitionError):
        policy.validate_transition(ResearchStage.REJECTED, ResearchStage.DISCOVERY, iteration_count=0, max_iterations=3)

    with pytest.raises(InvalidTransitionError):
        policy.validate_transition(ResearchStage.DISCOVERY, ResearchStage.PRODUCTION, iteration_count=0, max_iterations=3)


def test_rejected_is_terminal() -> None:
    policy = ResearchTransitionPolicy()
    with pytest.raises(InvalidTransitionError):
        policy.transition_run(make_run(stage=ResearchStage.REJECTED), ResearchStage.DISCOVERY)


def test_discovery_iteration_limit_is_enforced() -> None:
    policy = ResearchTransitionPolicy()
    with pytest.raises(IterationLimitExceededError):
        policy.validate_transition(ResearchStage.DISCOVERY, ResearchStage.DISCOVERY, iteration_count=3, max_iterations=3)
