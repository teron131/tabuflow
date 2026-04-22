# Orchestrator Rework Plan, Revised

## Summary

This rework keeps the top-level orchestrator as the only user-facing AI surface and turns the tabular worker into a tool-style execution runtime with a strict internal lifecycle:

`prep -> sql -> validate -> save(view) -> package`

The main architectural shift is ownership of skills behavior. Today, skills context exists in both the orchestrator and the tabular worker. The target design removes worker-owned skill discovery and makes the orchestrator the single place where skills are searched, loaded, normalized, and passed down.

The second major shift is response ownership. Today, the worker produces both a structured artifact and a `result_message`, and the orchestrator tool wrapper forwards that worker-authored text. In the target design, the worker returns structured execution data only, and the orchestrator writes the final assistant response from that artifact.

The third major shift is completion semantics. A validated SQL result is not considered complete until `save(view)` succeeds. Save failure after validation is a failed run, not a soft warning.

## Current State And Why It Needs To Change

The current codebase already contains most of the target building blocks, but they are split across layers in a way that blurs ownership:

- `src/agents/orchestrator/middleware.py` already injects available-skill and matched-skill context into orchestrator model calls.
- `src/agents/orchestrator/tools.py` already exposes `run_tabular_workflow`, but it currently passes only `task` and `source_files` into the worker and forwards `output.result_message`.
- `src/agents/tabular_agent/graph.py` still starts with a worker-owned `skills` node before `prep`.
- `src/agents/tabular_agent/state.py` still treats `matched_skill_names` and `search_context` as worker-owned runtime state.
- `src/agents/tabular_agent/payloads.py` still produces both structured artifact output and worker-facing summary text.

That creates three problems:

1. Skills routing is duplicated across orchestrator and worker, which makes routing behavior harder to reason about and test.
2. The worker is not yet behaving like a clean tool runtime because it still emits presentation-layer text for the user.
3. The save step is important architecturally, but the current plan does not yet spell out enough detail around naming, failure handling, and output contracts to make implementation unambiguous.

## Target Runtime Shape

### End-to-end flow

1. A user request enters the orchestrator.
2. The orchestrator middleware determines relevant workspace skills for the latest request.
3. The orchestrator tool layer optionally loads matched skills and synthesizes a normalized worker instruction payload.
4. The orchestrator invokes the tabular worker with:
   - task
   - source files
   - matched skill names
   - synthesized worker instructions
   - skill refs / loaded references
   - validation retry budget
5. The worker executes:
   - `prep`
   - `sql`
   - `validate`
   - `save(view)`
   - `package`
6. The worker returns a structured artifact plus execution trace metadata.
7. The orchestrator turns that artifact into the final assistant response.

### Non-goals

- Multi-turn worker steering is out of scope.
- Export to CSV/JSON/XLSX is out of scope for this rework.
- Dedup of saved results is out of scope for v1.
- The SQL agent itself is not being replaced with a different planning architecture in this change.

## Architecture Changes By Area

### 1. Orchestrator

The orchestrator remains the outer shell. It owns:

- user-facing message flow
- skills discovery and optional loading
- worker selection and invocation
- translation of worker artifact into final assistant response
- user-visible lifecycle updates

The orchestrator must not own:

- file inspection or extraction
- SQLite sandbox construction
- SQL planning or SQL execution
- post-SQL validation logic
- SQLite view persistence internals

#### Orchestrator responsibilities in code

Primary files:

- `src/agents/orchestrator/middleware.py`
- `src/agents/orchestrator/tools.py`
- `src/agents/orchestrator/graph.py`
- `src/agents/orchestrator/orchestrator.py`
- `src/agents/orchestrator/state.py`

Planned changes:

- Keep middleware responsible for injecting deterministic skills context into the model.
- Expand the orchestrator tool layer so it can construct worker-ready skill payloads rather than just exposing raw worker entrypoints.
- Keep the orchestrator graph shape simple; do not introduce a first-class graph node just to mirror skills routing that already exists in middleware/tools.
- Extend orchestrator state only if needed for artifact handoff, active worker reporting, or progress visibility.

