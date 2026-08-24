AI Quant Scientist

AI does the science.
Software measures reality.
Governance keeps the scientist honest.

## Project Purpose

`ai-quant-scientist` is a governed quantitative research system with a hard boundary between bounded AI intent and deterministic execution.

- AI components may propose scientific intent.
- Deterministic components validate structure, choose exact reproducibility-critical values, execute the approved experiment, and persist authoritative history.
- The repository does not run an autonomous open-ended research loop.

## Current Architecture

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
-> deterministic candidate feasibility gate
-> `READY_FOR_SPEC` or `BLOCKED_CAPABILITY`

Research Designer path after explicit `READY_FOR_SPEC` authorization:

`ResearchCandidate`
-> explicit candidate-feasibility authorization
-> `ResearchDesigner`
-> structured design decision
-> deterministic validator
-> authoritative `ResearchDesignIntent`
-> authoritative `ResearchPredictionPlan`
-> stop

Supervised end-to-end cycle:

`ResearchBrief`
-> caller-owned `ResearchScope`
-> `HypothesisScientist`
-> deterministic scope-fidelity validation
-> authoritative `HypothesisClaimSet`
-> `ResearchCandidate`
-> `GovernedResearchIntake`
-> explicit candidate-feasibility authorization
-> `ResearchDesigner` V3
-> authoritative `ResearchDesignIntent`
-> deterministic `ResearchPredictionPlan` from frozen claims
-> deterministic `SpecMaterializer` V2
-> `InitialExperimentPlan`
-> explicit human acceptance of the whole plan
-> deterministic ordered execution of `BASELINE` then `COMPARATOR`
-> deterministic parameter-sensitivity contrast result
-> deterministic scientific verdict

Deterministic contrast-plan path after `ResearchDesignIntent`:

`ResearchDesignIntent`
-> deterministic `SpecMaterializer` V2
-> `InitialExperimentPlan`
-> per-condition exact feasibility
-> `InitialExperimentPlanProposal`
-> explicit human acceptance of the whole plan
-> deterministic ordered execution of `BASELINE` then `COMPARATOR`
-> deterministic contrast result

Current semantic split:

- `ResearchSpec` remains the repository’s single-condition execution payload in the existing lifecycle and revision system.
- `InitialExperimentPlan` is the new scientific comparison artifact that precommits a complete parameter-sensitivity contrast.
- Planned comparison conditions are not revisions and do not use `parent_spec_id` or `SpecRevisionProposal` semantics.

## AI Authority Boundaries

### Hypothesis Scientist

The Hypothesis Scientist is implemented and bounded.

It may:
- originate exactly one falsifiable hypothesis
- explain scientific rationale
- declare explicit prerequisite data and broad tool requirements
- return `NO_HYPOTHESIS` when the brief is underspecified

It may not:
- declare feasibility or capability availability
- create a `ResearchSpec`
- create an `InitialExperimentPlan`
- start or run research

Prompt status:
- `v1` preserved as the original live-tested prompt
- `v2` preserved as the hardened requirement-contract prompt
- `v3` preserved as the historical candidate-feasibility boundary prompt
- `v4` preserved as the historical canonical-claim prompt
- `v5` current default prompt with caller-owned `ResearchScope` fidelity and authoritative structured claim semantics

Current adapter defaults:
- model: `gpt-5.6-terra`
- prompt version: `v5`

Current boundary:
- caller/application owns `ResearchScope`
- `ResearchScope` fixes the independent variable and material outcome set before the Scientist runs
- the Scientist owns expected direction and rationale for every in-scope outcome
- deterministic software rejects any broadened, narrowed, or mismatched authoritative claim set before candidate persistence

### Research Designer

The Research Designer is now implemented and bounded.

It may:
- propose one bounded `ResearchDesignIntent`
- translate one authoritative `HypothesisClaimSet` into one bounded design under V3
- return `NO_VALID_DESIGN` when the candidate cannot be responsibly expressed under the bounded ontology

It may not:
- choose exact parameter values
- choose baseline/comparator values
- choose condition count or order
- choose capability IDs
- declare exact feasibility
- declare post-execution verdicts
- execute research
- automatically call the `SpecMaterializer`

