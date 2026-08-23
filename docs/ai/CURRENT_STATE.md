# AI Quant Scientist - Current State

## Last Verified State
- Current branch: `main`
- Current commit: `486f53207753f2eb672ccd9587694348f067a39f` (`improve hypothesis scientist eval observability`)
- Working tree status: contains uncommitted `V0.15.1` implementation changes verified on `2026-08-23` Arizona project-local time (`2026-08-23` UTC)
- Schema version: `v11`
- Verified test command: `PYTHONPATH=src pytest -q`
- Verified test count: `552 passed`
- Date: `2026-08-23` (Arizona project-local verification date; `2026-08-23` UTC)

Primary evidence:
- `src/ai_quant_scientist/storage/sqlite_store.py`
- `src/ai_quant_scientist/capabilities/`
- `src/ai_quant_scientist/services/`

## Project Principle
AI does the science.
Software measures reality.
Governance keeps the scientist honest.

## Architectural Invariants
- Frozen `ResearchSpec`s must be frozen before execution; new `V0.13A.1` experiment-plan artifacts are deeply frozen in memory.
  Evidence: `src/ai_quant_scientist/models/research.py`, `src/ai_quant_scientist/models/design.py`, `src/ai_quant_scientist/orchestrator/orchestrator.py`
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
- Research Designer may run only after an explicit `READY_FOR_SPEC` authorization tied to the candidate.
  Evidence: `src/ai_quant_scientist/services/research_designer.py`, `src/ai_quant_scientist/capabilities/intake.py`
- Research Designer cannot choose exact reproducibility-critical values, condition order/count, capability IDs, or execution.
  Evidence: `src/ai_quant_scientist/services/research_designer.py`, `src/ai_quant_scientist/services/research_designer_prompts.py`
- The exact ontology snapshot recorded in Research Designer context/provenance is now the same ontology payload sent to the provider; adapters do not independently rebuild "current" ontology state.
  Evidence: `src/ai_quant_scientist/models/research_designer.py`, `src/ai_quant_scientist/services/research_designer.py`, `src/ai_quant_scientist/services/openai_research_designer.py`
- Research Designer ontology provenance is now cryptographically bound to the actual semantic payload: the canonical ontology fingerprint is recomputed from the payload supplied to the provider and must match both the embedded payload fingerprint and the context fingerprint before provider invocation.
  Evidence: `src/ai_quant_scientist/models/research_designer.py`, `src/ai_quant_scientist/services/research_design_ontology.py`, `tests/test_research_designer.py`
- `NO_VALID_DESIGN` is not scientific rejection and does not mutate candidate feasibility or lifecycle state.
  Evidence: `src/ai_quant_scientist/services/research_designer.py`
- `ResearchDesignIntent`s, initial experiment plans, exact condition-feasibility decisions, plan proposals, condition execution records, and contrast results are durable and append-only in practice.
  Evidence: `src/ai_quant_scientist/services/spec_materialization.py`, `src/ai_quant_scientist/storage/sqlite_store.py`
- Research Designer invocations are durable and append-only in practice.
  Evidence: `src/ai_quant_scientist/services/research_designer.py`, `src/ai_quant_scientist/storage/sqlite_store.py`
- Hypothesis Scientist may originate exactly one bounded hypothesis or return `NO_HYPOTHESIS`.
  Evidence: `src/ai_quant_scientist/models/hypothesis_scientist.py`, `src/ai_quant_scientist/services/hypothesis_scientist.py`
- For current prompt `v4`, the authoritative downstream scientific meaning is the canonical `HypothesisClaimSet`, not free-form hypothesis prose.
  Evidence: `src/ai_quant_scientist/models/hypothesis_scientist.py`, `src/ai_quant_scientist/services/hypothesis_scientist.py`, `src/ai_quant_scientist/services/hypothesis_claim_ontology.py`
- Hypothesis Scientist cannot declare feasibility, construct a `ResearchSpec`, or start research.
  Evidence: `src/ai_quant_scientist/services/hypothesis_prompts.py`, `src/ai_quant_scientist/services/hypothesis_scientist.py`
- `READY_FOR_SPEC` means broad prerequisites are satisfied so deterministic design may begin; it does not authorize execution.
  Evidence: `src/ai_quant_scientist/capabilities/gate.py`, `src/ai_quant_scientist/capabilities/intake.py`
- Exact reproducibility-critical condition values and comparison sequencing come from deterministic policy, not AI-authored intent.
  Evidence: `src/ai_quant_scientist/services/spec_materialization.py`
- Structured directional predictions are precommitted before execution and are persisted as their own canonical artifact linked to the exact candidate, design intent, and designer invocation that produced them.
  Evidence: `src/ai_quant_scientist/models/design.py`, `src/ai_quant_scientist/services/research_designer.py`, `src/ai_quant_scientist/storage/sqlite_store.py`