### 2. Skills Routing

Skills routing becomes an orchestrator-only concern.

The worker must not perform `search_skills` at runtime anymore. It should treat skill context as already resolved upstream.

#### Skills routing responsibilities

- Search the workspace skills catalog for the current request.
- Decide which matched skills should be loaded more deeply.
- Synthesize a deterministic instruction block for the worker.
- Pass through references that can help the worker or SQL planner, especially skill-scoped `references/` assets.

#### Target skills contract

Inputs:

- latest user request
- workspace `skills/` directory
- optional orchestrator prompt additions

Outputs:

- `matched_skill_names: list[str]`
- `worker_instructions: str`
- `skill_refs: list[dict]`

`skill_refs` should carry the minimum structured metadata needed downstream, for example:

- `skill_name`
- `path`
- `relative_path`
- `content`
- `kind` such as `reference`, `instructions_excerpt`, or `script_reference`

#### Instruction synthesis policy

The orchestrator should pass both:

- a synthesized, normalized instruction block
- refs to the underlying matched skill assets

This keeps the worker prompt deterministic while still preserving traceability back to the underlying skill sources.

### 3. Tabular Worker Runtime

The worker becomes a true execution engine for tabular analysis. Its lifecycle is fixed and explicit:

- `prep`
- `sql`
- `validate`
- `save`
- `package`

There is no worker `skills` stage in the target design.

#### Worker responsibilities

- accept orchestrator-provided task and skill context
- build or reuse one SQLite sandbox for the run
- produce extracted target metadata for downstream SQL work
- perform SQL planning and execution
- validate fulfillment of the user task
- persist successful validated SQL as a SQLite view
- package a structured artifact for the orchestrator

#### Worker non-responsibilities

- searching workspace skills
- composing the final assistant answer
- exporting files for the user
- managing long-term dedup or result history in v1

### 4. SQL Agent

The SQL agent remains focused on `NL -> SQL -> execute`.

It should continue to own:

- target suggestion
- inspected-target context use
- SQL planning
- SQL execution
- SQL error repair guidance

It should not become:

- the owner of save semantics
- the owner of high-level skill routing
- the narrator of final user-facing results

## Detailed Runtime Contracts

### Orchestrator -> worker input contract

`TabularTaskInput` should include at least:

- `task: str`
- `source_files: list[str]`
- `matched_skill_names: list[str]`
- `worker_instructions: str`
- `skill_refs: list[dict[str, Any]]`
- `max_validation_retries: int`
- `run_id: str`

Notes:

- `run_id` exists so `save(view)` can use `task-slug + run-id` naming without inventing names deep inside the save node.
- `max_validation_retries` is configurable, but defaults to `2`.
- `matched_skill_names` remains useful for trace output even when detailed instructions are synthesized separately.

### Worker internal state contract

`TabularTaskState` should explicitly carry:

- user/task input:
  - `task`
  - `source_files`
  - `matched_skill_names`
  - `worker_instructions`
  - `skill_refs`
  - `run_id`
  - `max_validation_retries`
- prep outputs:
  - `database_path`
  - `extraction_results`
  - `extracted_targets`
- SQL outputs:
  - `selected_targets`
  - `candidate_sql`
  - `sql_result`
  - `sql_agent_output`
- validation outputs:
  - `validation_feedback`
  - `validation_attempts`
- save outputs:
  - `saved_view_name`
  - `saved_view`
- terminal/shared fields:
  - `status`
  - `outcome`
  - `completion_reason`
  - `last_error`
  - `trace`
  - `result_artifact`

Fields that should be removed from worker ownership:

- `search_context`
- worker-authored `result_message`

### Worker -> orchestrator output contract

`TabularTaskOutput` should become structured-artifact-first and should not require any final-user text field.

