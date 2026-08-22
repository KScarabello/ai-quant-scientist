AI Quant Scientist

AI does the science.
Software measures reality.
Governance keeps the scientist honest.

## Project Purpose

`ai-quant-scientist` is a governed quantitative research system built around a strict boundary:
- AI components propose bounded scientific intent.
- Deterministic components validate structure, measure outcomes, govern lifecycle transitions, and persist authoritative history.

The repository already implements both a bounded Research Critic and a bounded Hypothesis Scientist. It does not run an autonomous open-ended research loop.

## Current Architecture

Two bounded AI paths are implemented today.

Research Critic path:

`ResearchRun`
-> measured results
-> `ResearchCritic`
-> bounded revision intent or `NO_USEFUL_REVISION`
-> deterministic `RevisionPlanner` V1
-> governed revision proposal / acceptance flow

Hypothesis Scientist path:

`ResearchBrief`
-> `HypothesisScientist`
-> structured decision
-> deterministic validator
-> `ResearchCandidate`
-> `GovernedResearchIntake`
-> deterministic feasibility gate
-> `READY_FOR_SPEC` or `BLOCKED_CAPABILITY`

Key implementation files:
- `src/ai_quant_scientist/services/research_critic.py`
- `src/ai_quant_scientist/services/revision_planner.py`
- `src/ai_quant_scientist/services/hypothesis_scientist.py`
- `src/ai_quant_scientist/capabilities/gate.py`
- `src/ai_quant_scientist/capabilities/intake.py`

## AI Authority Boundaries

### Hypothesis Scientist

The Hypothesis Scientist is implemented and bounded.

It may:
- originate exactly one falsifiable hypothesis
- explain scientific rationale
- declare explicit data and tool requirements
- return `NO_HYPOTHESIS` when the brief is underspecified

It may not:
- declare feasibility or capability availability
- create a `ResearchSpec`
- start or run research
- assign authoritative governance fields

Prompt status:
- `v1` is preserved as the original live-tested prompt
- `v2` is the current default output-contract prompt

Current OpenAI adapter defaults:
- model: `gpt-5.6-terra`
- prompt version: `v2`

### Research Critic

The Research Critic is implemented and separately bounded.

It may:
- propose one bounded revision intent
- return `NO_USEFUL_REVISION`

It may not:
- override evaluator authority
- directly mutate production specs
- choose authoritative lifecycle transitions

The critic proposes intent; `RevisionPlanner` V1 deterministically chooses the exact value.

## Deterministic Governance

The repository’s core invariants are deterministic:
- frozen `ResearchSpec`s are immutable
- SQLite is canonical scientific history
- lifecycle transitions are explicitly governed
- `ResultEvaluator` owns `PROMOTE` / `ITERATE` / `REJECT`
- `BLOCKED_CAPABILITY` is not scientific rejection
- requirements are never inferred from prose
- append-only feasibility and invocation history is preserved

Important deterministic components:
- `src/ai_quant_scientist/evaluation/result_evaluator.py`
- `src/ai_quant_scientist/policies/transitions.py`
- `src/ai_quant_scientist/storage/sqlite_store.py`
- `src/ai_quant_scientist/services/revision_planner.py`

## Current Implemented Milestones

- `V0.1-V0.2`: deterministic run backbone and result evaluation
- `V0.3-V0.4`: immutable spec lineage and hardening
- `V0.5-V0.8`: bounded Research Critic, context hardening, deterministic RevisionPlanner V1
- `V0.9-V0.11`: truthful capability registry, deterministic feasibility gate, durable governed intake
- `V0.12A`: bounded Hypothesis Scientist, eval harness, schema `v6` invocation persistence
- `V0.12B`: hardened requirement contract between the Hypothesis Scientist and the CapabilityRegistry

For the detailed operational handoff, see `docs/ai/CURRENT_STATE.md`.

## Hypothesis Scientist

`V0.12B` hardens the requirement language between stochastic proposal generation and deterministic feasibility governance.

Current contract:
- canonical `ToolKind`
- exact new-authoritative tool matching
- no fuzzy matching
- AI does not know concrete capability IDs
- primitive canonical `required_fields`
- first-class `required_parameters`
- bounded `PriorCandidateSummary`
- historical legacy tool snapshots remain readable