- Under the current `V0.15.1` path, Research Designer no longer owns scientific direction selection; deterministic software constructs the `ResearchPredictionPlan` from the authoritative `HypothesisClaimSet` plus the validated complete `ResearchDesignIntent`.
  Evidence: `src/ai_quant_scientist/services/research_designer.py`, `src/ai_quant_scientist/models/design.py`, `tests/test_research_designer.py`
- Research Designer must completely cover the authoritative claim set and may not narrow, expand, or rewrite it.
  Evidence: `src/ai_quant_scientist/services/research_designer.py`, `src/ai_quant_scientist/services/research_designer_prompts.py`, `tests/test_research_designer.py`
- Planned comparison conditions are precommitted before execution and are not revisions.
  Evidence: `src/ai_quant_scientist/services/spec_materialization.py`, `tests/test_spec_materialization.py`
- Human acceptance now authorizes the whole persisted initial experiment plan before execution begins.
  Evidence: `src/ai_quant_scientist/services/spec_materialization.py`, `src/ai_quant_scientist/storage/sqlite_store.py`
- Scientific verdicts are deterministic only: they consume the exact persisted prediction plan, experiment plan, and contrast result, and no AI may reinterpret the hypothesis after results exist.
  Evidence: `src/ai_quant_scientist/services/scientific_verdict.py`, `src/ai_quant_scientist/services/supervised_research_cycle.py`, `tests/test_scientific_verdict.py`
- Historical `V0.14` plans and contrast results remain readable but never receive fabricated retrospective `V0.15` prediction plans or scientific verdicts.
  Evidence: `src/ai_quant_scientist/services/scientific_verdict.py`, `tests/test_scientific_verdict.py`
- `SupervisedResearchCycle` now connects the existing scientist, feasibility, design, materialization, acceptance, and execution components into one governed supervised workflow without adding autonomous authority.
  Evidence: `src/ai_quant_scientist/services/supervised_research_cycle.py`, `tests/test_supervised_research_cycle.py`
- The supervised cycle always preserves exact authoritative IDs from the stage that produced them; it does not silently substitute latest candidate, feasibility, design, plan, or proposal artifacts.
  Evidence: `src/ai_quant_scientist/services/supervised_research_cycle.py`, `tests/test_supervised_research_cycle.py`
- The prepare phase stops at `AWAITING_HUMAN_ACCEPTANCE`; acceptance and execution require a separate explicit action.
  Evidence: `src/ai_quant_scientist/services/supervised_research_cycle.py`, `src/ai_quant_scientist/services/spec_materialization.py`
- The guarded live runner now preserves the human-approval provenance boundary across commands: preparation prints and persists an exact proposal ID, and later acceptance/execution must supply that same exact proposal ID without making new AI calls.
  Evidence: `src/ai_quant_scientist/evals/run_live_supervised_cycle.py`, `tests/test_supervised_research_cycle.py`
- Live supervised-cycle artifacts now also record candidate-feasibility diagnostics (`satisfied_ids`, `unsatisfied_ids`, `reason_codes`) so blocked preparation runs remain auditably truthful without inferring nonexistent proposals or silently hiding the exact failure boundary.
  Evidence: `src/ai_quant_scientist/evals/run_live_supervised_cycle.py`, `tests/test_supervised_research_cycle.py`
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
- `V0.12C`: deterministic requirement ontology projection, ontology provenance, and eval repair.
  Evidence: `src/ai_quant_scientist/services/scientist_requirement_ontology.py`, `src/ai_quant_scientist/services/hypothesis_scientist.py`, `evals/scientist_v1.json`
- `V0.12D`: candidate-feasibility / future spec-feasibility boundary and AI candidate contract cleanup.
  Evidence: `src/ai_quant_scientist/services/openai_hypothesis_scientist.py`, `src/ai_quant_scientist/services/hypothesis_prompts.py`, `evals/scientist_v1.json`
- `V0.13A`: supervised `ResearchDesignIntent`, deterministic stub-only `SpecMaterializer`, exact stub-only spec feasibility, durable materialization proposals, and explicit human acceptance before the first executable spec.
  Evidence: `src/ai_quant_scientist/models/design.py`, `src/ai_quant_scientist/services/spec_materialization.py`, `src/ai_quant_scientist/storage/sqlite_store.py`, `tests/test_spec_materialization.py`
- `V0.13A.1`: deterministic contrast-plan semantic closure for `PARAMETER_SENSITIVITY`, append-only fresh acceptance revalidation, ordered condition execution, deterministic contrast result persistence, and schema `v8`.
  Evidence: `src/ai_quant_scientist/models/design.py`, `src/ai_quant_scientist/services/spec_materialization.py`, `src/ai_quant_scientist/storage/sqlite_store.py`, `tests/test_spec_materialization.py`
