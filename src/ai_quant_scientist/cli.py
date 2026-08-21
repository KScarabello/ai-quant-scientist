"""Command-line interface for AI Quant Scientist V0."""

from __future__ import annotations

import argparse
from pathlib import Path

from .evaluation.result_evaluator import ResultEvaluator
from .models.evaluation import ResultEvaluationPolicy
from .orchestrator.orchestrator import ResearchOrchestrator
from .policies.transitions import ResearchTransitionPolicy
from .services.spec_builder import SpecBuilder
from .storage.sqlite_store import SQLiteStore
from .tools.stub_backtester import StubBacktester
from .models.research import SpecRevisionProposal, new_id
from .models.enums import ResearchAction, SpecRevisionProposalStatus
from .services.research_critic import FakeResearchCritic, run_critic_for_run
from .models.critic import CriticInvocation
from .capabilities.v1_registry import build_v1_registry
from .capabilities.gate import ResearchCandidate, ResearchFeasibilityGate, GateDecision
from .capabilities.intake import GovernedResearchIntake
from .capabilities.models import (
    AssetClass, DataKind, DataRequirement, Resolution, ToolRequirement,
)


DEFAULT_DB_PATH = Path("data/ai_quant_scientist.db")


def build_orchestrator(db_path: Path) -> ResearchOrchestrator:
    store = SQLiteStore(db_path)
    return ResearchOrchestrator(
        store=store,
        transition_policy=ResearchTransitionPolicy(),
        spec_builder=SpecBuilder(),
        research_tool=StubBacktester(),
        result_evaluator=ResultEvaluator(),
        evaluation_policy=ResultEvaluationPolicy(),
    )


def cmd_init(args: argparse.Namespace) -> int:
    SQLiteStore(args.db_path)
    print(f"Initialized SQLite database at {args.db_path}")
    return 0


def cmd_create(args: argparse.Namespace) -> int:
    orchestrator = build_orchestrator(args.db_path)
    run = orchestrator.create_research(
        hypothesis_statement=args.hypothesis,
        rationale=args.rationale,
        parameters={"signal_threshold": args.signal_threshold, "lookback": args.lookback},
        max_iterations=args.max_iterations,
    )
    print(f"Created run {run.id} in stage {run.stage.value} with spec {run.active_spec_id}")
    return 0


def cmd_step(args: argparse.Namespace) -> int:
    orchestrator = build_orchestrator(args.db_path)
    before = orchestrator.get_state(args.run_id)
    if before is None:
        raise SystemExit(f"Unknown run: {args.run_id}")
    after = orchestrator.run_next_step(args.run_id)
    print(f"Run {after.id}: {before.stage.value} -> {after.stage.value} | iterations={after.iteration_count}")
    return 0


def cmd_critique(args: argparse.Namespace) -> int:
    orchestrator = build_orchestrator(args.db_path)
    run = orchestrator.get_state(args.run_id)
    if run is None:
        raise SystemExit(f"Unknown run: {args.run_id}")
    if run.next_required_action != ResearchAction.REVISION_REQUIRED:
        raise SystemExit("Critic may only run when a revision is required")
    critic = FakeResearchCritic()
    inv, decision, result = run_critic_for_run(store=orchestrator.store, critic=critic, run_id=run.id)
    print(f"Critic invocation {inv.id}: validation={inv.validation_status}")
    if result.get("proposal"):
        print(f"Created proposal {result.get('proposal')}")
    else:
        print(f"Decision: {decision.decision_type.value} | rationale: {decision.rationale}")
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    orchestrator = build_orchestrator(args.db_path)
    run = orchestrator.get_state(args.run_id)
    if run is None:
        raise SystemExit(f"Unknown run: {args.run_id}")
    hypothesis = orchestrator.store.get_hypothesis(run.hypothesis_id)
    spec = orchestrator.store.get_spec(run.active_spec_id)
    print(f"Run: {run.id}")
    print(f"Stage: {run.stage.value}")
    print(f"Status: {run.status.value}")
    print(f"Iterations: {run.iteration_count}/{run.max_iterations}")
    if hypothesis is not None:
        print(f"Hypothesis: {hypothesis.statement}")
    if spec is not None:
        print(f"Spec parameters: {spec.parameters}")
    latest_evaluation = orchestrator.store.get_latest_evaluation_decision(run.id)
    if latest_evaluation is not None:
        print(f"Evaluation: {latest_evaluation.recommendation.value}")
        print("Reasons:")
        for reason_code in latest_evaluation.reason_codes:
            print(f"- {reason_code}")
    return 0