Required fields:

- `status`
- `outcome`
- `completion_reason`
- `database_path`
- `extracted_targets`
- `selected_targets`
- `candidate_sql`
- `sql_result`
- `saved_view_name`
- `saved_view`
- `validation_feedback`
- `last_error`
- `trace`
- `result_artifact`

The output should be sufficient for the orchestrator to:

- explain success
- explain blocked or failed outcomes
- name the saved view
- summarize targets used and result size
- surface validation or save failure reasons

### Validation contract

`validate` remains LLM-backed, but the contract must be explicit and typed.

Expected validator output:

- decision class:
  - `pass`
  - `retry`
  - `block`
- structured payload:
  - `valid`
  - `retryable`
  - `failure_type`
  - `summary`
  - `instructions`
  - `rationale`

Validation should use:

- task
- selected targets
- candidate SQL
- SQL result
- compact target context
- prior validation feedback
- current attempt count

Validation feedback should be stored as structured state and passed back into the next SQL attempt when retrying.

### Retry policy contract

The worker must keep an explicit loop:

`sql -> validate -> sql`

Rules:

- retry only if validation marks the result retryable
- stop retrying once `validation_attempts >= max_validation_retries`
- default retry budget is `2`
- a blocked decision exits to packaging without calling save
- a retry-exhausted decision exits to packaging with failed outcome

### Save contract

`save(view)` is mandatory after validation success.

Inputs:

- validated candidate SQL
- `database_path`
- deterministic view name derived from task slug + run id

Outputs:

- `saved_view_name`
- `saved_view`
- `status`
- `database_path`
- failure reason if save fails

Rules:

- save is not optional on the success path
- no dedup in v1
- save failure after validation yields `outcome=failed`
- save should not silently downgrade into a warning

### View naming policy

V1 naming policy is:

- base readable task slug from normalized task text
- append run id
- final format: `task_slug__<run_id>` or another equivalent stable delimiter pattern

The exact delimiter can be chosen during implementation, but the policy must satisfy:

- valid SQLite identifier
- low collision risk
- human-readable enough for debugging
- unique across successful runs without result dedup

## SQL Refs

SQL refs remain first-class, but they are not the same as runtime-generated candidate SQL or saved views.

### Storage

Curated SQL refs should remain skill-scoped, for example:

- `skills/gcp-cost-pipeline/references/...`
- `skills/<skill-name>/references/...`

### Role

SQL refs may provide:

- named query patterns
- stable domain-specific view conventions
- examples of correct joins, metrics, or filters
- human-curated guidance for billing-specific analysis tasks

### Runtime relationship

SQL refs:

- may inform SQL planning
- do not bypass validation
- do not replace runtime candidate SQL
- do not replace `save(view)`

## File-Level Implementation Plan

### `src/agents/orchestrator/middleware.py`

- Keep deterministic skills overview injection.
- Keep relevant-skill match injection.
- Avoid moving worker logic into middleware.
- Ensure the middleware output remains deterministic enough for testing.

### `src/agents/orchestrator/tools.py`

- Add a helper layer that converts skill search/load results into worker-ready payloads.
- Update `run_tabular_workflow` to pass:
  - matched skill names
  - synthesized worker instructions
  - skill refs
  - optional retry budget
- Stop using worker-authored `result_message` as the returned content.
- Build orchestrator-facing summary text from the worker artifact instead.

### `src/agents/tabular_agent/state.py`

- Expand `TabularTaskInput` for orchestrator-provided skill context and retry budget.
- Remove worker-owned search fields.
- Remove `result_message` from worker output.
- Add `run_id` or equivalent save-naming input.

### `src/agents/tabular_agent/graph.py`

- Remove the `skills` node entirely.
- Start the graph at `prep`.
- Route validation retry decisions using configurable retry budget from state instead of fixed module constant only.

### `src/agents/tabular_agent/nodes.py`