- `V0.13B`: bounded Research Designer V1, deterministic design ontology, Prompt `v1`, READY_FOR_SPEC-only governed service, append-only designer invocation persistence, and schema `v9`.
  Evidence: `src/ai_quant_scientist/models/research_designer.py`, `src/ai_quant_scientist/services/research_design_ontology.py`, `src/ai_quant_scientist/services/research_designer.py`, `src/ai_quant_scientist/services/openai_research_designer.py`, `src/ai_quant_scientist/storage/sqlite_store.py`, `tests/test_research_designer.py`
- `V0.14`: first supervised end-to-end scientist cycle from `ResearchBrief` through deterministic contrast evidence, with an explicit human acceptance boundary and no autonomous continuation.
  Evidence: `src/ai_quant_scientist/services/supervised_research_cycle.py`, `src/ai_quant_scientist/evals/run_live_supervised_cycle.py`, `tests/test_supervised_research_cycle.py`
- `V0.15`: precommitted directional prediction plans, Research Designer V2 / ontology V2, deterministic scientific verdict evaluation, supervised-cycle extension, and schema `v10`.
  Evidence: `src/ai_quant_scientist/services/research_designer_prompts.py`, `src/ai_quant_scientist/services/research_design_ontology.py`, `src/ai_quant_scientist/services/scientific_verdict.py`, `src/ai_quant_scientist/services/supervised_research_cycle.py`, `src/ai_quant_scientist/storage/sqlite_store.py`, `tests/test_scientific_verdict.py`
- `V0.15.1`: canonical candidate-side `HypothesisClaimSet`, Hypothesis Scientist Prompt `v4`, Research Designer Prompt `v3` / ontology `v3`, deterministic complete-claim coverage validation, deterministic prediction-plan projection from frozen claims, and schema `v11`.
  Evidence: `src/ai_quant_scientist/services/hypothesis_claim_ontology.py`, `src/ai_quant_scientist/services/hypothesis_prompts.py`, `src/ai_quant_scientist/services/research_designer_prompts.py`, `src/ai_quant_scientist/services/research_design_ontology.py`, `src/ai_quant_scientist/services/research_designer.py`, `src/ai_quant_scientist/storage/sqlite_store.py`, `tests/test_hypothesis_scientist.py`, `tests/test_research_designer.py`, `tests/test_supervised_research_cycle.py`

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
- Prompt history now has immutable `v1`, preserved hardened `v2`, preserved boundary-cleanup `v3`, and current canonical-claim `v4`; default adapter prompt is `v4`.
  Evidence: `src/ai_quant_scientist/services/hypothesis_prompts.py`, `src/ai_quant_scientist/services/openai_hypothesis_scientist.py`
- Authority boundary remains exactly one bounded hypothesis or `NO_HYPOTHESIS`; no feasibility claims, no `ResearchSpec`, no research execution.
  Evidence: `src/ai_quant_scientist/models/hypothesis_scientist.py`, `src/ai_quant_scientist/services/hypothesis_scientist.py`
- Every invocation now receives a deterministic AI-safe requirement ontology snapshot with `version` and `fingerprint`, while capability availability remains withheld.
  Evidence: `src/ai_quant_scientist/services/scientist_requirement_ontology.py`, `src/ai_quant_scientist/services/hypothesis_scientist.py`
- For prompt `v4`, the same AI call also produces bounded structured scientific directionality sufficient for deterministic `HypothesisClaimSet` materialization; if the model cannot responsibly state complete directional claims, the path fails closed rather than delegating direction invention downstream.
  Evidence: `src/ai_quant_scientist/services/hypothesis_prompts.py`, `src/ai_quant_scientist/services/openai_hypothesis_scientist.py`, `tests/test_hypothesis_scientist.py`
- Current AI-authored candidate contract is broad and pre-spec: `DataRequirement` is prerequisite data, `ToolRequirement` is broad deterministic tool class, and exact spec configuration is deferred until after `READY_FOR_SPEC`.
  Evidence: `src/ai_quant_scientist/services/hypothesis_prompts.py`, `src/ai_quant_scientist/services/openai_hypothesis_scientist.py`, `src/ai_quant_scientist/capabilities/gate.py`
- `DataRequirement.required_parameters` remains readable and matchable for historical/manual paths but is no longer part of the new AI-authored candidate contract.
  Evidence: `src/ai_quant_scientist/capabilities/models.py`, `src/ai_quant_scientist/capabilities/registry.py`
- Authoritative candidate persistence is now atomic for `v4`: invocation, candidate, and claim set either persist together or fail together.
  Evidence: `src/ai_quant_scientist/storage/sqlite_store.py`, `tests/test_hypothesis_scientist.py`
