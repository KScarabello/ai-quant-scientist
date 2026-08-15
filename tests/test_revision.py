from __future__ import annotations

from ai_quant_scientist.orchestrator.orchestrator import ResearchOrchestrator, NoNextActionError
from ai_quant_scientist.policies.transitions import ResearchTransitionPolicy
from ai_quant_scientist.services.spec_builder import SpecBuilder
from ai_quant_scientist.storage.sqlite_store import SQLiteStore
from ai_quant_scientist.tools.stub_backtester import StubBacktester
from ai_quant_scientist.evaluation.result_evaluator import ResultEvaluator
from ai_quant_scientist.models.evaluation import ResultEvaluationPolicy, EvaluationRecommendation
import pytest

from ai_quant_scientist.models.research import SpecRevisionProposal, new_id
from ai_quant_scientist.models.enums import ResearchAction, SpecRevisionProposalStatus


def build_orchestrator(db_path):
    return ResearchOrchestrator(
        store=SQLiteStore(db_path),
        transition_policy=ResearchTransitionPolicy(),
        spec_builder=SpecBuilder(),
        research_tool=StubBacktester(),
        result_evaluator=ResultEvaluator(),
        evaluation_policy=ResultEvaluationPolicy(),
    )


def test_iterate_requires_revision_and_accepting_creates_new_spec(tmp_path) -> None:
    db_path = tmp_path / "ai_quant_scientist.db"
    orch = build_orchestrator(db_path)

    run = orch.create_research(
        hypothesis_statement="h",
        rationale="r",
        parameters={"signal_threshold": 3.0, "lookback": 20},
        max_iterations=3,
    )

    # enter discovery
    orch.run_next_step(run.id)

    # first discovery attempt -> ITERATE
    after = orch.run_next_step(run.id)
    decisions = orch.store.list_evaluation_decisions(run.id)
    assert len(decisions) == 1
    assert decisions[0].recommendation == EvaluationRecommendation.ITERATE

    # system should now require a revision
    run_state = orch.get_state(run.id)
    assert run_state.next_required_action == ResearchAction.REVISION_REQUIRED

    # cannot run discovery again until revision accepted
    with pytest.raises(NoNextActionError):
        orch.run_next_step(run.id)

    # propose a deterministic revision: change threshold
    latest_decision = decisions[0]
    current_spec = orch.store.get_spec(run_state.active_spec_id)
    proposed = dict(current_spec.parameters)
    proposed["signal_threshold"] = 2.5

    proposal = SpecRevisionProposal(
        id=new_id(),
        research_run_id=run.id,
        parent_spec_id=current_spec.id,
        trigger_evaluation_id=latest_decision.id,
        proposed_parameters=proposed,
        change_summary="lower threshold",
        reason="manual",
        change_record={"signal_threshold": {"before": current_spec.parameters["signal_threshold"], "after": 2.5}},
        status=SpecRevisionProposalStatus.PROPOSED,
    )

    orch.store.create_spec_revision_proposal(proposal)

    # accept proposal -> creates new frozen spec and activates it
    orch.store.accept_spec_revision_proposal(proposal.id)

    updated_run = orch.get_state(run.id)
    new_spec = orch.store.get_spec(updated_run.active_spec_id)
    assert new_spec.parent_spec_id == current_spec.id
    assert new_spec.version == current_spec.version + 1

    # prior attempts must still reference the original spec
    attempts = orch.store.get_attempts(run.id)
    assert attempts[0].spec_id == current_spec.id


def test_cross_run_parent_spec_forbidden(tmp_path) -> None:
    db_path = tmp_path / "ai_quant_scientist.db"
    store = SQLiteStore(db_path)
    # create two runs
    orch = build_orchestrator(db_path)
    r1 = orch.create_research(hypothesis_statement="h1", rationale="r", parameters={"signal_threshold": 2.0, "lookback": 20}, max_iterations=3)
    r2 = orch.create_research(hypothesis_statement="h2", rationale="r", parameters={"signal_threshold": 2.0, "lookback": 20}, max_iterations=3)

    spec1 = store.get_spec(r1.active_spec_id)

    # attempt to create proposal in run 2 that references spec1 -> should fail on accept
    proposal = SpecRevisionProposal(
        id=new_id(),
        research_run_id=r2.id,
        parent_spec_id=spec1.id,
        trigger_evaluation_id=None,
        proposed_parameters={**spec1.parameters, "signal_threshold": 1.5},
        change_summary="cross-run",
        reason="test",
        change_record={"signal_threshold": {"before": spec1.parameters["signal_threshold"], "after": 1.5}},
        status=SpecRevisionProposalStatus.PROPOSED,
    )

    store.create_spec_revision_proposal(proposal)

    with pytest.raises(ValueError):
        store.accept_spec_revision_proposal(proposal.id)