def cmd_audit(args: argparse.Namespace) -> int:
    orchestrator = build_orchestrator(args.db_path)
    events = orchestrator.store.list_audit_events(args.run_id)
    for event in events:
        print(f"{event.created_at.isoformat()} | {event.event_type} | {event.action} | {event.reason}")
    return 0


def cmd_specs(args: argparse.Namespace) -> int:
    orchestrator = build_orchestrator(args.db_path)
    run = orchestrator.get_state(args.run_id)
    if run is None:
        raise SystemExit(f"Unknown run: {args.run_id}")
    # list specs for run by querying attempts and specs table
    # gather all specs by scanning attempts and active_spec
    specs = []
    # fetch active and all known specs by reading attempts and active id
    active = orchestrator.store.get_spec(run.active_spec_id)
    if active is not None:
        specs.append(active)
    # also scan attempts
    attempts = orchestrator.store.get_attempts(run.id)
    for a in attempts:
        s = orchestrator.store.get_spec(a.spec_id)
        if s is not None and s.id != (active.id if active else None):
            specs.append(s)
    # dedupe by id
    seen = set()
    ordered = []
    for s in specs:
        if s.id in seen:
            continue
        seen.add(s.id)
        ordered.append(s)

    for s in ordered:
        active_flag = "active" if run.active_spec_id == s.id else ""
        parent = s.parent_spec_id or "none"
        print(f"V{s.version} {s.id} active={active_flag}\n    parent={parent}\n    parameters={s.parameters}")
    return 0


def cmd_propose_revision(args: argparse.Namespace) -> int:
    orchestrator = build_orchestrator(args.db_path)
    run = orchestrator.get_state(args.run_id)
    if run is None:
        raise SystemExit(f"Unknown run: {args.run_id}")
    if run.next_required_action != ResearchAction.REVISION_REQUIRED:
        raise SystemExit("Revision not currently required for this run")
    spec = orchestrator.store.get_spec(run.active_spec_id)
    if spec is None:
        raise SystemExit("Active spec not found")

    proposed = dict(spec.parameters)
    if args.signal_threshold is not None:
        proposed["signal_threshold"] = args.signal_threshold
    if args.lookback is not None:
        proposed["lookback"] = args.lookback

    proposal = SpecRevisionProposal(
        id=new_id(),
        research_run_id=run.id,
        parent_spec_id=spec.id,
        trigger_evaluation_id=args.trigger_evaluation_id,
        proposed_parameters=proposed,
        change_summary=args.summary or "manual revision",
        reason=args.reason or "manual",
        change_record={k: {"before": spec.parameters.get(k), "after": v} for k, v in proposed.items() if spec.parameters.get(k) != v},
        status=SpecRevisionProposalStatus.PROPOSED,
    )

    orchestrator.store.create_spec_revision_proposal(proposal)
    print(f"Created proposal {proposal.id} for run {run.id}")
    return 0


def cmd_accept_revision(args: argparse.Namespace) -> int:
    orchestrator = build_orchestrator(args.db_path)
    proposal = orchestrator.store.get_spec_revision_proposal(args.proposal_id)
    if proposal is None:
        raise SystemExit(f"Unknown proposal: {args.proposal_id}")
    # acceptance performed in store (transactional)
    accepted = orchestrator.store.accept_spec_revision_proposal(args.proposal_id)
    print(f"Accepted proposal {args.proposal_id}; created spec {accepted.accepted_spec_id}")
    return 0