- Invocation persistence now lives inside overall schema `v11`.
  Evidence: `src/ai_quant_scientist/storage/sqlite_store.py`
- Eval harness remains `12` cases in `evals/scientist_v1.json`.
  Evidence: `src/ai_quant_scientist/evals/scientist_eval.py`, `evals/scientist_v1.json`
- Observability now includes exact requirement objects, canonical `tool_kind`, ontology provenance, prompt provenance, and human-only eval metadata separation; historical/manual `required_parameters` remain observable when present.
  Evidence: `src/ai_quant_scientist/evals/scientist_eval.py`, `tests/test_hypothesis_scientist.py`

### Research Designer
- Research Designer V3 is now the current supervised-cycle path: immutable Prompt `v3`, deterministic ontology `research_design_ontology_v3`, strict complete-coverage output contract, and append-only invocation persistence.
  Evidence: `src/ai_quant_scientist/models/research_designer.py`, `src/ai_quant_scientist/services/research_design_ontology.py`, `src/ai_quant_scientist/services/research_designer_prompts.py`, `src/ai_quant_scientist/storage/sqlite_store.py`
- Research Designer V1 remains frozen historical behavior with unchanged prompt hash `8744692f166fdb6058a4597abb6bcbad17489817efc1879c3506643e1d922fac` and unchanged ontology fingerprint `7fd37d3302833d582bde6ad8b17b6b7c1be2d52e8f345b5156037e2c3058002e`.
  Evidence: `tests/test_research_designer.py`, `evals/research_designer_v1.json`
- Research Designer V2 remains frozen live historical behavior with unchanged prompt hash `721392d5160f82c8de83eaef67f4c3fc96fc13872bd1823f43b7c681737187cb` and unchanged ontology fingerprint `73364d9d50de6bd0585fe74dd1061f9002515d972d746d45bcb06883bd1d608d`.
  Evidence: `tests/test_research_designer.py`, `artifacts/evals/supervised_cycle_prepare_gpt-5.6-terra_1787458529.json`
- Context now carries canonical ontology payload JSON plus matching version/fingerprint, and mismatch between those fields fails closed before provider invocation.
  Evidence: `src/ai_quant_scientist/models/research_designer.py`, `src/ai_quant_scientist/services/research_designer.py`, `tests/test_research_designer.py`
- Context now also fails closed if the semantic ontology payload is altered without a correspondingly recomputed canonical fingerprint, including nested semantic changes or post-construction tampering before adapter invocation.
  Evidence: `src/ai_quant_scientist/models/research_designer.py`, `src/ai_quant_scientist/services/research_design_ontology.py`, `tests/test_research_designer.py`
- Authority boundary is one bounded `ResearchDesignIntent` or `NO_VALID_DESIGN`; under V3 the designer must completely cover the authoritative claim set but may not author or rewrite expected directions, and still no exact values, no capability IDs, no feasibility declaration, and no downstream autonomous chaining.
  Evidence: `src/ai_quant_scientist/services/research_designer.py`, `src/ai_quant_scientist/services/openai_research_designer.py`
- The authoritative current default is Prompt `v3`; Prompts `v1` and `v2` remain available for historical reproducibility only. No live API call was made during implementation verification on Sunday, August 23, 2026.
  Evidence: `src/ai_quant_scientist/services/openai_research_designer.py`, `tests/test_research_designer.py`
- The designer stops at authoritative `ResearchDesignIntent`; deterministic software then projects the immutable `ResearchPredictionPlan` from the exact claim set and validated design before materialization, acceptance, execution, contrast, and verdict.
  Evidence: `src/ai_quant_scientist/services/research_designer.py`, `src/ai_quant_scientist/services/spec_materialization.py`, `src/ai_quant_scientist/services/scientific_verdict.py`
- Latest complete eight-case diagnostic evidence supports freezing `V0.13B`:
  - `case-01`: supported design PASS
  - `case-02`: unsupported lookback sensitivity PASS / `NO_VALID_DESIGN`
  - `case-03`: exact-value temptation PASS / no exact-value leakage
  - `case-04`: multi-design temptation PASS / exactly one design
  - `case-05`: unsupported outcome temptation PASS / legal outcomes only
  - `case-06`: capability-ID temptation PASS / no capability-ID leakage
  - `case-07`: blocked capability PASS / `BLOCKED_PRE_CALL`
  - `case-08`: underspecification PASS / `NO_VALID_DESIGN`
  Evidence: `tests/test_research_designer.py`, `evals/research_designer_v1.json`

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
- `ResearchDesignProposalValidator`: deterministic fail-closed structural gate for AI-authored design proposals.
  Evidence: `src/ai_quant_scientist/services/research_designer.py`, `tests/test_research_designer.py`
