# AI Quant Scientist - Current State

## Last Verified State
- Current branch: `main`
- Current commit: `486f53207753f2eb672ccd9587694348f067a39f` (`improve hypothesis scientist eval observability`)
- Working tree status: contains uncommitted `V0.12B` implementation changes verified on `2026-08-21` Arizona project-local time (`2026-08-22` UTC)
- Schema version: `v6`
- Verified test command: `PYTHONPATH=src pytest -q`
- Verified test count: `393 passed`
- Date: `2026-08-21` (Arizona project-local verification date; `2026-08-22` UTC)

Primary evidence:
- `src/ai_quant_scientist/storage/sqlite_store.py`
- `src/ai_quant_scientist/capabilities/`
- `src/ai_quant_scientist/services/`

## Project Principle
AI does the science.
Software measures reality.
Governance keeps the scientist honest.

## Architectural Invariants
- Frozen `ResearchSpec`s are immutable and must be frozen before execution.
  Evidence: `src/ai_quant_scientist/storage/sqlite_store.py`, `src/ai_quant_scientist/orchestrator/orchestrator.py`
- SQLite is canonical scientific history.
  Evidence: `src/ai_quant_scientist/storage/sqlite_store.py`
- Lifecycle transitions are governed by explicit policy.
  Evidence: `src/ai_quant_scientist/policies/transitions.py`
- Deterministic `ResultEvaluator` owns `PROMOTE` / `ITERATE` / `REJECT`.
  Evidence: `src/ai_quant_scientist/evaluation/result_evaluator.py`
- Research Critic cannot override evaluator decisions or autonomously advance lifecycle state.
  Evidence: `src/ai_quant_scientist/services/research_critic.py`, `src/ai_quant_scientist/orchestrator/orchestrator.py`
- Critic emits revision intent, not exact numeric targets.
  Evidence: `src/ai_quant_scientist/models/revision.py`, `src/ai_quant_scientist/services/openai_research_critic.py`
- `RevisionPlanner` V1 deterministically materializes exact values.
  Evidence: `src/ai_quant_scientist/services/revision_planner.py`
- AI cannot supply authoritative `parent_spec_id`.
  Evidence: `src/ai_quant_scientist/services/openai_research_critic.py`
- Capability registry is fail-closed.
  Evidence: `src/ai_quant_scientist/capabilities/registry.py`
- Explicit requirements are not inferred from hypothesis prose.
  Evidence: `src/ai_quant_scientist/services/hypothesis_scientist.py`, `src/ai_quant_scientist/capabilities/gate.py`
- `BLOCKED_CAPABILITY` does not mean scientific rejection.
  Evidence: `src/ai_quant_scientist/capabilities/gate.py`, `src/ai_quant_scientist/capabilities/intake.py`
- `ResearchCandidate`s and feasibility histories are durable and append-only in practice.
  Evidence: `src/ai_quant_scientist/capabilities/intake.py`, `src/ai_quant_scientist/storage/sqlite_store.py`
- Hypothesis Scientist may originate exactly one bounded hypothesis or return `NO_HYPOTHESIS`.
  Evidence: `src/ai_quant_scientist/models/hypothesis_scientist.py`, `src/ai_quant_scientist/services/hypothesis_scientist.py`
- Hypothesis Scientist cannot declare feasibility, construct a `ResearchSpec`, or start research.
  Evidence: `src/ai_quant_scientist/services/hypothesis_prompts.py`, `src/ai_quant_scientist/services/hypothesis_scientist.py`
- No autonomous loop exists today.
  Evidence: `README.md`, `src/ai_quant_scientist/orchestrator/orchestrator.py`
- No RAG or vector memory is canonical state.
  Evidence: `README.md`; no implementation path exists in `src/`
- No automatic model escalation exists.
  Evidence: explicit adapter defaults in `src/ai_quant_scientist/services/openai_research_critic.py` and `src/ai_quant_scientist/services/openai_hypothesis_scientist.py`