def cmd_capabilities(args: argparse.Namespace) -> int:
    import json
    registry = build_v1_registry()
    caps = [
        {
            "capability_id": c.capability_id,
            "capability_type": c.capability_type,
            "data_kind": c.data_kind.value,
            "asset_classes": [a.value for a in c.asset_classes],
            "resolutions": [r.value for r in c.resolutions],
            "instruments": sorted(c.instruments) if c.instruments is not None else None,
            "available_fields": sorted(c.available_fields) if c.available_fields is not None else None,
            "supported_parameters": sorted(c.supported_parameters) if c.supported_parameters is not None else None,
            "coverage_start": c.coverage_start.isoformat() if c.coverage_start else None,
            "coverage_end": c.coverage_end.isoformat() if c.coverage_end else None,
            "point_in_time": c.point_in_time,
            "provider": c.provider,
            "enabled": c.enabled,
            "version": c.version,
            "metadata": c.metadata,
        }
        for c in registry.list_capabilities()
    ]
    output = {
        "registry_version": registry.version,
        "registry_fingerprint": registry.fingerprint,
        "capabilities": caps,
    }
    print(json.dumps(output, indent=2))
    return 0


def cmd_feasibility_check(args: argparse.Namespace) -> int:
    import json
    registry = build_v1_registry()
    gate = ResearchFeasibilityGate()

    if args.preset == "synthetic":
        candidate = ResearchCandidate.create(
            hypothesis_statement="signal_threshold controls trade frequency",
            hypothesis_rationale="SYNTHETIC: deterministic stub experiment",
            requirements=[
                DataRequirement(requirement_id="data", data_kind=DataKind.SYNTHETIC_PARAMETRIC,
                                asset_class=AssetClass.SYNTHETIC),
                ToolRequirement(requirement_id="tool", tool_name="stub_backtester_v1"),
            ],
        )
    elif args.preset == "ohlcv-mes":
        candidate = ResearchCandidate.create(
            hypothesis_statement="order-book imbalance predicts MES futures returns",
            hypothesis_rationale="microstructure hypothesis requiring real market data",
            requirements=[
                DataRequirement(requirement_id="ob", data_kind=DataKind.ORDER_BOOK,
                                asset_class=AssetClass.FUTURES, instruments=("MES",),
                                resolution=Resolution.SECOND_1),
                ToolRequirement(requirement_id="tool", tool_name="futures_backtester"),
            ],
        )
    else:
        raise SystemExit(f"Unknown preset: {args.preset}. Available: synthetic, ohlcv-mes")

    decision = gate.evaluate(candidate, registry)
    fr = decision.feasibility_result
    output = {
        "candidate_id": decision.candidate_id,
        "decision": decision.decision.value,
        "gate_version": decision.gate_version,
        "registry_version": decision.registry_version,
        "registry_fingerprint": decision.registry_fingerprint,
        "satisfied_ids": list(fr.satisfied_ids),
        "unsatisfied_ids": list(fr.unsatisfied_ids),
        "reason_codes": [r.value for r in fr.reason_codes],
    }
    print(json.dumps(output, indent=2))
    return 0


def cmd_candidates(args: argparse.Namespace) -> int:
    import json
    store = SQLiteStore(args.db_path)
    candidates = store.list_research_candidates()
    output = [
        {
            "id": c.id,
            "hypothesis_statement": c.hypothesis_statement,
            "source": c.source,
            "requirement_count": len(c.requirements),
            "created_at": c.created_at.isoformat(),
        }
        for c in candidates
    ]
    print(json.dumps(output, indent=2))
    return 0


def cmd_candidate(args: argparse.Namespace) -> int:
    import json
    from .capabilities.serialization import requirements_to_json, compute_candidate_fingerprint
    store = SQLiteStore(args.db_path)
    candidate = store.get_research_candidate(args.candidate_id)
    if candidate is None:
        raise SystemExit(f"Unknown candidate: {args.candidate_id}")
    import json as _json
    output = {
        "id": candidate.id,
        "hypothesis_statement": candidate.hypothesis_statement,
        "hypothesis_rationale": candidate.hypothesis_rationale,
        "source": candidate.source,
        "created_at": candidate.created_at.isoformat(),
        "scientific_fingerprint": compute_candidate_fingerprint(
            candidate.hypothesis_statement, candidate.hypothesis_rationale, candidate.requirements,
        ),
        "requirements": _json.loads(requirements_to_json(candidate.requirements)),
    }
    print(json.dumps(output, indent=2))
    return 0


