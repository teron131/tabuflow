# Agent Stack Stabilization Plan

## Summary

The architecture is now a chat-facing orchestrator with explicit data stages:

`orchestrator -> prep_stage -> query_stage -> save_view -> answer`

The orchestrator is the only assistant-speaking layer. For normal chat, it answers directly. For data work, it decides when to call stage tools. The fixed `data_workflow` graph remains available for deterministic prep/query/save execution, but the public graph is the user-facing orchestrator agent.

Skill context is orchestrator-owned in both paths: the public chat agent loads the same skill-context state update with middleware, while `data_workflow` loads it through the explicit `skill_context` node.

The near-term goal is to keep this shape small, typed, traceable, and easy to inspect. Do not reintroduce a separate router shell or old `*_agent` vocabulary for stage code.

## Runtime Shape

1. The user-facing `orchestrator` receives chat messages.
2. For ordinary messages such as "hello", it answers directly without tools.
3. For data work, it calls `prep_stage` to prepare source files into a shared SQLite database and target metadata.
4. It calls `query_stage` with the prepared state to draft SQL, execute it, repair runtime errors, validate the result, and save a view.
5. `answer` returns the final user-facing content and compact artifact.

The fixed `data_workflow` graph runs the same stage sequence directly:

`skill_context -> prep_stage -> query_stage -> answer`

## Package Shape

Current stage packages:

- `src/agents/orchestrator/`: user-facing agent, data-workflow graph, stage tool wrappers, payload shaping, skill context, runtime helpers, shared state.
- `src/agents/prep_stage/`: ReAct-style prep stage for inspect/profile/extract tabular data.
- `src/agents/query_stage/`: SQL drafting, execution, runtime repair, and query-stage state.
- `src/agents/validation_stage/`: deterministic and model-backed validation for query results.

LangGraph entrypoints in `langgraph.json`:

- `orchestrator`: user-facing chat agent with stage tools.
- `data_workflow`: fixed prep/query/save/answer workflow.
- `prep`: visible prep ReAct graph.
- `query`: visible query-stage graph.

## State Model

`src/agents/orchestrator/state.py` is the shared state source.

Keep these public schemas:

- `OrchestratorInput`: chat message, source files, validation retry budget.
- `OrchestratorOutput`: final content, result artifact, `stage_artifacts`.
- `OrchestratorState`: the full graph state, including LangGraph `messages`, prep output bridge fields, prepared data, SQL/query fields, validation feedback, and runtime repair counters.

Keep these reusable slices only because `QueryStageState` also uses them:

- `PreparedDataState`
- `SQLArtifactState`
- `SQLRuntimeState`

Avoid adding wrapper schemas that only group one or two orchestrator-only fields. The following have intentionally been removed:

- `TaskInput`
- `ChatInput`
- `MessageState`
- `ArtifactState`
- `ValidationRetryConfig`
- `StageBridgeState`
- serialized `sql_output`
- `agent_artifacts`
- `active_agent`
- `OrchestratorExecutionResult`

## Stage Contracts

### Prep Stage

Primary files:

- `src/agents/prep_stage/prep_stage.py`
- `src/agents/prep_stage/prompts.py`
- `src/agents/prep_stage/payloads.py`
- `src/agents/prep_stage/state.py`

Responsibilities:

- inspect/profile/extract supplied source files with tabular tools
- produce `PrepStageOutput`
- expose extracted target metadata for query drafting
- receive orchestrator-owned worker context and skill refs

Prep does not search skills on its own.

### Query Stage

Primary files:

- `src/agents/query_stage/nodes.py`
- `src/agents/query_stage/prompts.py`
- `src/agents/query_stage/state.py`

Responsibilities:

- draft SQL with `SQLDraft`
- write SQL artifacts to disk
- execute SQL against the prepared SQLite database
- repair SQLite/runtime failures with `SQLRuntimeRepair`
- keep SQL-specific names for SQL mechanics, while the package and graph node remain `query_stage`

The query stage does not own validation or final answering. The orchestrator query subgraph wires validation and save around it.

### Validation Stage

Primary files:

- `src/agents/validation_stage/validation_stage.py`
- `src/agents/validation_stage/nodes.py`
- `src/agents/validation_stage/prompts.py`
- `src/agents/validation_stage/state.py`

Responsibilities:

- reject missing, failed, or empty SQL results deterministically
- run typed `ValidationOutput` for semantic checks
- return retry guidance for the next query attempt

Validation success is not terminal. The result must still be saved.

### Save View

Save is mandatory after a completed query result.

Rules:

- save SQL as a SQLite view
- derive the view name from message slug plus run id
- report save failure as a failed run
- do not silently downgrade save failures to warnings

## Tracing

The root traced function is now:

`execute_data_workflow`

Expected children include:

- skill context
- prep stage
- query SQL draft/execute/repair nodes
- validation attempts
- save view

Keep config propagation through nested graph/model calls so traces stay connected.

## Verification

Primary checks:

- `uv run ruff check src/agents test_sql_files.py test_agent.py`
- `uv run pytest test_sql_files.py`
- `LLM_API_KEY=test LLM_BASE_URL=http://localhost:1 uv run langgraph validate`

Smoke checks:

- user-facing orchestrator with `hello` should answer directly and produce no tool messages
- `data_workflow` should still run the fixed prep/query/save path

## Remaining Work

### 1. Add Focused Contract Tests

Add tests for:

- `build_worker_skill_payload` lexical fallback behavior
- `build_result_artifact` and `build_result_message`
- prep failure path
- query runtime repair path
- validation retry and validation blocked paths
- save failure path
- direct-chat orchestrator path with no stage tool calls

Avoid relying only on `test_agent.py`; it is a live smoke script.

### 2. Tighten Query/Validation Naming In Artifacts

Review user-facing artifact keys and trace strings for old SQL-stage wording. SQL-specific fields such as `sql_path`, `candidate_sql`, and `sql_result` should stay; stage labels should say `query_stage`.

### 3. Keep Structured Output Pure

Current policy:

- `create_agent` paths use `ToolStrategy(schema)`
- graph outputs validate with Pydantic
- no prompt-only "return JSON" fallback
- no broad text JSON parsing for structured responses

If a provider breaks structured tool output, fix the provider/model strategy instead of adding fallback parsing.

### 4. Keep Skill Context Orchestrator-Owned

Policy:

- orchestrator owns skill search and worker context
- stages receive only the prepared worker context and skill refs they need
- semantic search may be used when embeddings are available
- lexical fallback should stay deterministic and visible

## Non-Goals For Now

- export to CSV/JSON/XLSX
- deduplication of saved views
- multi-turn worker steering beyond passing prepared state to `query_stage`
- replacing the SQL drafting/repair architecture
- adding compatibility aliases for old `*_agent` package names
- adding feature flags or dual old/new paths

## Design Guardrails

- The orchestrator is the only assistant-speaking layer.
- Stages are callable execution units, not separate user-facing assistants.
- `query_stage` is the workflow stage; SQL names are for SQL mechanics inside it.
- Skills routing is orchestrator-owned.
- Save is mandatory after validation success.
- Structured output is Pydantic-first.
- Keep state schemas few and real; inline one-off fields on `OrchestratorState`.
