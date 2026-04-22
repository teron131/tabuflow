# Orchestrator Rework Plan

## Summary

This document describes the end-state architecture for the orchestrator and worker runtime.

The target system is a single-turn request lifecycle centered on the worker pipeline, not on the top-level chatbot shell.

The intended runtime shape is:

1. chat request enters the top-level orchestrator
2. the orchestrator injects skills-routing behavior through middleware/model context
3. the worker runtime enters `prep agent`
4. `prep agent` inspects, profiles, and extracts source files into the SQLite sandbox
5. `sql agent` performs NL-to-SQL planning and execution against the sandbox
6. `validate` decides whether the result actually fulfills the user task
7. `save(view)` persists the validated result as a reusable SQLite view
8. the worker packages the result for the orchestrator
9. the orchestrator reports the result back to the user

This plan intentionally treats two kinds of saving as separate concerns:

- runtime save: always persist the validated result as a SQLite view
- user export: optionally write files for the user in a future UI-driven flow

## Design Principles

- The worker lifecycle is the architectural backbone.
- The top-level orchestrator remains a thin user-facing shell.
- `skills_router` remains middleware/model behavior, not a required explicit graph node.
- `prep agent`, `sql agent`, `validate`, and `save(view)` are the primary runtime stages.
- Reusable SQL refs are first-class architecture, separate from one-run candidate SQL.
- The core design is single-turn first; multi-turn feedback loops are not part of this target architecture.

## End-State Architecture

### 1. Top-level orchestrator shell

The top-level orchestrator remains the user-facing runtime.

Its responsibilities are:
- receive the user request
- maintain the assistant-style interaction surface
- inject skills-routing behavior into the run
- choose and invoke the correct worker runtime
- surface progress updates at major lifecycle transitions
- relay the packaged worker result back to the user

The orchestrator should not own:
- file inspection logic
- sandbox preparation logic
- SQL planning or execution logic
- validation decision logic
- persistence details beyond reporting outcomes

The orchestrator is a shell around the worker lifecycle, not the owner of its internal reasoning.

### 2. Skills routing behavior

`skills_router` remains a middleware/model-level behavior attached to the orchestrator rather than a mandatory explicit top-level graph stage.

Its role is to:
- inspect the user request
- identify relevant workspace skills
- load detailed skill instructions only when needed
- provide routing context to the worker runtime

Its contract should be explicit even if its implementation stays in middleware/model behavior.

#### Skills routing contract

Input:
- user request
- available workspace skills
- optional conversation context needed for the current request

Output:
- matched skill names
- optional loaded skill payloads or refs
- routing hints for the worker runtime
- assistant-visible trace metadata when useful

This keeps skills behavior deterministic enough to reason about without forcing it into a literal graph node.

### 3. Worker runtime

The worker runtime is the main execution backbone for tabular analysis tasks.

Its lifecycle is:
- `prep agent`
- `sql agent`
- `validate`
- `save(view)`
- `package`

The worker runtime owns the task-specific execution lifecycle from source files to validated reusable result.

### 4. `prep agent`

`prep agent` is a first-class runtime stage with the stable role:

`inspect + profile + extract/load`

It owns:
- file inspection
- source profiling
- table/block segmentation as needed
- extraction/loading into the shared SQLite sandbox
- construction of extracted-target metadata for downstream SQL work
- sandbox bootstrapping for the rest of the run

It should not be treated as a thin helper or hidden loader.

#### Prep contract

Input:
- task
- source files
- optional skill/routing context

Output:
- `database_path`
- extracted target list
- inspection metadata
- profiling metadata
- source-to-target mapping trace
- prep status
- prep failure reason when applicable

Prep must either produce a usable shared sandbox for downstream work or fail clearly before SQL work begins.

### 5. `sql agent`

`sql agent` remains the focused NL-to-SQL worker.

It owns:
- target suggestion and target selection
- use of inspected schema/target context
- NL-to-SQL planning
- SQL execution against the sandbox
- SQL repair guidance after execution failures
- structured packaging of SQL-stage outputs

It should remain a real `NL -> SQL + execute` worker.
It should not be reduced to a dumb executor.
It should also not become the owner of the full lifecycle.

#### SQL contract

Input:
- task
- `database_path`
- extracted targets and schema context
- optional curated SQL refs
- prior validation feedback when retrying

Output:
- selected targets
- candidate SQL
- SQL execution status
- SQL result payload
- repair hints when relevant
- SQL-stage trace metadata

### 6. `validate`

`validate` is the task-fulfillment gate after SQL execution.

Its job is not merely to confirm that SQL ran.
Its job is to decide whether the user task was actually fulfilled.

It should answer:
- did the SQL result answer the user request?
- is another SQL attempt likely to help?
- should the run pass, retry, or block?

#### Validation contract

Input:
- task
- selected targets
- candidate SQL
- SQL result
- target context
- prior validation feedback if any

Output:
- `pass`, `retry`, or `block`
- structured validation payload with:
  - `valid`
  - `retryable`
  - `failure_type`
  - `summary`
  - `instructions`
  - `rationale`

Validation feedback should be structured input to the next SQL attempt, not just freeform text appended to prompts.

### 7. Retry loop

The worker runtime should preserve an explicit retry loop between validation and SQL work.

The retry path is:
- `sql agent -> validate -> sql agent`

This loop continues only while:
- validation marks the issue as retryable
- the retry policy allows another attempt

The retry loop exists to improve task fulfillment, not merely to recover from syntax errors.

### 8. `save(view)`

`save(view)` is a mandatory runtime stage after successful validation.

Its role is to persist the validated SQL as a reusable SQLite view.

This stage is always part of a successful core run.
It should not be hidden inside worker completion.

#### Runtime save contract