def cmd_feasibility_history(args: argparse.Namespace) -> int:
    import json
    store = SQLiteStore(args.db_path)
    decisions = store.get_feasibility_decisions(args.candidate_id)
    output = [
        {
            "id": d.id,
            "gate_decision": d.gate_decision.value,
            "gate_version": d.gate_version,
            "registry_version": d.registry_version,
            "registry_fingerprint": d.registry_fingerprint,
            "satisfied_ids": list(d.satisfied_ids),
            "unsatisfied_ids": list(d.unsatisfied_ids),
            "reason_codes": [r.value for r in d.reason_codes],
            "evaluated_at": d.evaluated_at.isoformat(),
        }
        for d in decisions
    ]
    print(json.dumps(output, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ai_quant_scientist")
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="Initialize the SQLite database")
    init_parser.set_defaults(func=cmd_init)

    create_parser = subparsers.add_parser("create", help="Create a new research run")
    create_parser.add_argument("--hypothesis", required=True)
    create_parser.add_argument("--rationale", default="V0 manually supplied hypothesis")
    create_parser.add_argument("--signal-threshold", type=float, required=True)
    create_parser.add_argument("--lookback", type=int, required=True)
    create_parser.add_argument("--max-iterations", type=int, default=3)
    create_parser.set_defaults(func=cmd_create)

    step_parser = subparsers.add_parser("step", help="Run one orchestrated step")
    step_parser.add_argument("run_id")
    step_parser.set_defaults(func=cmd_step)

    show_parser = subparsers.add_parser("show", help="Show the current persisted run state")
    show_parser.add_argument("run_id")
    show_parser.set_defaults(func=cmd_show)

    audit_parser = subparsers.add_parser("audit", help="Show the audit history for a run")
    audit_parser.add_argument("run_id")
    audit_parser.set_defaults(func=cmd_audit)

    specs_parser = subparsers.add_parser("specs", help="List specs for a run")
    specs_parser.add_argument("run_id")
    specs_parser.set_defaults(func=cmd_specs)

    propose_parser = subparsers.add_parser("propose-revision", help="Propose a spec revision for a run")
    propose_parser.add_argument("run_id")
    propose_parser.add_argument("--signal-threshold", type=float)
    propose_parser.add_argument("--lookback", type=int)
    propose_parser.add_argument("--trigger-evaluation-id", dest="trigger_evaluation_id", default=None)
    propose_parser.add_argument("--summary", default=None)
    propose_parser.add_argument("--reason", default=None)
    propose_parser.set_defaults(func=cmd_propose_revision)

    accept_parser = subparsers.add_parser("accept-revision", help="Accept a spec revision proposal")
    accept_parser.add_argument("proposal_id")
    accept_parser.set_defaults(func=cmd_accept_revision)

    critique_parser = subparsers.add_parser("critique", help="Run AI research critic for a run (supervised)")
    critique_parser.add_argument("run_id")
    critique_parser.set_defaults(func=cmd_critique)

    capabilities_parser = subparsers.add_parser("capabilities", help="List registered capabilities and registry version")
    capabilities_parser.set_defaults(func=cmd_capabilities)

    feasibility_parser = subparsers.add_parser(
        "feasibility-check",
        help="Evaluate a named candidate preset against the V1 registry",
    )
    feasibility_parser.add_argument(
        "--preset", choices=["synthetic", "ohlcv-mes"], default="synthetic",
        help="built-in candidate preset (default: synthetic)",
    )
    feasibility_parser.set_defaults(func=cmd_feasibility_check)

    candidates_parser = subparsers.add_parser("candidates", help="List persisted research candidates")
    candidates_parser.set_defaults(func=cmd_candidates)

    candidate_parser = subparsers.add_parser("candidate", help="Show a specific research candidate")
    candidate_parser.add_argument("candidate_id")
    candidate_parser.set_defaults(func=cmd_candidate)

    fh_parser = subparsers.add_parser("feasibility-history", help="Show feasibility decisions for a candidate")
    fh_parser.add_argument("candidate_id")
    fh_parser.set_defaults(func=cmd_feasibility_history)

    evaluations_parser = subparsers.add_parser("evaluations", help="Show persisted evaluation decisions for a run")
    evaluations_parser.add_argument("run_id")
    evaluations_parser.set_defaults(func=cmd_evaluations)

    return parser


def cmd_evaluations(args: argparse.Namespace) -> int:
    orchestrator = build_orchestrator(args.db_path)
    decisions = orchestrator.store.list_evaluation_decisions(args.run_id)
    for decision in decisions:
        print(f"{decision.created_at.isoformat()} | {decision.recommendation.value} | {decision.summary}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
