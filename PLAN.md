# Agent Stack Stabilization Plan

## Summary

The current implementation is a stacked-agent workflow for local tabular analysis:

`orchestrator -> prep_agent -> sql_agent -> validation_agent -> save(view) -> package`

The orchestrator is the only user-facing layer. It owns skill context, tool exposure, workflow tracing, and final response packaging. The worker agents do bounded execution work and return typed artifacts.

The near-term goal is not to invent a broader architecture. The goal is to keep this stack coherent, typed, traceable, and easy to debug end to end.

## Current Runtime Shape

1. A user request enters `Orchestrator`.
2. `SkillsContextMiddleware` injects available and relevant workspace-skill context into orchestrator model calls.
3. The orchestrator exposes tools through `make_orchestrator_tools`.
4. `execute_workflow` runs the direct prep-SQL-validation-save flow.
5. `PrepAgent` prepares source files into the shared SQLite database and target metadata.
6. `SQLAgent` suggests targets, inspects context, asks for a typed `SQLPlan`, executes SQL, and repairs once needed.
7. `ValidationAgent` deterministically rejects obviously invalid SQL results, otherwise asks for a typed `ValidationOutput`.
8. A validated SQL result is saved as a SQLite view.
9. The orchestrator packages a compact artifact and final user-facing response.

## Implemented Architecture

### Shared Agent Base

`src/agents/base.py` centralizes shared agent behavior:

- model resolution
- LLM construction
- graph artifact writing
- structured `create_agent` construction with `ToolStrategy(schema)`
- typed structured-response extraction

Structured outputs should stay schema-first. Do not add prompt-only JSON fallback paths.

### Orchestrator

Primary files:

- `src/agents/orchestrator/orchestrator.py`
- `src/agents/orchestrator/middleware.py`
- `src/agents/orchestrator/skill_context.py`
- `src/agents/orchestrator/workflow.py`
- `src/agents/orchestrator/tools.py`
- `src/agents/orchestrator/payloads.py`
- `src/agents/orchestrator/state.py`

Current responsibilities:

- `orchestrator.py`: builds the top-level `create_agent` runtime.
- `middleware.py`: injects deterministic skill overview and relevant skill matches.
- `skill_context.py`: lists, searches, loads, formats, and summarizes workspace skills.
- `workflow.py`: owns direct prep-SQL-validation-save execution and the root LangSmith trace.
- `tools.py`: exposes LangChain tools and converts workflow output into `Command` updates.
- `payloads.py`: builds compact artifacts and user-facing result messages.
- `state.py`: carries orchestrator-visible artifacts and active-agent state.

The key cleanup already done is separating orchestration responsibilities. `tools.py` should stay small and should not become the place where worker execution logic accumulates again.

### Prep Agent

Primary files:

- `src/agents/prep_agent/prep_agent.py`
- `src/agents/prep_agent/prompts.py`
- `src/agents/prep_agent/payloads.py`
- `src/agents/prep_agent/state.py`

Current responsibilities:

- use tabular tools to inspect/profile/extract files
- produce a normalized `PrepTaskOutput`
- collect extracted target metadata for SQL
- pass orchestrator-provided worker instructions and skill refs into prep requests
- accept runnable config so nested runs stay inside the parent LangSmith trace

Prep should not search skills on its own.

### SQL Agent

Primary files:

- `src/agents/sql_agent/sql_agent.py`
- `src/agents/sql_agent/nodes.py`
- `src/agents/sql_agent/payloads.py`
- `src/agents/sql_agent/prompts.py`
- `src/agents/sql_agent/state.py`

Current responsibilities:

- target suggestion
- target inspection
- typed SQL planning through `SQLPlan`
- SQL execution
- SQL error repair routing
- compact planner payload construction

Important rules:

- Keep `SQLAgent` as orchestration only: planner construction, graph construction, invoke wrapper.
- Keep graph nodes and routes in `nodes.py`.
- Keep planner message/payload shaping in `payloads.py`.
- Do not reintroduce raw JSON parsing fallback for planner output.

### Validation Agent

Primary files:

- `src/agents/validation_agent/validation.py`
- `src/agents/validation_agent/nodes.py`
- `src/agents/validation_agent/prompts.py`
- `src/agents/validation_agent/state.py`

Current responsibilities:

- deterministic failure checks for missing, failed, or empty SQL results
- typed validation through `ValidationOutput`
- concise retry guidance for the next SQL attempt