Current adapter defaults:
- model: `gpt-5.6-terra`
- prompt version: `v3`

Current boundary:
- runs only after an explicit `READY_FOR_SPEC` authorization
- receives the authoritative candidate-side `HypothesisClaimSet`
- receives a deterministic AI-safe design ontology snapshot with version and fingerprint
- persists every invocation
- leaves Research Designer V1 and V2 frozen for historical reproducibility
- stops at `ResearchDesignIntent`; deterministic software then constructs `ResearchPredictionPlan` from the frozen claim set plus complete design coverage

### Research Critic

The Research Critic is implemented and separately bounded.

It may:
- propose one bounded revision intent
- return `NO_USEFUL_REVISION`

It may not:
- choose exact numeric revision values
- override evaluator authority
- mutate accepted experiment plans
- turn planned comparison conditions into revisions

`RevisionPlanner` V1 still deterministically chooses the exact revision value.

## Deterministic Governance

Core invariants:

- SQLite is canonical scientific history.
- Capability matching is deterministic and fail-closed.
- `ResultEvaluator` owns `PROMOTE` / `ITERATE` / `REJECT`.
- `READY_FOR_SPEC` means broad prerequisites exist; it does not authorize execution.
- Canonical structured scientific claims, not free prose, are the authoritative downstream hypothesis semantics.
- Caller-owned `ResearchScope` is the authoritative upstream question boundary for the supervised path.
- `ResearchScope` coverage must equal `HypothesisClaimSet` coverage, which must equal `ResearchDesignIntent` coverage and `ResearchPredictionPlan` coverage.
- Research Designer must completely cover the authoritative claim set and may not invent, remove, or rewrite directions.
- Exact reproducibility-critical values come from deterministic policy, not AI-authored intent.
- Structured predictions are frozen before execution.
- Planned comparison conditions are precommitted before execution.
- Human acceptance authorizes the whole precommitted plan.
- The supervised cycle stops at plan preparation unless a separate explicit acceptance action executes the exact persisted proposal.
- The AI does not see experiment results before prediction commitment.
- Deterministic software alone computes `SUPPORTED` / `FALSIFIED` from the persisted prediction plan and measured contrast result.
- `SUPPORTED` / `FALSIFIED` applies only to the bounded precommitted contrast, not general market truth.
- Lifecycle promotion remains downstream and separate from condition sequencing.
- `falsification_condition` is retained as non-authoritative scientific prose and is not parsed into governance thresholds.
- No Critic, revision planner, or lifecycle promotion is automatically invoked after verdict computation.

## Current Implemented Milestones

- `V0.1-V0.2`: deterministic run backbone and result evaluation
- `V0.3-V0.4`: immutable spec lineage and hardening
- `V0.5-V0.8`: bounded Research Critic, context hardening, deterministic `RevisionPlanner` V1
- `V0.9-V0.11`: truthful capability registry, deterministic feasibility gate, durable governed intake
- `V0.12A`: bounded Hypothesis Scientist, eval harness, schema `v6` invocation persistence
- `V0.12B`: hardened requirement contract between the Hypothesis Scientist and the `CapabilityRegistry`
- `V0.12C`: deterministic requirement ontology projection, ontology provenance, and scientist eval repair
- `V0.12D`: candidate-feasibility / future spec-feasibility boundary and AI contract cleanup
- `V0.13A`: supervised `ResearchDesignIntent`, deterministic stub-only exact feasibility, durable materialization proposals, and explicit human acceptance
- `V0.13A.1`: deterministic contrast plan, precommitted baseline/comparator execution, append-only acceptance-time revalidation, restart-safe condition execution records, deterministic contrast result, and semantic closure for `PARAMETER_SENSITIVITY`
- `V0.13B`: bounded Research Designer V1, deterministic design ontology, prompt V1, governed READY_FOR_SPEC-only design service, append-only invocation persistence, and schema `v9`
- `V0.14`: first supervised end-to-end scientist cycle connecting brief -> hypothesis -> candidate feasibility -> design -> deterministic materialization -> explicit human acceptance -> deterministic execution -> contrast result
- `V0.15`: precommitted directional predictions, Research Designer V2 plus design ontology V2, deterministic scientific verdict persistence, and schema `v10`
- `V0.15.1`: canonical candidate-side scientific claims, Research Designer V3 complete-coverage validation, deterministic claim-to-prediction projection, and schema `v11`
- `V0.15.2`: caller-owned canonical `ResearchScope`, Hypothesis Scientist Prompt `v5`, deterministic scope-fidelity validation, and no schema bump

