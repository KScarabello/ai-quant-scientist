AI Quant Scientist — V0.8 (Revision Intent + Deterministic Planner)

Core architectural principle:

  AI decides experimental intent.
  Deterministic Revision Planner chooses exact parameter value.
  Governance validates the result.

Intent Architecture (V0.8)
- AI (Terra + Prompt V3) expresses RevisionIntent: parameter name, direction (INCREASE/DECREASE/PERTURB),
  and experiment type (MECHANISTIC_DIAGNOSTIC / PARAMETER_SENSITIVITY).
- The AI does NOT specify an exact target value.
- RevisionPlanner (revision_planner_v1) resolves intent to an exact value deterministically:
    smallest legal untested perturbation in the requested direction.
    same inputs always produce the same output.
    fails closed when no legal non-redundant candidate exists.
- RevisionPlanner makes zero LLM/API calls.
- Resulting change flows into the existing deterministic SpecRevisionProposal workflow.
- Human acceptance remains required.

Planner V1 policy
- Float parameters: requires a step field in constraints; rejects if absent (no arbitrary heuristics).
- Integer parameters: defaults to step=1 if no step is specified.
- Default constraint grid: signal_threshold step=0.5, lookback step=5.
- Candidate selection: nearest untested full-spec in the requested direction.
- Lineage checked at full-spec level (not just individual parameter value).

Provisional critic: gpt-5.6-terra (Terra + Prompt V3, hardened context, intent contract)
Terra is the current candidate model — not permanently selected.

Previous benchmark artifacts (Luna, Terra V1/V2/V3, Sol, Llama)
- Produced under old value-contract (AI chose exact numeric value).
- Constraint plumbing fix and intent architecture follow those runs.
- Those artifacts remain as historical evidence, not as final scientific comparisons.


Evaluation Integrity Note (post-Benchmark-V1)
- A context plumbing bug was discovered and fixed after Benchmark V1 was run.
- Live critic eval runners (OpenAI and Ollama) were passing raw fixture dicts to the model
  adapter instead of properly constructed CriticContext objects.
- As a result: revision constraints (allowed_revision_constraints) were omitted (null) in
  all live benchmark payloads; the top-level reason_codes convenience field was also empty.
- A canonical build_critic_context(case) builder was introduced to ensure all evaluation
  paths (deterministic suite, OpenAI runner, Ollama runner) construct identical contexts.
- Previous live benchmark artifacts (Luna, Terra V1, Sol, Terra V2/V3, Llama) were produced
  without revision constraints in the model input. They remain as historical evidence but
  should NOT be treated as final scientific comparisons pending a re-run with the fix.
- Structural / stop-discipline decisions (e.g., NO_USEFUL_REVISION on exhausted/contradictory
  cases) may remain informative even without explicit constraints, since those decisions were
  grounded in reason codes and lineage rather than parameter bounds.
- Fix is in: src/ai_quant_scientist/evals/critic_eval.py (build_critic_context),
  run_live_critic_eval.py, run_ollama_critic_eval.py, openai_research_critic.py,
  ollama_research_critic.py.
- Regression tests: tests/test_context_plumbing.py (17 tests).


Benchmark V1 — COMPLETE (2026-08-19)
- Eval set: `evals/critic_v1.json` — 15 cases, version v1 (frozen).
- Models evaluated: gpt-5.6-luna, gpt-5.6-terra, gpt-5.6-sol, llama3.1:8b (local Ollama).
- Provisional critic selection: gpt-5.6-terra.
- Local Llama 8B: operationally excellent (fast, zero cost) but scientifically insufficient for the critic role.
- Prompt v1 is scientifically unchanged. Prompt v2 / anti-fiddling work is NEXT.

Contract rules (post-Benchmark-V1 hardening)
- Decision must be exactly `PROPOSE_REVISION` or `NO_USEFUL_REVISION`.
- Confidence must be exactly `low`, `medium`, or `high`; arbitrary strings rejected.
- For PROPOSE_REVISION: parent_spec_id, exactly-one change, rationale, prediction, and confidence are all required.
- For NO_USEFUL_REVISION: changes must be absent; confidence is optional but if present must satisfy the vocabulary.
- Provenance is compact: response_id, model, status, timestamps, token usage, output_text — no encrypted blobs.

OpenAI adapter
- Default candidate model: `gpt-5.6-luna` (configurable via `AI_QUANT_CRITIC_MODEL`).
- Uses OpenAI Responses API + Pydantic strict structured outputs.
- Critic output is a proposal only → deterministic validator → `SpecRevisionProposal(status=PROPOSED)` → human acceptance required.

Ollama adapter
- Local only (`localhost:11434`), no API key.
- Default model: `llama3.1:8b` (configurable via `OLLAMA_CRITIC_MODEL`).

Live benchmark guard
- OpenAI: requires `--allow-live-api` flag.
- Ollama: no flag required (local; no cost).
- Both support `--max-cases` / `--case-id` for smoke tests.

Artifacts: `artifacts/evals/` — do not mutate the authoritative DB.
Environment: set `OPENAI_API_KEY` for OpenAI live runs.

Important: Luna/Terra/Sol are candidate models being evaluated. AI output remains a proposal. Acceptance remains deterministic/supervised.

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
