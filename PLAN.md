# Agent Stack Stabilization Plan

## UI Workbench Extension Plan

### Product Goal

Build a viable, functional UI around the current Python-first Tabuflow backend without reshaping the analysis engine. The frontend should make the existing orchestrator, tabular extraction posture, SQL helpers, and future skills-assisted workflows visible as one coherent workbench.

The first version should feel like a serious data operator surface:

- chat-facing agent on one side,
- SQL editor and result inspection in the center,
- source files, queryable targets, saved views, and skills context close enough to use,
- clear run state, errors, validation, and next-step affordances,
- no dependency on a complete end-to-end production workflow for the UI to be useful.

### Backend Scope

Keep backend changes minimal and additive:

- add a FastAPI app as a transport shell over current code,
- expose health, chat, SQL execution, target listing, target description, skills listing, and UI bootstrap endpoints,
- reuse `src.tools.sql.query` for read-only SQL execution and target navigation,
- reuse current orchestrator entrypoints for chat when environment credentials exist,
- fail chat requests clearly when model credentials are unavailable,
- serve the built frontend from FastAPI for a one-process preview path.

Do not rewrite the orchestrator, prep_csv stage, query stage, validation stage, tabular extraction, or SQL helper internals for this UI pass.

### Frontend Scope

Create a new TypeScript React web shell under `frontend/` with Vite. The root should gain small scripts to build and run the UI, plus a `start.sh` that starts FastAPI and Vite together for local development on non-default ports.

Primary UI surfaces:

- Runs dashboard: current run identity, status, stage timeline, and recent changes.
- SQL workbench: editable SQL, explain/run/save controls, validation panel, result table, chart placeholder, download affordance.
- Agent panel: chat history, generated SQL summary, backend status, suggested next steps, and command composer.
- Files and targets: source file queue, known SQLite database path, queryable target list, target metadata preview.
- Skills editor: searchable skill list, selected skill preview, editable draft area, and future agent-assist controls.
- SQL editor assist: prompt-to-SQL drawer/side panel, selected target chips, repair hints, and save-view affordance.

### Design Direction

Use the provided `artifacts/graphs/ui.png` as the functional baseline, but raise the visual standard:

- calm operator-grade density instead of marketing cards,
- premium split shell with persistent left rail, central command surface, and right agent rail,
- wide editorial command header using Cabinet Grotesk-like typography,
- precise spacing and stable dimensions for tables, editors, buttons, tabs, and side panels,
- real hover/press/focus states on every button and card,
- no generic AI-startup gradients, ornamental labels, or fake metrics.

### API Contract

Initial endpoints:

- `GET /api/health`: app status, model default, and whether LLM environment variables are present.
- `GET /api/bootstrap`: initial UI data, sample SQL, known database path, suggested questions, and stage cards.
- `POST /api/chat`: message only; returns assistant content and compact artifact. Returns explicit errors if LLM credentials are absent or model execution fails.
- `POST /api/sql/run`: read-only SQL execution against the prepared local SQLite database. Browser-supplied database paths are not accepted.
- `GET /api/sql/targets`: list queryable targets for the prepared local SQLite database.
- `GET /api/sql/targets/{name}`: describe one target.
- `GET /api/skills`: discover workspace skills metadata for the UI.
- `POST /api/skills/draft`: non-destructive skill draft echo endpoint for the editor until full persistence is designed.

All responses should be JSON-friendly Pydantic models. The frontend should treat errors as first-class states rather than console-only failures.

Default local ports:

- FastAPI: `localhost:8017` via `API_PORT`
- Vite UI: `localhost:5174` via `UI_PORT`

### Implementation Steps

1. Add the FastAPI app.
   - Create a small API package under `src/api/`.
   - Add request/response schemas near the API boundary.
   - Reuse SQL helpers and current config defaults.
   - Keep prepared local data server-side and expose only redacted source metadata to the browser.

2. Add the frontend project.
   - Create Vite React TypeScript files under `frontend/`.
   - Add `package.json`, `tsconfig`, `vite.config`, and app source.
   - Use CSS modules or a single disciplined CSS file instead of adding a large UI kit.
   - Use `lucide-react` icons and restrained CSS motion.

3. Build the app shell.
   - Left navigation for Runs, Files, Targets, SQL, Views, Skills.
   - Central SQL editor/results panel with toolbar controls.
   - Right agent panel with chat, validation, recent changes, and suggested next steps.
   - Skills and files drawers/sections that are functional, not placeholders.

4. Wire the API.
   - Fetch bootstrap data on load.
   - Run SQL from the editor and update table/results state.
   - Send chat messages and render backend replies or explicit API errors.
   - Load targets and selected target details.
   - Load skills and allow local draft editing.

5. Verify iteratively with Playwright.
   - Open the dev server.
   - Snapshot the app at desktop and mobile widths.
   - Click every primary nav item and toolbar button.
   - Run the sample SQL.
   - Send a simple chat message.
   - Open target and skills views.
   - Check console errors and horizontal overflow.

6. Polish until it is good.
   - Fix text overflow and cramped controls.
   - Improve empty/error/loading states.
   - Tighten colors, border contrast, table density, and responsive behavior.
   - Add final build/typecheck/backend smoke checks.

### Verification Targets

Backend:

- `uv run ruff check src/agents src/tools src/api`
- `uv run python -m py_compile src/api/*.py`
- `uv run python -c "from fastapi.testclient import TestClient; from src.api import app; c=TestClient(app); print(c.get('/api/health').json()['status']); print(c.post('/api/sql/run', json={'sql':'select 1 as ok'}).json()['status'])"`