## Implemented Milestones
- `V0.1`: deterministic backbone and governed run lifecycle.
  Evidence: `src/ai_quant_scientist/orchestrator/orchestrator.py`, `src/ai_quant_scientist/storage/sqlite_store.py`
- `V0.2`: deterministic result evaluation.
  Evidence: `src/ai_quant_scientist/evaluation/result_evaluator.py`
- `V0.3-V0.4`: immutable spec revision lineage and hardening.
  Evidence: `src/ai_quant_scientist/models/research.py`, `tests/test_revision.py`, `tests/test_hardening.py`
- `V0.5-V0.8`: bounded Research Critic, corrected context plumbing, revision intent, deterministic `RevisionPlanner` V1.
  Evidence: `src/ai_quant_scientist/services/research_critic.py`, `src/ai_quant_scientist/evals/critic_eval.py`, `src/ai_quant_scientist/services/revision_planner.py`
- `V0.9`: truthful capability registry.
  Evidence: `src/ai_quant_scientist/capabilities/models.py`, `src/ai_quant_scientist/capabilities/registry.py`, `src/ai_quant_scientist/capabilities/v1_registry.py`
- `V0.10`: deterministic research feasibility gate.
  Evidence: `src/ai_quant_scientist/capabilities/gate.py`
- `V0.11`: durable governed intake and feasibility history persistence.
  Evidence: `src/ai_quant_scientist/capabilities/intake.py`, `tests/test_research_intake.py`
- `V0.12A`: bounded Hypothesis Scientist, eval harness, and schema `v6` invocation persistence.
  Evidence: `src/ai_quant_scientist/models/hypothesis_scientist.py`, `src/ai_quant_scientist/services/openai_hypothesis_scientist.py`, `src/ai_quant_scientist/evals/scientist_eval.py`
- `V0.12B`: requirement contract hardening between stochastic hypothesis generation and deterministic feasibility governance.
  Evidence: `src/ai_quant_scientist/capabilities/models.py`, `src/ai_quant_scientist/capabilities/serialization.py`, `src/ai_quant_scientist/services/hypothesis_prompts.py`

## Current AI Components

### Research Critic
- Current candidate: `gpt-5.6-terra` + Prompt V3.
  Evidence: `src/ai_quant_scientist/services/critic_prompts.py`
- Authority is bounded to proposing one revision intent or `NO_USEFUL_REVISION`; it does not accept, apply, or execute revisions.
  Evidence: `src/ai_quant_scientist/services/openai_research_critic.py`, `src/ai_quant_scientist/services/research_critic.py`
- Deterministic planner boundary is active: AI proposes `parameter` + `direction` + `experiment_type`; `RevisionPlanner` chooses the exact value.
  Evidence: `src/ai_quant_scientist/models/revision.py`, `src/ai_quant_scientist/services/revision_planner.py`
- Corrected repeatability findings remain recorded in repo artifacts.
  Evidence: `artifacts/evals/openai_eval_gpt-5.6-terra_repeats5_1787276724.json`, `artifacts/evals/openai_eval_gpt-5.6-terra_repeats5_1787276772.json`
- Historical live-eval `revision_constraints=null` artifacts are historical only.
  Evidence: `src/ai_quant_scientist/evals/critic_eval.py`, `tests/test_context_plumbing.py`

### Hypothesis Scientist
- Prompt history now has immutable `v1` plus hardened `v2`; default adapter prompt is `v2`.
  Evidence: `src/ai_quant_scientist/services/hypothesis_prompts.py`, `src/ai_quant_scientist/services/openai_hypothesis_scientist.py`
- Authority boundary remains exactly one bounded hypothesis or `NO_HYPOTHESIS`; no feasibility claims, no `ResearchSpec`, no research execution.
  Evidence: `src/ai_quant_scientist/models/hypothesis_scientist.py`, `src/ai_quant_scientist/services/hypothesis_scientist.py`
- Invocation persistence remains schema `v6`.
  Evidence: `src/ai_quant_scientist/storage/sqlite_store.py`
- Eval harness remains `12` cases in `evals/scientist_v1.json`.
  Evidence: `src/ai_quant_scientist/evals/scientist_eval.py`, `evals/scientist_v1.json`
