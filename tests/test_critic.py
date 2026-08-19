from __future__ import annotations

import pytest

from ai_quant_scientist.services.research_critic import FakeResearchCritic, run_critic_for_run
from ai_quant_scientist.storage.sqlite_store import SQLiteStore
from ai_quant_scientist.services.spec_builder import SpecBuilder
from ai_quant_scientist.tools.stub_backtester import StubBacktester
from ai_quant_scientist.evaluation.result_evaluator import ResultEvaluator
from ai_quant_scientist.models.evaluation import ResultEvaluationPolicy
from ai_quant_scientist.policies.transitions import ResearchTransitionPolicy
from ai_quant_scientist.orchestrator.orchestrator import ResearchOrchestrator
from ai_quant_scientist.models.enums import ResearchAction


def build_orchestrator(db_path):
    return ResearchOrchestrator(
        store=SQLiteStore(db_path),
        transition_policy=ResearchTransitionPolicy(),
        spec_builder=SpecBuilder(),
        research_tool=StubBacktester(),
        result_evaluator=ResultEvaluator(),
        evaluation_policy=ResultEvaluationPolicy(),
    )


def test_critic_runs_only_on_iterate(tmp_path):
    db_path = tmp_path / "ai_quant_scientist.db"
    orch = build_orchestrator(db_path)
    # create run that will produce ITERATE
    run = orch.create_research(hypothesis_statement="h", rationale="r", parameters={"signal_threshold": 3.0, "lookback": 20}, max_iterations=3)
    orch.run_next_step(run.id)
    orch.run_next_step(run.id)
    state = orch.get_state(run.id)
    assert state.next_required_action == ResearchAction.REVISION_REQUIRED
    critic = FakeResearchCritic()
    inv, decision, result = run_critic_for_run(store=orch.store, critic=critic, run_id=run.id)
    assert inv is not None
    assert decision is not None
    # decision should be a PROPOSE or NO_USEFUL depending on fake logic


def test_critic_rejects_when_not_iterate(tmp_path):
    db_path = tmp_path / "ai_quant_scientist.db"
    orch = build_orchestrator(db_path)
    # create run that will produce PROMOTE -> critic should error
    run = orch.create_research(hypothesis_statement="h", rationale="r", parameters={"signal_threshold": 1.0, "lookback": 20}, max_iterations=3)
    orch.run_next_step(run.id)
    # this step should promote; trying to run critic should raise
    with pytest.raises(RuntimeError):
        # run_critic_for_run should raise because latest evaluation is not ITERATE
        run_critic_for_run(store=orch.store, critic=FakeResearchCritic(), run_id=run.id)