This keeps historical evidence readable while preventing new authoritative candidates from using ambiguous tool names or pseudo-fields.

Primary files:
- `src/ai_quant_scientist/capabilities/models.py`
- `src/ai_quant_scientist/capabilities/registry.py`
- `src/ai_quant_scientist/capabilities/serialization.py`
- `src/ai_quant_scientist/models/hypothesis_scientist.py`
- `src/ai_quant_scientist/services/openai_hypothesis_scientist.py`
- `src/ai_quant_scientist/services/hypothesis_prompts.py`

## Research Critic

The Research Critic remains a separate implemented AI component with bounded authority and deterministic downstream governance.

Current state:
- OpenAI candidate model default: `gpt-5.6-luna`
- prompt version default: `v1`
- bounded output contract: revision intent, not exact numeric targets
- deterministic `RevisionPlanner` V1 materializes exact values

Primary files:
- `src/ai_quant_scientist/services/openai_research_critic.py`
- `src/ai_quant_scientist/services/research_critic.py`
- `src/ai_quant_scientist/services/revision_planner.py`

## Capability And Feasibility System

The production capability registry is intentionally truthful and sparse.

Current registered production capability:
- `stub_backtester_v1`
  - `data_kind`: `SYNTHETIC_PARAMETRIC`
  - `asset_class`: `SYNTHETIC`
  - `resolution`: `N/A`
  - `supported_parameters`: `signal_threshold`, `lookback`
  - `supported_tool_kinds`: `BACKTEST_EXECUTION`

Not currently registered as production reality:
- real market data
- real equities
- real futures
- real order-book data
- point-in-time fundamentals
- production statistical-analysis tools

The feasibility boundary is deterministic and fail-closed:
- unmet requirements become `BLOCKED_CAPABILITY`
- blocked candidates remain durable for later re-evaluation
- no capability is inferred from prose or missing metadata

Primary files:
- `src/ai_quant_scientist/capabilities/v1_registry.py`
- `src/ai_quant_scientist/capabilities/registry.py`
- `src/ai_quant_scientist/capabilities/gate.py`
- `src/ai_quant_scientist/capabilities/intake.py`

## Persistence

Current schema version: `v6`

SQLite persists authoritative history for:
- research runs
- hypotheses
- research specs
- revision proposals
- attempts
- evaluations
- critic invocations
- research candidates
- feasibility decisions
- hypothesis scientist invocations

Primary file:
- `src/ai_quant_scientist/storage/sqlite_store.py`

## Evals

Deterministic suite status:
- `PYTHONPATH=src pytest -q`
- current verified result: `393 passed`

Research Critic eval support:
- deterministic harness
- guarded live runners

Hypothesis Scientist eval support:
- 12-case harness in `evals/scientist_v1.json`
- guarded live runner requiring `--allow-live-api`
- default invocation makes zero API calls

Primary files:
- `src/ai_quant_scientist/evals/critic_eval.py`
- `src/ai_quant_scientist/evals/run_live_critic_eval.py`
- `src/ai_quant_scientist/evals/scientist_eval.py`
- `src/ai_quant_scientist/evals/run_live_scientist_eval.py`

## CLI

Useful deterministic commands:

```bash
PYTHONPATH=src python3 -m ai_quant_scientist.cli capabilities
PYTHONPATH=src python3 -m ai_quant_scientist.cli feasibility-check --preset synthetic
PYTHONPATH=src python3 -m ai_quant_scientist.cli candidates
PYTHONPATH=src python3 -m ai_quant_scientist.cli feasibility-history <candidate_id>
PYTHONPATH=src pytest -q
```

## Current Limitations And Future Work

- no autonomous loop
- no production Spec Builder after `READY_FOR_SPEC`
- no RAG or vector canonical memory
- no fake real-market capabilities
- no post-`V0.12B` live scientist rerun artifacts yet
- plain `pytest` still requires `PYTHONPATH=src`

## Historical Integrity Note

Historical information and artifacts are retained in the repository, but they should be read as historical evidence, not as the current architecture.

Examples:
- pre-fix critic live eval artifacts that lacked full constraint plumbing
- pre-`V0.12B` scientist live artifacts produced before canonical tool/data contract hardening
- older milestone-era architecture notes preserved in git history rather than presented as current README state