- Observability now includes exact requirement objects, canonical `tool_kind`, `required_parameters`, prompt provenance, and human-only eval metadata separation.
  Evidence: `src/ai_quant_scientist/evals/scientist_eval.py`, `tests/test_hypothesis_scientist.py`

## Current Deterministic Components
- `ResultEvaluator`: recommendation engine over measured metrics.
  Evidence: `src/ai_quant_scientist/evaluation/result_evaluator.py`
- Revision lineage and proposals: immutable spec revisions and acceptance flow.
  Evidence: `src/ai_quant_scientist/models/research.py`, `src/ai_quant_scientist/storage/sqlite_store.py`
- `RevisionPlanner` V1: deterministic exact-value materialization.
  Evidence: `src/ai_quant_scientist/services/revision_planner.py`
- `CapabilityRegistry` V1: truthful, deterministic, fail-closed feasibility matching.
  Evidence: `src/ai_quant_scientist/capabilities/registry.py`, `tests/test_capabilities.py`
- `ResearchFeasibilityGate`: `READY_FOR_SPEC` vs `BLOCKED_CAPABILITY`.
  Evidence: `src/ai_quant_scientist/capabilities/gate.py`
- `GovernedResearchIntake`: durable candidate persistence plus append-only feasibility history.
  Evidence: `src/ai_quant_scientist/capabilities/intake.py`, `tests/test_research_intake.py`
- SQLite schema/persistence: runs, specs, evaluations, critic invocations, candidates, feasibility decisions, scientist invocations.
  Evidence: `src/ai_quant_scientist/storage/sqlite_store.py`

## Current Production Capabilities
- Production registry truth is still intentionally sparse.
- Authoritative production capability: `stub_backtester_v1`
  - semantic tool support: `BACKTEST_EXECUTION`
  - `data_kind`: `SYNTHETIC_PARAMETRIC`
  - `asset_class`: `SYNTHETIC`
  - `resolution`: `N/A`
  - supported parameters: `signal_threshold`, `lookback`
- No real market data, no real equities, no real futures, no real order book, and no real PIT fundamentals are registered as available.

Evidence:
- `src/ai_quant_scientist/capabilities/v1_registry.py`
- `src/ai_quant_scientist/capabilities/registry.py`

## Current Hypothesis Scientist Findings
- `case-02`: a diagnostic rerun produced a valid lookback-sensitivity proposal.
  Evidence: `artifacts/evals/scientist_eval_gpt-5.6-terra_1787359882.json`
- `case-05`: strong requirement awareness for PIT equity momentum research, including OHLCV, corporate actions, and point-in-time universe construction.
  Evidence: `artifacts/evals/scientist_eval_gpt-5.6-terra_1787359890.json`
- `case-06`: strong MES order-book hypothesis, but the earlier live artifact exposed pseudo-field requirement language that `V0.12B` now forbids.
  Evidence: `artifacts/evals/scientist_eval_gpt-5.6-terra_1787359897.json`, `src/ai_quant_scientist/capabilities/models.py`
- `case-07`: correctly declared both data and tool needs.
  Evidence: `artifacts/evals/scientist_eval_gpt-5.6-terra_1787359904.json`
- `case-10`: fingerprint alone is opaque to AI; bounded `PriorCandidateSummary` context is now the readable novelty aid while fingerprints remain authoritative identity.
  Evidence: `src/ai_quant_scientist/models/hypothesis_scientist.py`, `evals/scientist_v1.json`
- `case-11`: a diagnostic rerun successfully selected exactly one hypothesis.
  Evidence: `artifacts/evals/scientist_eval_gpt-5.6-terra_1787359918.json`

These are useful live observations, not statistically exhaustive model evaluations.

## Known Historical Bugs / Invalidated Evidence
- Critic live-eval constraint plumbing bug: earlier live critic runs omitted `allowed_revision_constraints`.
  Evidence: `src/ai_quant_scientist/evals/critic_eval.py`, `tests/test_context_plumbing.py`