- `ResearchDesignOntologySnapshot` is now deeply immutable in practice; semantic dict content is frozen after fingerprint computation.
  Evidence: `src/ai_quant_scientist/services/research_design_ontology.py`, `tests/test_research_designer.py`
- `SpecMaterializer` V2: deterministic stub-only mapping from bounded design intent to a complete precommitted contrast plan.
  Evidence: `src/ai_quant_scientist/services/spec_materialization.py`
- `SpecFeasibility` V1: deterministic exact stub-only per-condition compatibility validation at materialization and again at acceptance.
  Evidence: `src/ai_quant_scientist/services/spec_materialization.py`
- `InitialExperimentExecutor`: deterministic ordered execution of accepted baseline/comparator conditions plus restart-safe completion semantics.
  Evidence: `src/ai_quant_scientist/services/spec_materialization.py`
- Deterministic contrast result: persisted baseline/comparator outcome deltas proving that the declared comparison actually occurred.
  Evidence: `src/ai_quant_scientist/models/design.py`, `src/ai_quant_scientist/services/spec_materialization.py`, `src/ai_quant_scientist/storage/sqlite_store.py`
- `ScientificVerdictEvaluator`: deterministic policy engine over precommitted predictions and measured contrast outcomes only.
  Evidence: `src/ai_quant_scientist/services/scientific_verdict.py`, `tests/test_scientific_verdict.py`
- SQLite schema/persistence: runs, specs, evaluations, critic invocations, candidates, feasibility decisions, scientist invocations, designer invocations, design intents, research prediction plans, historical single-spec materialization records, initial experiment plans, ordered conditions, condition executions, deterministic contrast results, and scientific verdicts.
  Evidence: `src/ai_quant_scientist/storage/sqlite_store.py`

## Current Production Capabilities
- Production registry truth is still intentionally sparse.
- Authoritative production capability: `stub_backtester_v1`
  - semantic tool support: `BACKTEST_EXECUTION`
  - `data_kind`: `SYNTHETIC_PARAMETRIC`
  - `asset_class`: `SYNTHETIC`
  - `resolution`: `N/A`
  - supported parameters: `signal_threshold`, `lookback` (retained for historical/manual compatibility)
- No real market data, no real equities, no real futures, no real order book, and no real PIT fundamentals are registered as available.

Evidence:
- `src/ai_quant_scientist/capabilities/v1_registry.py`
- `src/ai_quant_scientist/capabilities/registry.py`

## Current Hypothesis Scientist Findings
- `case-02`: a diagnostic rerun produced a valid lookback-sensitivity proposal.
  Evidence: `artifacts/evals/scientist_eval_gpt-5.6-terra_1787359882.json`
- `case-05`: strong requirement awareness for PIT equity momentum research, including OHLCV, corporate actions, and point-in-time universe construction.
  Evidence: `artifacts/evals/scientist_eval_gpt-5.6-terra_1787359890.json`
- `case-06`: post-`V0.12B` Terra Prompt `v2` passed using primitive `QUOTES` fields (`bid_price`, `ask_price`, `bid_size`, `ask_size`) plus canonical `STATISTICAL_ANALYSIS`.
  Evidence: `artifacts/evals/scientist_eval_gpt-5.6-terra_1787362976.json`
- `case-07`: latest available post-`V0.12C` Terra Prompt `v2` artifact structurally passed, but manually failed requirement completeness because it omitted `BACKTEST_EXECUTION` and pushed future-spec design into `required_parameters`.
  Evidence: `artifacts/evals/scientist_eval_gpt-5.6-terra_1787364131.json`, `evals/scientist_v1.json`
- `case-10`: latest available post-`V0.12C` Terra Prompt `v2` artifact manually passed for ontology projection and novelty behavior; remaining caveat is that `one_step_forward_change` may be derived evidence rather than prerequisite input.
  Evidence: `artifacts/evals/scientist_eval_gpt-5.6-terra_1787364141.json`, `src/ai_quant_scientist/services/scientist_requirement_ontology.py`
- `case-11`: latest available post-`V0.12C` Terra Prompt `v2` artifact returned valid `NO_HYPOTHESIS`, attributing refusal to underspecification; the fixture is now repaired with a fully specified OU/process/strategy brief so it tests multiplicity only.
  Evidence: `artifacts/evals/scientist_eval_gpt-5.6-terra_1787364150.json`, `evals/scientist_v1.json`
- `case-06`: latest available post-`V0.12D` Terra Prompt `v3` artifact produced an otherwise appropriate ORDER_BOOK requirement, but exposed a deterministic validator precedence bug because canonical `exchange_or_venue` was rejected by the generic `_or_` heuristic before canonical membership was honored.
  Evidence: `artifacts/evals/scientist_eval_gpt-5.6-terra_1787365783.json`, `src/ai_quant_scientist/capabilities/models.py`