For the detailed operational handoff, see `docs/ai/CURRENT_STATE.md`.

## Hypothesis Scientist

Current candidate contract:

- caller-owned `ResearchScope` with contract version `research_scope_v1`
- canonical `ToolKind`
- exact new-authoritative tool matching
- no fuzzy matching
- AI does not know concrete capability IDs
- primitive canonical `required_fields`
- bounded `PriorCandidateSummary`
- deterministic AI-safe requirement ontology snapshot with version and fingerprint
- authoritative `HypothesisClaimSet` with bounded directional claim semantics
- historical legacy tool snapshots remain readable

Semantic boundary:

- `ResearchScope` owns the independent variable and material outcome set for the current supervised path.
- `DataRequirement` means prerequisite input data needed before deterministic execution or analysis.
- `ToolRequirement` means a broad deterministic tool class needed before deterministic design can proceed.
- New AI-authored candidates normally leave `required_parameters=None`.
- For the current directional experiment path, the authoritative `HypothesisClaimSet` must exactly cover `ResearchScope`, while prose remains non-authoritative narrative.
- Exact parameter grids, strategy rules, execution settings, and other frozen condition details belong after `READY_FOR_SPEC`.
- `hypothesis_claim_ontology_v1` remains unchanged; `ResearchScope` is a separate caller-side contract rather than a claim-ontology revision.

## Research Critic

The critic remains a separate bounded AI path. It is not the comparison engine for planned parameter sensitivity.

- Planned comparator conditions are not `ITERATE` outcomes.
- Planned comparator conditions are not `SpecRevisionProposal`s.
- `RevisionPlanner` V1 remains unchanged and only applies to the revision workflow.

## Capability / Feasibility System

Production capability truth remains intentionally sparse.

Current registered production capability:

- `stub_backtester_v1`
  - `data_kind`: `SYNTHETIC_PARAMETRIC`
  - `asset_class`: `SYNTHETIC`
  - `resolution`: `N/A`
  - `supported_parameters`: `signal_threshold`, `lookback`
  - `supported_tool_kinds`: `BACKTEST_EXECUTION`

There are now four relevant deterministic layers:

- candidate feasibility: broad data/tool-class availability
- scientific intent coverage: exact preservation of the authoritative claim set
- condition feasibility: exact stub payload support for each precommitted condition
- plan execution: deterministic ordered execution and comparison of all required conditions
- verdict evaluation: deterministic comparison of precommitted expected direction versus observed direction

There is now one supervised orchestration layer:

- `SupervisedResearchCycle.prepare(...)`: bounded AI generation and deterministic plan preparation, stopping at `AWAITING_HUMAN_ACCEPTANCE`
- `SupervisedResearchCycle.accept_and_execute(...)`: separate explicit human-governed acceptance plus deterministic execution of the exact previously prepared persisted plan and deterministic verdict evaluation

## Persistence

Current schema version: `v11`

SQLite persists authoritative history for:

- research runs, specs, revisions, attempts, results, evaluations, and critic invocations
- caller-authored `ResearchScope` inside the authoritative `research_brief_snapshot`
- research candidates and candidate-feasibility decisions
- hypothesis scientist invocations
- hypothesis claim sets
- research designer invocations
- research design intents
- research prediction plans
- historical single-spec materialization records from `V0.13A`
- `InitialExperimentPlan`
- ordered experiment conditions
- materialization-time exact condition-feasibility decisions
- acceptance-time fresh exact condition-feasibility decisions
- plan proposal status
- condition execution records
- deterministic parameter-sensitivity contrast results
- deterministic scientific verdicts

## Evals

Verified deterministic suite:

- command: `PYTHONPATH=src pytest -q`
- result: `569 passed`

Relevant scientist artifact note:

