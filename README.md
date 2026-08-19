AI Quant Scientist — V0.6 (OpenAI Research Critic adapter)

Overview
- Adds `OpenAIResearchCritic` adapter (Responses API, structured outputs).
- Provides a guarded live-runner for the `critic_v1` 15-case benchmark.

Key points
- Default candidate model: `gpt-5.6-luna` (configurable via `AI_QUANT_CRITIC_MODEL`).
- Uses OpenAI Responses API with a strict JSON schema for outputs.
- Critic output remains a proposal only: `CriticDecision` -> deterministic `CriticProposalValidator` -> `SpecRevisionProposal(status=PROPOSED)` (human acceptance required).

Live benchmark guard
- Live calls are refused unless `--allow-live-api` is passed to the live runner module.
- Support for `--max-cases` and `--case-id` so you can smoke-test a single case before running all 15.

Artifacts
- Live-run artifacts are written to `artifacts/evals/` as JSON; the runner does not mutate the authoritative DB.

Environment
- Provide `OPENAI_API_KEY` in environment for live runs.

Important: Luna is a candidate evaluation model only. Validation and acceptance remain deterministic and supervised.
AI Quant Scientist — V0.4 hardened

Summary
- Immutable, versioned `ResearchSpec` objects with durable `SpecRevisionProposal` records.
- Revision workflow requires an explicit proposal + acceptance before a revised spec becomes active.
- CLI commands: `specs`, `propose-revision`, `accept-revision` added for manual deterministic control.

Key invariants
- Frozen specs are immutable — attempts to mutate an existing frozen spec raise `FrozenSpecMutationError`.
- Versions are monotonically increasing per run and unique (DB enforces via unique index).
- `ITERATE` recommendations require a revision; the orchestrator will refuse to execute the same frozen spec again until a revision is accepted.

Minimal CLI example
```
PYTHONPATH=src python3 -m ai_quant_scientist.cli --db-path /tmp/demo.db init
PYTHONPATH=src python3 -m ai_quant_scientist.cli --db-path /tmp/demo.db create --hypothesis "H" --rationale "r" --signal-threshold 3.0 --lookback 20
PYTHONPATH=src python3 -m ai_quant_scientist.cli --db-path /tmp/demo.db step <RUN_ID>
PYTHONPATH=src python3 -m ai_quant_scientist.cli --db-path /tmp/demo.db propose-revision <RUN_ID> --signal-threshold 2.5 --reason "manual"
PYTHONPATH=src python3 -m ai_quant_scientist.cli --db-path /tmp/demo.db accept-revision <PROPOSAL_ID>
PYTHONPATH=src python3 -m ai_quant_scientist.cli --db-path /tmp/demo.db specs <RUN_ID>
```

Notes
- The system does not invent revisions. Parameters for proposals are supplied manually and frozen deterministically on acceptance.
- Future AI components may propose changes; the deterministic infrastructure will continue to validate, freeze, version, persist, and execute them.
# AI Quant Scientist

AI Quant Scientist is a deterministic quantitative research orchestration backbone. It is designed to eventually support a full research pipeline that can generate hypotheses, freeze machine-readable specs, run tools, evaluate evidence, and promote candidates through explicit scientific stages.

## V0 Scope

This repository currently provides infrastructure only:

- no LLM APIs
- no vector search or embeddings
- no real trading research or market-data APIs
- no backtesting engine beyond a deterministic stub
- no web service or GUI
- no autonomous open-ended loop

V0 does establish the core primitives needed for later stages:

- SQLite-backed authoritative persistence
- explicit research stages and transition policy
- frozen research specifications
- auditable actions and results
- deterministic result evaluation decisions
- bounded iteration
- resumable orchestration from disk

## Long-Term Research Pipeline

The intended long-term flow is:

Idea -> Discovery -> Replication -> Holdout -> Paper -> Shadow Live -> Tiny Capital -> Production

V0 actively exercises only:

- Idea
- Discovery
- Replication
- Rejected

The remaining stages are already present in the model so the architecture does not need to be rewritten later.

## How It Works

1. A user supplies a research hypothesis and simple parameters.
2. The orchestrator creates and persists a research run, hypothesis, and frozen research spec.
3. `run_next_step()` performs exactly one deterministic orchestration step.
4. The transition policy validates the requested stage change.
5. The stub backtester produces repeatable metrics from the frozen spec.
6. The result evaluator compares the measured evidence with frozen stub criteria and produces a structured decision.
7. The store records attempts, results, evaluation decisions, audit events, and the updated run state in SQLite.
8. A later process can reopen the same database and resume from the persisted state.

## Extension Points

These are intentionally not implemented yet, but the code is structured so they can be added later:

- Hypothesis Agent
- Context Builder
- Semantic Memory / Embeddings
- Real Backtest Adapters
- Result Evaluator
- Research Critic
- Holdout Gate
- Paper Generator

The future semantic-memory design should remain derived from canonical SQLite records, not replace them.

## Result Evaluator

The Result Evaluator is not an AI agent.

It deterministically compares a measured `ExperimentResult` against frozen stub evaluation criteria and returns a structured decision such as `PROMOTE`, `ITERATE`, or `REJECT`.

The current V0.2 thresholds are intentionally simple infrastructure defaults:

- `minimum_trade_count = 4`
- `minimum_sharpe = 1.0`
- `minimum_net_pnl = 10.0`

These are stub research criteria, not validated trading thresholds.

The evaluator stores reason codes such as:

- `MINIMUM_TRADE_COUNT_MET`
- `MINIMUM_SHARPE_NOT_MET`
- `MINIMUM_NET_PNL_MET`
- `NET_PNL_BELOW_ZERO_HARD_FAIL`

The orchestrator still owns the legal transition, and the transition policy remains the authority on which stage changes are allowed.

The most recent evaluation is also shown in `show`, and all persisted evaluation decisions can be listed with `evaluations`.

## CLI

The CLI is intentionally small and deterministic.

Examples:

```bash
python3 -m ai_quant_scientist.cli init
python3 -m ai_quant_scientist.cli create --hypothesis "Extreme displacement from intraday fair value may predict short-term mean reversion." --signal-threshold 2.0 --lookback 20 --max-iterations 3
python3 -m ai_quant_scientist.cli step <RUN_ID>
python3 -m ai_quant_scientist.cli show <RUN_ID>
python3 -m ai_quant_scientist.cli audit <RUN_ID>
python3 -m ai_quant_scientist.cli evaluations <RUN_ID>
```

By default the database is created at `data/ai_quant_scientist.db`.