Validation remains model-backed, but the interface is typed. Do not make it prompt-only JSON.

### LangSmith Tracing

The intended trace shape is one root run per workflow:

`execute_workflow`

Nested beneath it:

- skill context calls
- `prep_agent`
- `sql_agent_attempt_N`
- `validation_agent_attempt_N`
- lower-level graph nodes and model calls

Implementation notes:

- `execute_workflow` is decorated with `@traceable`.
- Tool functions accept injected `RunnableConfig`.
- Nested graph and tool invocations pass or patch the parent config.
- `InjectedToolArg` keeps `config` out of model-visible tool schemas.

Do not remove config propagation unless another trace-parent mechanism replaces it.

## Runtime Contracts

### Workflow Input

`execute_workflow` accepts:

- `task: str`
- `source_files: list[str]`
- `max_prep_trials: int`
- `max_validation_retries: int`
- `prompt: str`
- `root_dir: str | Path | None`
- optional injected/cached agent instances
- optional `RunnableConfig`

### Workflow Output

`WorkflowExecutionResult` contains:

- `content: str`
- `artifact: dict[str, Any]`
- `agent_artifacts: dict[str, dict[str, Any]]`
- `active_agent: str | None`

The artifact should remain sufficient for:

- explaining success, blocked, or failed outcomes
- naming the saved view
- showing result size and target preview
- surfacing validation or save failure reasons
- debugging via compact trace events

### Save Semantics

Validation success is not terminal until save succeeds.

Rules:

- save validated SQL as a SQLite view
- derive the view name from task slug plus run id
- save failure after validation is a failed workflow
- do not silently downgrade save failures to warnings

## Remaining Work

### 1. Add Focused Contract Tests

Add tests for:

- `build_worker_skill_payload` with lexical skill fallback behavior
- `build_result_artifact` and `build_result_message`
- SQL graph blocked / planned / repair routes with fake planner outputs
- validation deterministic failures
- `execute_workflow` with mocked agents for success, prep failure, SQL failure, validation retry, and save failure paths

Avoid relying only on `test_agent.py` because it is an integration smoke script with live model/provider behavior.

### 2. Tighten Workflow Module Internals

`src/agents/orchestrator/workflow.py` is now the right owner for the lifecycle, but it can still be cleaned further.

Possible improvements:

- extract small result builders for prep failure, save failure, and validation failure if repeated logic grows
- keep retry state in `SqlLoopResult`
- keep final outcome decisions in one place
- avoid moving tool registration back into this module

### 3. Keep Structured Output Pure

Current policy:

- `create_agent` paths use `ToolStrategy(schema)`
- LangGraph paths invoke compiled graphs and validate outputs with Pydantic
- no prompt-only "return JSON" fallback
- no broad `cast()` plumbing for structured-response extraction

If a provider breaks structured tool output, fix the provider/model strategy rather than adding text JSON parsing in agent code.

### 4. Keep Skill Search Simple

The current skills search fallback is intentionally simple.

Policy:

- use semantic search when embeddings are available
- otherwise use lexical search without an env-var switch
- keep this fallback deterministic and visible in diagnostics

### 5. Review Data Correctness, Not Just Harness Success

For the GCP cost example, acceptable output may vary in row count and breakdown shape because the SQL planner can choose different useful summaries.

What matters:

- the prepared target is from the current source file
- SQL uses the current run target
- results contain meaningful totals and/or notable patterns
- validation follows the task instructions
- final artifact names the saved view
- LangSmith has one root trace for the run

## Verification Commands

Primary checks:

- `uv run ruff check .`
- `uv run ruff format --check .`
- `uv run python -m compileall -q src/agents`
- `uv run python test_agent.py`

If pytest is added later:

- `uv run pytest`

LangSmith sanity check:

- latest root run should be `execute_workflow`
- root count within that trace should be `1`
- trace should include prep, SQL, and validation child runs

## Non-Goals For Now

- export to CSV/JSON/XLSX
- deduplication of saved views
- multi-turn worker steering
- replacing the SQL planner architecture
- adding feature flags or dual old/new paths

## Design Guardrails

- The orchestrator is the only assistant-speaking layer.
- Worker agents are tool-style execution runtimes.
- Skills routing is orchestrator-owned.
- Save is mandatory after validation success.
- Structured output is Pydantic-first.
- LangSmith traces should stay unified under `execute_workflow`.