- the clean post-validator-fix `case-06` Prompt `v3` / ontology `v2` artifact exists at [scientist_eval_gpt-5.6-terra_1787366543.json](/Users/kimscarabello/Repos/trading/ai-quant-scientist/artifacts/evals/scientist_eval_gpt-5.6-terra_1787366543.json:7)

## CLI

Useful deterministic commands:

```bash
PYTHONPATH=src python3 -m ai_quant_scientist.cli capabilities
PYTHONPATH=src python3 -m ai_quant_scientist.cli feasibility-check --preset synthetic
PYTHONPATH=src python3 -m ai_quant_scientist.cli candidates
PYTHONPATH=src python3 -m ai_quant_scientist.cli feasibility-history <candidate_id>
PYTHONPATH=src python3 -m ai_quant_scientist.evals.run_live_supervised_cycle --model gpt-5.6-terra --allow-live-api
PYTHONPATH=src python3 -m ai_quant_scientist.evals.run_live_supervised_cycle --proposal-id <EXACT_PROPOSAL_ID> --accept-and-execute
PYTHONPATH=src pytest -q
```

There is intentionally no dedicated production CLI for the supervised cycle yet. The governed service APIs and guarded live diagnostic runner are the supported V0.15.2 interfaces.

Live supervised cycle workflow:

1. Run preparation once and note the printed `proposal_id`.
2. Inspect that exact persisted proposal, canonical claim set, plan, and precommitted prediction mapping.
3. Execute only with `--proposal-id <EXACT_PROPOSAL_ID> --accept-and-execute`.

Historical negative evidence:

- proposal `2f641366-3e40-4aa3-90df-4423ba0fff65` is `DO NOT ACCEPT`
- it was a real V0.15 / Research Designer V2 preparation artifact that narrowed a two-outcome hypothesis to `trade_count` only
- it was never accepted or executed
- it remains valuable governance evidence and must not receive a fabricated claim set or verdict
- proposal `2cea1a89-afa5-4ace-abca-3dbda86ded82` is also `DO NOT ACCEPT`
- it was a real V0.15.1 preparation artifact where Hypothesis Scientist V4 broadened the caller's question by adding canonical `net_pnl`
- it was never accepted or executed
- it remains valuable scope-integrity evidence and must not receive a fabricated `ResearchScope` or verdict

## Current Limitations / Future Work

- no autonomous loop
- no autonomous iterative chaining from verdicts into Critic, revision, replication, or holdout
- no generalized multi-capability exact materializer
- no generalized multi-condition experiment DSL
- no RAG or vector canonical memory
- no fake real-market capabilities
- plain `pytest` still requires `PYTHONPATH=src`

## Historical Integrity Note

Historical evidence remains in the repository, but it should not be mistaken for the current architecture.

- `V0.13A` proved governed baseline-spec materialization.
- `V0.13A.1` closes the semantic gap by precommitting and executing a complete deterministic parameter contrast.
- `V0.13B` adds the bounded AI layer that may author `ResearchDesignIntent` but still cannot control exact reproducibility-critical values.
- `V0.14` is complete and frozen historical evidence of the first supervised end-to-end run. Attempts on Saturday, August 22, 2026 correctly blocked twice before the approved proposal `7d4c04d5-9f36-49bc-ab15-8cd630f10999` reached human acceptance and deterministic execution, observing `trade_count` `4 -> 4` and `sharpe` `1.0 -> 0.75`.
- That historical `V0.14` execution does not receive a retrospective `V0.15` verdict because it did not persist a machine-readable precommitted prediction plan before execution.
- The first live `V0.15` preparation artifact, proposal `2f641366-3e40-4aa3-90df-4423ba0fff65`, is frozen rejected evidence. It reached the human boundary but must remain unaccepted because Research Designer V2 narrowed the candidate’s scientific intent instead of covering it completely.
- The first live `V0.15.1` preparation artifact, proposal `2cea1a89-afa5-4ace-abca-3dbda86ded82`, is frozen rejected evidence. It reached the human boundary but must remain unaccepted because Hypothesis Scientist V4 broadened the caller's scientific scope by adding `net_pnl`.
- Historical single-spec materialization records and pre-fix scientist artifacts remain readable for audit purposes.