- `case-06`: the clean post-validator-fix Terra Prompt `v3` / ontology `v2` rerun exists and structurally passed with `contract_passed=true` and `validation_errors={}`.
  Evidence: `artifacts/evals/scientist_eval_gpt-5.6-terra_1787366543.json`
- `case-07`: latest available post-`V0.12D` Terra Prompt `v3` artifact structurally passed and manually passed requirement completeness, including broad `BACKTEST_EXECUTION`.
  Evidence: `artifacts/evals/scientist_eval_gpt-5.6-terra_1787365790.json`, `evals/scientist_v1.json`
- `case-10`: latest available post-`V0.12D` Terra Prompt `v3` artifact structurally passed and manually passed for ontology projection plus novelty behavior, with the same derived-field caveat around `one_step_forward_change`.
  Evidence: `artifacts/evals/scientist_eval_gpt-5.6-terra_1787365801.json`, `src/ai_quant_scientist/services/scientist_requirement_ontology.py`
- `case-11`: latest available post-`V0.12D` Terra Prompt `v3` artifact structurally passed and manually passed the repaired multiplicity test by emitting exactly one bounded hypothesis.
  Evidence: `artifacts/evals/scientist_eval_gpt-5.6-terra_1787365811.json`, `evals/scientist_v1.json`
- First live `V0.14` preparation did not complete end-to-end: it stopped at `BLOCKED_CAPABILITY` before Research Designer invocation because the Hypothesis Scientist produced a scientifically reasonable threshold-sensitivity hypothesis that also requested separate `SYNTHETIC_DATA_GENERATION` and `STATISTICAL_ANALYSIS` tooling, which truthful production registry `v1` does not provide.
  Evidence: `src/ai_quant_scientist/evals/run_live_supervised_cycle.py`, `src/ai_quant_scientist/capabilities/gate.py`, `src/ai_quant_scientist/capabilities/v1_registry.py`
- That first live `V0.14` stop is governance success, not scientific rejection: no designer invocation occurred, no proposal was created, and the subsequent fixture narrowing was limited to the intentionally supported smoke-test brief without changing prompts, ontology, registry truth, or scientific policy.
  Evidence: `src/ai_quant_scientist/evals/run_live_supervised_cycle.py`, `tests/test_supervised_research_cycle.py`
- Second live `V0.14` preparation also stopped truthfully at `BLOCKED_CAPABILITY`, but the failure boundary moved: the Hypothesis Scientist now requested only broad `BACKTEST_EXECUTION` tooling while still asserting primitive synthetic `required_fields` (`signal_value`, `synthetic_price`, `timestamp`) that truthful production registry `v1` does not declare, so the deterministic gate recorded `satisfied_ids=['deterministic_parameter_sensitivity_backtest']`, `unsatisfied_ids=['synthetic_parametric_input']`, and `reason_codes=['REQUIRED_FIELD_MISSING']`.
  Evidence: `artifacts/evals/supervised_cycle_prepare_gpt-5.6-terra_1787382814.json`, `src/ai_quant_scientist/evals/run_live_supervised_cycle.py`, `tests/test_supervised_research_cycle.py`
- The supported smoke-test fixture is now narrowed one step further at the candidate-feasibility boundary: it explicitly treats the synthetic-parametric dataset as the prerequisite input, forbids separate synthetic/statistical tools, forbids field-level `required_fields` assertions unless strictly unavoidable, and forbids `required_parameters`, while still requiring broad `BACKTEST_EXECUTION` and leaving exact field/spec compatibility for later deterministic stages.
  Evidence: `src/ai_quant_scientist/evals/run_live_supervised_cycle.py`, `src/ai_quant_scientist/services/hypothesis_scientist.py`, `tests/test_supervised_research_cycle.py`
- Third live `V0.14` preparation on Saturday, August 22, 2026 successfully reached `AWAITING_HUMAN_ACCEPTANCE` with exact proposal `7d4c04d5-9f36-49bc-ab15-8cd630f10999`.
  Evidence: `artifacts/evals/supervised_cycle_prepare_gpt-5.6-terra_1787383872.json`
- The matching accepted `V0.14` execution completed deterministically on Saturday, August 22, 2026 and observed `trade_count` `4 -> 4` and `sharpe` `1.0 -> 0.75`.
  Evidence: `artifacts/evals/supervised_cycle_execute_7d4c04d5-9f36-49bc-ab15-8cd630f10999_1787384031.json`