Frontend:

- `pnpm --dir frontend build`
- Playwright desktop screenshot and interactions.
- Playwright mobile screenshot and interactions.
- Browser console check after all major interactions.

### Near-Term Non-Goals

- full persistence for edited skills,
- multi-user auth,
- complete file upload and extraction orchestration,
- production-grade charting,
- changing orchestrator stage internals,
- replacing LangGraph dev tooling.

### Follow-Up After This UI Pass

Once the shell is solid, the next valuable backend-facing work is:

- real upload-to-prep_csv workflow,
- saved run history,
- persisted skills editor with validation,
- prompt-to-SQL assist using the query stage,
- target-aware chart suggestions,
- generated transport types from FastAPI OpenAPI.

## Summary

The architecture is now a chat-facing orchestrator with explicit data stages:

`orchestrator -> prep_csv/prep_pdf -> query_stage -> save_view -> answer`

The orchestrator is the only assistant-speaking layer. For normal chat, it answers directly. For data work, it decides when to call stage tools. The fixed `data_workflow` graph remains available for deterministic prep_csv/query/save execution, but the public graph is the user-facing orchestrator agent.

Skill handling is orchestrator-owned in both paths. The public chat agent starts with a `skills` node that lists available skill descriptions, then may choose `search_skills` or `load_skills` tools. Tool-using chat turns pass through a simple `summarize` node before ending. The fixed `data_workflow` keeps the explicit `skill_context` node for deterministic worker context.

The near-term goal is to keep this shape small, typed, traceable, and easy to inspect. Do not reintroduce a separate router shell or old `*_agent` vocabulary for stage code.

## Runtime Shape

1. The user-facing `orchestrator` receives chat messages.
2. For ordinary messages such as "hello", it answers directly without tools.
3. For data work, it calls `prep_csv` or `prep_pdf` to prepare source files into a shared SQLite database and target metadata.
4. It calls `query_stage` with the prepared state to draft SQL, execute it, repair runtime errors, validate the result, and save a view.
5. `answer` returns the final user-facing content and compact artifact.

The fixed `data_workflow` graph runs the same stage sequence directly:

`skill_context -> prep_csv -> query_stage -> answer`

## Package Shape

Current stage packages:

- `src/agents/orchestrator/`: user-facing agent, data-workflow graph, stage tool wrappers, payload shaping, skill context, runtime helpers, shared state.
- `src/agents/prep_csv/`: ReAct-style prep_csv stage for inspect/profile/extract tabular data.
- `src/agents/prep_pdf/`: ReAct-style prep_pdf stage for inspect/extract PDF table data.
- `src/agents/query_stage/`: SQL drafting, execution, runtime repair, and query-stage state.
- `src/agents/validation_stage/`: deterministic and model-backed validation for query results.

LangGraph entrypoints in `langgraph.json`:

- `orchestrator`: user-facing chat agent with stage tools.
- `data_workflow`: fixed prep_csv/query/save/answer workflow.
- `prep_csv`: visible prep_csv ReAct graph.
- `prep_pdf`: visible prep_pdf ReAct graph.
- `query`: visible query-stage graph.

## State Model

`src/agents/orchestrator/state.py` is the shared state source.

Keep these public schemas:

- `OrchestratorInput`: chat `messages`, source files, validation retry budget.
- `OrchestratorOutput`: final content, result artifact, `stage_artifacts`.
- `OrchestratorState`: the full graph state, including LangGraph `messages`, prep_csv output bridge fields, prepared data, SQL/query fields, validation feedback, and runtime repair counters.

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

### Prep CSV Stage

Primary files:

- `src/agents/prep_csv/prep_csv.py`
- `src/agents/prep_csv/prompts.py`
- `src/agents/prep_csv/payloads.py`
- `src/agents/prep_csv/state.py`

Responsibilities:

- inspect/profile/extract supplied source files with tabular tools
- produce `PrepCsvOutput`
- expose extracted target metadata for query drafting
- receive orchestrator-owned worker context and skill refs

Prep CSV does not search skills on its own.

### Prep PDF Stage

Primary files:

- `src/agents/prep_pdf/prep_pdf.py`
- `src/agents/prep_pdf/prompts.py`
- `src/agents/prep_pdf/payloads.py`
- `src/agents/prep_pdf/state.py`

Responsibilities:

- inspect supplied PDF files with raw page text and optional rendered page-image artifacts
- extract visually detected PDF tables into the shared SQLite cache
- produce `PrepPdfOutput`
- expose extracted target metadata for query drafting
- receive orchestrator-owned worker context and skill refs

Prep PDF does not profile PDFs. PDF table extraction is table-aware and visual, so inspection stays raw/evidence-oriented.

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
- prep_csv stage
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
- `data_workflow` should still run the fixed prep_csv/query/save path

## Remaining Work

### 1. Add Focused Contract Tests

Add tests for:

- `build_worker_skill_payload` deterministic lexical behavior
- `build_result_artifact` and `build_result_message`
- prep_csv failure path
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
- no prompt-only "return JSON" workaround
- no broad text JSON parsing for structured responses

If a provider breaks structured tool output, fix the provider/model strategy instead of adding broad text JSON parsing.

### 4. Keep Skill Context Orchestrator-Owned

Policy:

- orchestrator owns skill search and worker context
- stages receive only the prepared worker context and skill refs they need
- semantic search may be used when embeddings are available
- deterministic lexical search should stay visible

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
