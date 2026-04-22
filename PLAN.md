# Orchestrator Rework Plan

## Summary

Evolve the project toward the lifecycle shown in the draft by focusing first on the worker pipeline boundaries, not just the top-level orchestrator shell.

The intended runtime shape is:

1. chat request enters the top-level orchestrator
2. `skills_router` injects skills context and routes work
3. `prep agent` inspects, profiles, and extracts source files into the SQLite sandbox
4. `sql agent` performs NL-to-SQL planning and execution against the sandbox
5. `validate` checks whether the result actually fulfills the user’s task
6. `save` runs after validation as an explicit stage
7. the orchestrator reports the result back to the user, usually relaying the worker-packaged output

The immediate goal is to make that lifecycle explicit in the plan while only gradually promoting worker internals into the top-level orchestrator graph.

## Target Lifecycle

### Top-level responsibilities

- `orchestrator` remains the user-facing chatbot runtime.
- `skills_router` is the first explicit lifecycle stage owned by the top-level graph.
- The orchestrator is responsible for:
  - receiving the user request
  - deterministic skills context
  - choosing the worker path
  - surfacing assistant-style updates
  - relaying the final worker result back to the user

### Worker-stage responsibilities

#### `prep agent`

`prep agent` should own:
- file inspection
- source profiling
- extraction/loading into the shared SQLite sandbox
- construction of extracted target metadata for downstream SQL work

It should not stay as a mere loader. Its stable role is `inspect + profile + extract`.

#### `sql agent`

`sql agent` should own:
- target selection from available sandbox entities
- NL-to-SQL planning
- SQL execution
- SQL repair/retry guidance after execution failures

It should remain a real `NL -> SQL + execute` worker, not a dumb executor and not the full lifecycle owner.

#### `validate`

`validate` should mainly be a task-fulfillment gate.
It should answer:
- did the SQL result actually answer the user’s request?
- is another SQL attempt likely to help?
- should the run pass, retry, or block?

It should not be reduced to only SQL/result-shape sanity checking.

#### `save`

`save` should remain an explicit lifecycle stage.
For the current plan, treat it as always following successful validation rather than as a hidden worker-side detail.

## Implementation Plan

### Phase 1: Reframe the plan around the actual worker lifecycle

- Rewrite the plan so it is centered on `skills_router -> prep agent -> sql agent -> validate -> save`, not on generic orchestrator internals.
- Keep the orchestrator as the outer shell, but stop making it the main subject of every phase.
- Treat the current `tabular_agent` graph as the main source of truth for the first worker-lifecycle promotion steps.

### Phase 2: Stabilize stage contracts

Define explicit contracts for each lifecycle stage:

- `skills_router`
  - input: user task plus available workspace skills
  - output: relevant skill names, optional loaded skill references, routing context

- `prep agent`
  - input: task, files, skill context
  - output: database path, extracted targets, inspection/profile metadata, prep status

- `sql agent`
  - input: task, database path, extracted targets, prior validation feedback
  - output: selected targets, candidate SQL, SQL result, repair hints, execution status

- `validate`
  - input: task, selected targets, candidate SQL, SQL result, target context, prior feedback
  - output: pass / retry / block decision plus retry instructions

- `save`
  - input: validated SQL result and persistence target
  - output: saved view/file artifact and save status

These contracts should be decision-complete and stable before deeper refactors.

### Phase 3: Promote `prep agent` into a first-class concept

- Split the current prep behavior into a clearly named `prep agent` stage in the plan and later in code.
- Keep its responsibilities:
  - inspect files
  - profile schema/content
  - extract/load into SQLite
- Make prep outputs explicit and reusable by downstream stages.
- Ensure prep owns the sandbox bootstrapping responsibility rather than leaving it implicit in later steps.

### Phase 4: Keep `sql agent` as a focused worker

- Preserve `sql_agent` as the worker that does target suggestion, inspection, planning, execution, and repair hints.
- Do not prematurely move SQL reasoning into the orchestrator.
- Tighten its contract so the orchestrator and validation stage can consume its outputs without ad hoc parsing.

### Phase 5: Make validation a real fulfillment gate

- Keep `validate` after `sql agent`.
- Define validation as a task-level decision point, not just a data-quality check.
- Validation should decide:
  - accepted result
  - retry with instructions
  - blocked / failed result
- The retry loop should remain `validate -> sql agent` until the attempt policy is exhausted.

### Phase 6: Keep `save` explicit

- Model `save` as a separate stage after successful validation.
- In this plan, successful validated runs are assumed to proceed to save.
- Save should stay visible in the lifecycle and in the final result artifact, not folded away into “worker complete”.

### Phase 7: Make the top-level orchestrator reflect the worker lifecycle

- After the worker-stage boundaries are stable, adjust the orchestrator graph so it visibly routes into the worker lifecycle.
- The first explicit top-level graph should be closer to:
  - `chat_input`
  - `skills_router`
  - `worker_runtime`
  - `respond`
- `worker_runtime` may still wrap grouped internals at first, but the plan should clearly describe the inner lifecycle as `prep -> sql -> validate -> save`.

### Phase 8: Reporting behavior

- Use assistant-style intermediate updates from the orchestrator.
- Report major lifecycle transitions such as:
  - routing skills
  - preparing sandbox
  - running SQL analysis
  - validating result
  - saving output
- Final replies should usually relay the worker’s packaged result rather than heavily rewriting it.

## Important Interfaces And Contract Changes

### Orchestrator-facing state

The eventual top-level state should include:
- active phase
- candidate skill names
- loaded skill refs or payload handles
- prep outputs
- SQL outputs
- validation decision
- save outputs
- final packaged worker result
- assistant updates

### Worker result contract

Worker-facing results should always expose:
- `status`
- `outcome`
- `completion_reason`
- structured artifact payload
- human-readable packaged result message
- errors / blocked reason
- trace

### Validation contract

Validation output should always expose:
- `valid`
- `retryable`
- `failure_type`
- `summary`
- `instructions`
- `rationale`

## Test Plan

### Lifecycle tests

- request with files reaches `prep agent` before any SQL execution
- prep failure stops the run before SQL starts
- successful prep feeds sandbox metadata into `sql agent`
- SQL failure with repairable output loops back through validation/retry path
- validation pass leads to save
- validation retry leads back to SQL
- validation block stops the lifecycle with a blocked result
- successful validated run proceeds through save and returns a packaged result

### Contract tests

- `prep agent` always returns one shared SQLite database path or a clear prep failure
- `sql agent` returns stable structured outputs for success, blocked, and repairable failure
- `validate` produces deterministic pass/retry/block decisions from structured inputs
- `save` returns a visible saved artifact/status in the final result

### Top-level behavior tests

- orchestrator runs `skills_router` once per user turn
- orchestrator emits assistant-style lifecycle updates
- final user response mostly relays the worker-packaged result
- skill loading remains optional and model-invoked, not deterministic

## Assumptions And Defaults

- The plan should focus on `prep agent`, `sql agent`, `validate`, and `save` as the primary lifecycle backbone.
- `prep agent` owns `inspect + profile + extract`.
- `sql agent` owns `NL -> SQL + execute`.
- `validate` is a task-fulfillment gate.
- `save` is an explicit stage and is assumed to run after successful validation.
- The orchestrator remains the outer user-facing runtime, but it should stop dominating the plan narrative.
- Final responses should usually relay the worker’s packaged result rather than centrally rewriting it.