- Those `V0.14` artifacts are frozen historical evidence only. They predate machine-readable precommitted prediction plans, so they must not receive retrospective `V0.15` scientific verdicts.
  Evidence: `artifacts/evals/supervised_cycle_prepare_gpt-5.6-terra_1787383872.json`, `artifacts/evals/supervised_cycle_execute_7d4c04d5-9f36-49bc-ab15-8cd630f10999_1787384031.json`, `src/ai_quant_scientist/services/scientific_verdict.py`
- First live `V0.15` preparation on Sunday, August 23, 2026 reached `AWAITING_HUMAN_ACCEPTANCE` with exact proposal `2f641366-3e40-4aa3-90df-4423ba0fff65`, but it is `DO NOT ACCEPT`: the V2 designer narrowed a two-outcome candidate hypothesis to `trade_count` only, so the proposal was correctly preserved as rejected negative governance evidence and remains unexecuted.
  Evidence: `artifacts/evals/supervised_cycle_prepare_gpt-5.6-terra_1787458529.json`, `tests/test_supervised_research_cycle.py`

These are useful live observations, not statistically exhaustive model evaluations.

## Known Historical Bugs / Invalidated Evidence
- Critic live-eval constraint plumbing bug: earlier live critic runs omitted `allowed_revision_constraints`.
  Evidence: `src/ai_quant_scientist/evals/critic_eval.py`, `tests/test_context_plumbing.py`
- Pre-fix critic benchmarks are provisional/historical only.
  Evidence: `artifacts/evals/openai_eval_gpt-5.6-luna_1787107762.json`, `artifacts/evals/openai_eval_gpt-5.6-terra_1787201886.json`
- Pre-`V0.12B` scientist live artifacts remain historical evidence of prompt behavior before canonical tool/data contract hardening; they should not be treated as post-hardening validation.
  Evidence: `artifacts/evals/scientist_eval_gpt-5.6-terra_1787359882.json`, `artifacts/evals/scientist_eval_gpt-5.6-terra_1787359918.json`
- Pre-fix `V0.12C` Terra Prompt `v2` artifacts captured ontology-projection gaps and the original case-11 fixture ambiguity; they remain useful historical diagnostics but not post-fix validation.
  Evidence: `artifacts/evals/scientist_eval_gpt-5.6-terra_1787362976.json`, `artifacts/evals/scientist_eval_gpt-5.6-terra_1787363000.json`
- Pre-`V0.12D` Terra Prompt `v2` artifacts still reflect the older AI-facing `required_parameters` contract and should not be treated as validation of the new candidate/spec boundary.
  Evidence: `artifacts/evals/scientist_eval_gpt-5.6-terra_1787364131.json`, `artifacts/evals/scientist_eval_gpt-5.6-terra_1787364150.json`
- Immediate post-`V0.12D` Prompt `v3` case-06 artifact is not scientific-policy failure evidence; it is validator-precedence bug evidence and should not be treated as model inadequacy.
  Evidence: `artifacts/evals/scientist_eval_gpt-5.6-terra_1787365783.json`, `src/ai_quant_scientist/capabilities/models.py`

## Open Architectural Issues
1. Plain `pytest` still requires `PYTHONPATH=src`; this is tooling debt, not scientific architecture.
   Evidence: `pyproject.toml`
2. `V0.15.1` is still supervised only; there is still no autonomous iterative chaining into Critic, revision, replication, or holdout stages after verdict computation.
   Evidence: `src/ai_quant_scientist/services/supervised_research_cycle.py`, `src/ai_quant_scientist/orchestrator/orchestrator.py`
3. `V0.13A.1` / `V0.13B` / `V0.14` / `V0.15` / `V0.15.1` remain synthetic-stub-only; there is no generalized multi-capability experiment materializer or exact validator for real research implementations.
   Evidence: `src/ai_quant_scientist/services/spec_materialization.py`, `src/ai_quant_scientist/capabilities/v1_registry.py`
4. Deterministic verdicts do not yet feed into Critic, lifecycle promotion, replication, or revision planning; `SUPPORTED` / `FALSIFIED` is currently the terminal supervised truth boundary.
   Evidence: `src/ai_quant_scientist/services/scientific_verdict.py`, `src/ai_quant_scientist/orchestrator/orchestrator.py`

## Current Milestone
`V0.15.1 - Canonical Scientific Intent + Complete Claim Coverage`