- Pre-fix critic benchmarks are provisional/historical only.
  Evidence: `artifacts/evals/openai_eval_gpt-5.6-luna_1787107762.json`, `artifacts/evals/openai_eval_gpt-5.6-terra_1787201886.json`
- Pre-`V0.12B` scientist live artifacts remain historical evidence of prompt behavior before canonical tool/data contract hardening; they should not be treated as post-hardening validation.
  Evidence: `artifacts/evals/scientist_eval_gpt-5.6-terra_1787359882.json`, `artifacts/evals/scientist_eval_gpt-5.6-terra_1787359918.json`

## Open Architectural Issues
1. Plain `pytest` still requires `PYTHONPATH=src`; this is tooling debt, not scientific architecture.
   Evidence: `pyproject.toml`
2. `READY_FOR_SPEC` still stops before a production Spec Builder.
   Evidence: `src/ai_quant_scientist/capabilities/gate.py`, `README.md`
3. Post-hardening live scientist evals have not yet been rerun under prompt `v2`; committed live artifacts remain pre-hardening evidence.
   Evidence: `src/ai_quant_scientist/evals/run_live_scientist_eval.py`, `artifacts/evals/scientist_eval_gpt-5.6-terra_1787359882.json`

## Current Milestone
`V0.12B - Hypothesis Scientist Requirement Contract Hardening`

Status:
- Implemented in the working tree on `2026-08-21` Arizona project-local time (`2026-08-22` UTC)
- Not prompt-science tuning
- Hardens the language between stochastic scientific generation and deterministic feasibility governance

## Exact V0.12B Goals
- Canonical typed tool-kind vocabulary
- Exact deterministic registry matching
- AI does not know implementation capability IDs
- Primitive and canonical data-field semantics
- No pseudo-fields encoding `A or B` or transformation logic
- Expose `required_parameters` in Scientist structured output
- Bounded `PriorCandidateSummary` context for AI
- Fingerprints remain authoritative for exact identity
- `case-10` fixture corrected
- Production registry remains truthful
- Preserve original live-tested Prompt V1; if output-contract wording must change, create a new prompt version rather than overwriting `v1`
- No live API calls during implementation

## Files To Read First
- `src/ai_quant_scientist/capabilities/models.py`
- `src/ai_quant_scientist/capabilities/registry.py`
- `src/ai_quant_scientist/capabilities/serialization.py`
- `src/ai_quant_scientist/capabilities/v1_registry.py`
- `src/ai_quant_scientist/models/hypothesis_scientist.py`
- `src/ai_quant_scientist/services/hypothesis_scientist.py`
- `src/ai_quant_scientist/services/openai_hypothesis_scientist.py`
- `src/ai_quant_scientist/services/hypothesis_prompts.py`
- `src/ai_quant_scientist/evals/scientist_eval.py`
- `src/ai_quant_scientist/evals/run_live_scientist_eval.py`
- `evals/scientist_v1.json`
- `src/ai_quant_scientist/cli.py`
- `tests/test_hypothesis_scientist.py`
- `tests/test_capabilities.py`
- `tests/test_research_intake.py`

## Verification Commands
- `PYTHONPATH=src pytest -q`
- `PYTHONPATH=src python3 -m ai_quant_scientist.cli capabilities`
- `PYTHONPATH=src python3 -m ai_quant_scientist.cli feasibility-check --preset synthetic`
- `PYTHONPATH=src python3 -m ai_quant_scientist.cli candidates`
- `PYTHONPATH=src python3 -m ai_quant_scientist.cli feasibility-history <candidate_id>`

## Exact Next Task
Run a small post-hardening live scientist eval rerun under prompt `v2`, starting with `case-06`, `case-07`, `case-10`, and `case-11`, then compare those artifacts against the historical pre-hardening evidence.

## Stop Conditions / Do Not Do
- No live API calls
- No Prompt V1 overwrite
- No fake real-market capabilities
- No RAG
- No semantic duplicate system
- No Critic V3 changes
- No `RevisionPlanner` V1 changes
- No autonomous loop