Input:
- validated candidate SQL
- target database path
- view naming/persistence context

Output:
- saved view name
- saved view metadata
- save status
- persistence failure reason when applicable

A successful validated run is not fully complete until the reusable SQLite view has been saved.

### 9. Separate user export save

File export is a separate save concept and is not part of the mandatory core runtime lifecycle.

Its role is to:
- write user-requested files derived from the validated/saved result
- support future UI-triggered download or export actions

Examples include:
- CSV export
- XLSX export
- report file generation
- other user-facing file artifacts

This export surface should remain downstream of validation and runtime view persistence.
It is future UI-controlled behavior, not a required worker-stage prerequisite.

## SQL Refs As A First-Class Component

The architecture includes a separate `sql queries` / SQL refs subsystem.

This subsystem is first-class and distinct from the transient SQL produced during one run.

### Role of SQL refs

SQL refs exist to:
- store reusable query assets
- preserve curated SQL patterns or named queries
- allow filesystem editing and review
- improve repeatability across tasks
- serve as optional planning aids for the SQL worker

SQL refs are not the same thing as:
- the current run's candidate SQL
- the saved runtime result view
- validation output

### SQL refs contract

Input sources may include:
- curated query files
- refs stored in a workspace area such as skills/refs
- metadata describing intended use or compatible targets

Output to the runtime may include:
- query templates
- named reference queries
- examples for similar tasks
- hints about stable target/view conventions

### Relationship between SQL refs and runtime execution

The SQL worker may read curated SQL refs as planning guidance.
However:
- runtime SQL is still generated or selected for the current task
- runtime SQL must still be validated
- runtime success still requires `save(view)`
- curated refs do not bypass validation or runtime execution contracts

SQL refs improve reuse and consistency without replacing the runtime lifecycle.

## Result Packaging

After `save(view)` completes, the worker should package a stable result for the orchestrator.

The packaged result should include:
- `status`
- `outcome`
- `completion_reason`
- `database_path`
- extracted targets
- selected targets
- candidate SQL
- SQL result payload
- saved view name and metadata
- validation feedback when relevant
- a human-readable result message
- a structured result artifact
- trace/progress metadata

The orchestrator should usually relay this packaged result rather than re-synthesizing the entire answer from scratch.

## Assistant Update Behavior

The orchestrator should surface assistant-style progress updates for major lifecycle transitions.

Typical updates should correspond to:
- matching or loading relevant skills when needed
- preparing the sandbox
- running SQL analysis
- validating the result
- saving the reusable view
- packaging and returning the result

These updates are presentation behavior owned by the orchestrator shell, but they should be driven by real worker lifecycle events.

## State And Interface Expectations

### Orchestrator-facing state

The top-level runtime should be able to observe or relay:
- active worker name
- active lifecycle phase
- matched skill names
- loaded skill refs or payload handles
- worker progress events
- final packaged result artifact

### Worker-facing state

The worker runtime should explicitly carry:
- task
- source files
- routing/skill context when relevant
- `database_path`
- extracted targets
- selected targets
- candidate SQL
- SQL result
- validation decision and feedback
- saved view metadata
- packaged result fields
- trace/progress information

## Boundaries With Current Code

The end-state architecture maps cleanly onto the current codebase boundaries:

- `src/agents/orchestrator/*`
  - top-level shell, assistant interaction, skills-routing behavior, worker invocation
- `src/agents/tabular_agent/*`
  - worker lifecycle, state, nodes, packaging
- `src/agents/sql_agent.py`
  - SQL planning/execution contract
- `src/tools/tabular/tools.py`
  - prep/inspection/profile/extraction capabilities
- `src/tools/sql/query.py`
  - query execution and runtime view persistence
- skills/refs-style workspace area
  - curated reusable SQL refs and query assets

## Test Plan

### Lifecycle tests

- request with files reaches `prep agent` before any SQL execution
- prep failure stops the run before SQL starts
- successful prep produces one shared SQLite sandbox for downstream stages
- SQL execution feeds validation with structured outputs
- validation retry loops back into SQL with structured feedback
- validation pass leads to `save(view)`
- validation block stops the lifecycle with a blocked result
- successful validated runs save a reusable view and package the result

### Contract tests

- skills routing produces stable matched-skill and loaded-skill outputs
- `prep agent` returns a stable prep contract or a clear prep failure
- `sql agent` returns stable structured outputs for success, blocked, and repairable cases
- `validate` returns deterministic `pass` / `retry` / `block` decisions from structured inputs
- `save(view)` returns visible persistence metadata in the final result
- SQL refs load as optional planning aids without bypassing runtime validation

### Orchestrator behavior tests

- orchestrator injects skills-routing behavior once per request
- orchestrator emits assistant-style lifecycle updates
- orchestrator relays the packaged worker result cleanly
- orchestrator does not absorb prep or SQL reasoning responsibilities

### Export behavior tests

These are separate from the mandatory core lifecycle.

- export actions only run after a validated and saved runtime result exists
- export actions do not replace `save(view)`
- export file generation remains user-controlled

## Out Of Scope For This Target Architecture

- multi-turn feedback loops as a first-class runtime requirement
- user steering during an in-flight worker run
- treating file export as required for core completion
- collapsing curated SQL refs into transient runtime SQL
- moving worker-stage internals into the top-level orchestrator shell

## Assumptions And Defaults

- The primary runtime backbone is `prep agent -> sql agent -> validate -> save(view)`.
- The orchestrator remains the outer user-facing shell.
- `skills_router` remains middleware/model behavior with explicit contracts.
- Reusable SQL refs are first-class architecture.
- Runtime view persistence is always part of a successful run.
- File export is a future user-controlled action downstream of the core lifecycle.
- Final responses should usually relay the worker-packaged result rather than centrally rewriting it.