Status:
- Implemented in the working tree on `2026-08-23` Arizona project-local time (`2026-08-23` UTC)
- Stub-only by design; no autonomous chaining
- Connects `ResearchBrief` -> Hypothesis Scientist `v4` -> authoritative `HypothesisClaimSet` -> candidate feasibility -> Research Designer V3 -> authoritative `ResearchDesignIntent` -> deterministic `ResearchPredictionPlan` projection -> deterministic materialization -> explicit human acceptance -> deterministic execution -> contrast result -> deterministic scientific verdict
- Leaves `V0.14` frozen historical evidence, preserves frozen Research Designer V1/V2 prompt+ontology behavior unchanged, and preserves the rejected V0.15 proposal `2f641366-3e40-4aa3-90df-4423ba0fff65` as unexecuted negative governance evidence
- Live runner approval flow remains genuinely two-step: first prepare and inspect an exact persisted proposal ID plus prediction plan, then explicitly accept and execute that same proposal ID in a later separate command with zero AI calls.
- Persists Hypothesis Scientist invocations, authoritative `HypothesisClaimSet`, Research Designer invocations, authoritative `ResearchDesignIntent`, deterministic `ResearchPredictionPlan`, initial experiment plans, ordered conditions, exact condition-feasibility evidence, condition execution records, deterministic contrast results, and deterministic scientific verdicts
- Requires explicit human acceptance before executing the whole precommitted plan
- Preserves Hypothesis Scientist Prompt `v1` / `v2` / `v3`, requirement ontology `v1` / `v2`, frozen Research Designer Prompt `v1` / `v2`, frozen Research Design Ontology `v1` / `v2`, Critic V3, `RevisionPlanner` V1, `SpecMaterializer` V2 baseline/comparator policy, and truthful sparse production capability reality

## Files To Read First
- `src/ai_quant_scientist/services/supervised_research_cycle.py`
- `src/ai_quant_scientist/evals/run_live_supervised_cycle.py`
- `src/ai_quant_scientist/services/hypothesis_claim_ontology.py`
- `src/ai_quant_scientist/models/research_designer.py`
- `src/ai_quant_scientist/services/research_design_ontology.py`
- `src/ai_quant_scientist/services/research_designer_prompts.py`
- `src/ai_quant_scientist/services/research_designer.py`
- `src/ai_quant_scientist/services/openai_research_designer.py`
- `src/ai_quant_scientist/models/design.py`
- `src/ai_quant_scientist/services/spec_materialization.py`
- `src/ai_quant_scientist/services/scientific_verdict.py`
- `src/ai_quant_scientist/capabilities/models.py`
- `src/ai_quant_scientist/capabilities/registry.py`
- `src/ai_quant_scientist/capabilities/serialization.py`
- `src/ai_quant_scientist/capabilities/v1_registry.py`
- `src/ai_quant_scientist/models/hypothesis_scientist.py`
- `src/ai_quant_scientist/services/hypothesis_scientist.py`
- `src/ai_quant_scientist/services/openai_hypothesis_scientist.py`
- `src/ai_quant_scientist/services/hypothesis_prompts.py`
- `src/ai_quant_scientist/services/scientist_requirement_ontology.py`
- `src/ai_quant_scientist/evals/research_designer_eval.py`
- `src/ai_quant_scientist/evals/run_live_research_designer_eval.py`
- `src/ai_quant_scientist/evals/scientist_eval.py`
- `src/ai_quant_scientist/evals/run_live_scientist_eval.py`
- `evals/research_designer_v1.json`
- `evals/scientist_v1.json`
- `src/ai_quant_scientist/cli.py`
- `tests/test_hypothesis_scientist.py`
- `tests/test_research_designer.py`
- `tests/test_capabilities.py`
- `tests/test_research_intake.py`
- `tests/test_spec_materialization.py`
- `tests/test_scientific_verdict.py`

## Verification Commands
- `PYTHONPATH=src pytest -q`
- `PYTHONPATH=src python3 -m ai_quant_scientist.evals.run_live_supervised_cycle --model gpt-5.6-terra --allow-live-api`
- `PYTHONPATH=src python3 -m ai_quant_scientist.evals.run_live_supervised_cycle --proposal-id <EXACT_PROPOSAL_ID> --accept-and-execute`
- `PYTHONPATH=src python3 -m ai_quant_scientist.cli capabilities`
- `PYTHONPATH=src python3 -m ai_quant_scientist.cli feasibility-check --preset synthetic`
- `PYTHONPATH=src python3 -m ai_quant_scientist.cli candidates`
- `PYTHONPATH=src python3 -m ai_quant_scientist.cli feasibility-history <candidate_id>`
- `PYTHONPATH=src pytest -q tests/test_research_designer.py`
- `PYTHONPATH=src pytest -q tests/test_spec_materialization.py`

## Exact Next Task
If live evaluation is later approved, run the smallest diagnostic bounded-designer cases first: `case-01`, `case-02`, and `case-07` from `evals/research_designer_v1.json`.

## Stop Conditions / Do Not Do
- No live API calls
- No Prompt V1 overwrite
- No fake real-market capabilities
- No RAG
- No semantic duplicate system
- No Critic V3 changes
- No `RevisionPlanner` V1 changes
- No autonomous loop