- Delete `make_skills_node`.
- Update `make_sql_node` to use orchestrator-provided instructions and refs instead of worker-owned search context.
- Keep validation structured and explicit.
- Update `save_node` to derive unique view name from task slug + run id.
- Ensure save failure produces failed terminal state.
- Keep packaging focused on artifact output, not final-user prose.

### `src/agents/tabular_agent/payloads.py`

- Keep compact artifact helpers.
- Remove worker-authored summary text generation from the main worker output path.
- Expand artifact shape if needed so the orchestrator can generate clear final answers from it without rereading internal state.

### `src/agents/tabular_agent/tabular_agent.py`

- Stop building worker prompts from `list_skills`.
- Accept orchestrator-provided skill payloads through `build_graph_input`.
- Update step streaming helpers to reflect the new worker state shape.
- Keep CLI/test harness support working with sensible defaults.

### `src/agents/orchestrator/orchestrator.py`

- Keep orchestrator answer ownership.
- Ensure the final assistant response is derived from the returned artifact rather than worker-authored text.

## Packaging And Final Response Behavior

The worker artifact should contain enough information for the orchestrator to write clear final messages without reconstructing execution state from scratch.

Recommended artifact shape:

- request summary:
  - `task`
  - `source_files`
- runtime result:
  - `status`
  - `outcome`
  - `completion_reason`
- prep summary:
  - extracted target count
  - extracted target preview
  - `database_path`
- SQL summary:
  - selected targets
  - `candidate_sql`
  - result row count / summary
- validation summary:
  - compact feedback
  - attempt count
- save summary:
  - `saved_view_name`
  - save status
  - save failure message if any
- trace:
  - compact lifecycle events

The orchestrator should translate this artifact into final user-facing language such as:

- success with saved view name
- blocked outcome with reason
- failed save after successful validation

## Testing And Verification

### Unit and contract checks

- orchestrator skill helper returns deterministic `matched_skill_names`, `worker_instructions`, and `skill_refs`
- worker input model validates new fields correctly
- worker output model no longer requires `result_message`
- packaging output contains enough data for final response generation

### Graph behavior checks

- worker graph begins at `prep`
- worker graph no longer contains `skills`
- prep failure exits to `package`
- SQL success routes to `validate`
- validation `pass` routes to `save`
- validation `retry` routes back to `sql` until retry budget is exhausted
- validation `block` routes to `package`
- save always routes to `package`

### Save behavior checks

- validated success creates a unique view name from task slug + run id
- save failure marks the run failed
- save metadata is visible in the final artifact
- no dedup behavior is attempted in v1

### Orchestrator behavior checks

- orchestrator passes skill payloads into the worker exactly once per request
- orchestrator does not rely on worker-authored final text
- orchestrator final answer changes appropriately for fulfilled, blocked, and failed outcomes

### Repository verification commands

Primary validation commands for this repo:

- `uv run ruff check . --fix`
- `uv run ruff format .`
- `uv run pytest`
- `npm run build`

If some existing scratch scripts are not suitable for full automated verification, add focused tests for the reworked contracts rather than relying only on manual runs.

## Rollout Notes

- This should be implemented as a direct replacement of the current behavior, not a dual-path migration with feature flags.
- Existing worker consumers in this repo should be updated in the same change so the interface stays coherent.
- Keep backward-compatibility shims out unless a concrete caller requires them.

## Assumptions And Defaults

- `validate` remains model-based in v1, but its output is structured and typed.
- Skills routing remains middleware/tool behavior, not a required graph node.
- The orchestrator is the only assistant-speaking layer.
- The tabular worker is treated as a tool-style execution runtime.
- Dedup is intentionally deferred.
- Export is intentionally deferred.
- Primary implementation boundaries remain:
  - `src/agents/orchestrator/*`
  - `src/agents/tabular_agent/*`
  - `src/agents/sql_agent.py`
  - `src/tools/sql/query.py`
