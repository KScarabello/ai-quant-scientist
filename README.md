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
-> stop

Supervised end-to-end cycle:

`ResearchBrief`
-> `HypothesisScientist`
-> `ResearchCandidate`
-> `GovernedResearchIntake`
-> explicit candidate-feasibility authorization
-> `ResearchDesigner`
-> authoritative `ResearchDesignIntent`
-> deterministic `SpecMaterializer` V2
-> `InitialExperimentPlan`
-> explicit human acceptance of the whole plan
-> deterministic ordered execution of `BASELINE` then `COMPARATOR`
-> deterministic parameter-sensitivity contrast result

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
- `v3` current default candidate-feasibility boundary prompt

Current adapter defaults:
- model: `gpt-5.6-terra`
- prompt version: `v3`

### Research Designer

The Research Designer is now implemented and bounded.

It may:
- propose one bounded `ResearchDesignIntent`
- return `NO_VALID_DESIGN` when the candidate cannot be responsibly expressed under the bounded V1 ontology

It may not:
- choose exact parameter values
- choose baseline/comparator values
- choose condition count or order
- choose capability IDs
- declare exact feasibility
- execute research
- automatically call the `SpecMaterializer`

Current adapter defaults:
- model: `gpt-5.6-terra`
- prompt version: `v1`

Current boundary:
- runs only after an explicit `READY_FOR_SPEC` authorization
- receives a deterministic AI-safe design ontology snapshot with version and fingerprint
- persists every invocation
- stops at `ResearchDesignIntent`; only the separate supervised cycle may pass that exact intent into deterministic materialization

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
- Exact reproducibility-critical values come from deterministic policy, not AI-authored intent.
- Planned comparison conditions are precommitted before execution.
- Human acceptance authorizes the whole precommitted plan.
- The supervised cycle stops at plan preparation unless a separate explicit acceptance action executes the exact persisted proposal.
- Lifecycle promotion remains downstream and separate from condition sequencing.
- `falsification_condition` is retained as non-authoritative scientific prose and is not parsed into governance thresholds.

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

For the detailed operational handoff, see `docs/ai/CURRENT_STATE.md`.

## Hypothesis Scientist

Current candidate contract:

- canonical `ToolKind`
- exact new-authoritative tool matching
- no fuzzy matching
- AI does not know concrete capability IDs
- primitive canonical `required_fields`
- bounded `PriorCandidateSummary`
- deterministic AI-safe requirement ontology snapshot with version and fingerprint
- historical legacy tool snapshots remain readable

Semantic boundary:

- `DataRequirement` means prerequisite input data needed before deterministic execution or analysis.
- `ToolRequirement` means a broad deterministic tool class needed before deterministic design can proceed.
- New AI-authored candidates normally leave `required_parameters=None`.
- Exact parameter grids, strategy rules, execution settings, and other frozen condition details belong after `READY_FOR_SPEC`.

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

There are now three relevant deterministic layers:

- candidate feasibility: broad data/tool-class availability
- condition feasibility: exact stub payload support for each precommitted condition
- plan execution: deterministic ordered execution and comparison of all required conditions

There is now one supervised orchestration layer:

- `SupervisedResearchCycle.prepare(...)`: bounded AI generation and deterministic plan preparation, stopping at `AWAITING_HUMAN_ACCEPTANCE`
- `SupervisedResearchCycle.accept_and_execute(...)`: separate explicit human-governed acceptance plus deterministic execution of the exact previously prepared persisted plan

## Persistence

Current schema version: `v9`

SQLite persists authoritative history for:

- research runs, specs, revisions, attempts, results, evaluations, and critic invocations
- research candidates and candidate-feasibility decisions
- hypothesis scientist invocations
- research designer invocations
- research design intents
- historical single-spec materialization records from `V0.13A`
- `InitialExperimentPlan`
- ordered experiment conditions
- materialization-time exact condition-feasibility decisions
- acceptance-time fresh exact condition-feasibility decisions
- plan proposal status
- condition execution records
- deterministic parameter-sensitivity contrast results

## Evals

Verified deterministic suite:

- command: `PYTHONPATH=src pytest -q`
- result: `494 passed`

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

There is intentionally no dedicated production CLI for the supervised cycle yet. The governed service APIs and guarded live diagnostic runner are the supported V0.14 interfaces.

Live supervised cycle workflow:

1. Run preparation once and note the printed `proposal_id`.
2. Inspect that exact persisted proposal and plan.
3. Execute only with `--proposal-id <EXACT_PROPOSAL_ID> --accept-and-execute`.

## Current Limitations / Future Work

- no autonomous loop
- no autonomous iterative chaining from contrast results into Critic, revision, replication, or holdout
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
- Historical single-spec materialization records and pre-fix scientist artifacts remain readable for audit purposes.
