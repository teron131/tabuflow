# data-agentics Roadmap

This is a working roadmap, not a fixed release plan. It is now narrowed around one practical local-first product loop: upload CSV/XLSX/PDF-table data, inspect the schema/file state, ask a natural-language question, generate and run SQL, inspect the result, and export the useful output.

The core product should stay generalizable: a native general agent grounded in a data workbench environment for turning messy files and user intent into inspectable, reusable data artifacts.

The product is not a data-only bot with general chat bolted on. Its default posture is a general agent that can answer directly when no tools are needed, and can use workbench tools, predefined internal routes, skills, references, scripts, artifacts, and runs when the task benefits from operational grounding.

The durable workbench loop remains:

`sources -> inspect -> extract -> transform/query -> validate -> package -> rerun`

Engine choices, language choices, and provider-specific workflows matter only when they help that loop become clearer, more reliable, or easier to operate.

## Current Implementation Snapshot

The repo already has a meaningful workbench skeleton rather than only a plan:

- [DONE] Python backend under `src/api/` exposes health, bootstrap, upload, chat, streaming chat, SQL run/targets/download, file explanation, and skill save/resource save routes.
- [DONE] The user-facing orchestrator remains Python-first and LangGraph-shaped, with prep_csv, prep_pdf, query, validation, skill, SQL, tabular, PDF, filesystem, and artifact tools behind it.
- [DONE] The tool surface already has substantial IO logic under `src/tools/`, including SQL helpers, tabular handling, PDF handling, filesystem tools, and artifact-related behavior.
- [DONE] The frontend is a Next workbench under `frontend/`, with an activity shell, explorer, source viewer, SQL/results surface, skill resource viewer, and AI SDK chat panel.
- [DONE] The currently strongest implemented loop is: bootstrap workspace -> inspect sources/targets -> run SQL -> view/download results -> use chat or skills as contextual assistance.

Near-term work should make those pieces behave like one coherent data-exploration workbench instead of separate implemented islands. Deployment, auth, broad connectors, and runtime migration are explicitly not the next drivers.

Current biggest next step: make CSV/XLSX upload and PDF-table extraction speak one shared `source -> target -> schema/profile` contract that the API, orchestrator, and frontend can all inspect. This comes before adding more source types, broader exports, or runtime migration.

## Component Map

The roadmap should be easy to read as a set of product components with clear ownership boundaries.

1. **Workspace product contract:** stable nouns, lineage, artifact model, and browser/model-safe payloads.
2. **Agent engine and chat lifecycle:** direct answers, intent routing, ReAct-style tool use, streaming, approvals, and response synthesis.
3. **Harness-agnostic tool and IO layer:** reusable source, SQL, filesystem, artifact, export, and skill operations with typed inputs and outputs.
4. **Skills as operational source of truth:** `SKILL.md`, references, scripts, examples, and controlled edits as durable workflow memory.
5. **Observability and run evidence:** events, tool-call logs, observations, warnings, validation summaries, run notes, and user-visible state.
6. **Workbench UI and human controls:** visible controls for agent actions, state inspection, previews, diffs, and artifact review.
7. **Expansion and repeatability:** more source/artifact types, stronger validation, reusable workflows, and later engine/runtime decisions.

## 1. Workspace Product Contract

Goal: define the stable product nouns that every API route, tool call, frontend component, and agent message can share.

The contract owns:

- `source`: an uploaded, discovered, or connected input before transformation.
- `target`: an extracted or queryable table-like object derived from a source.
- `view`: a named SQL view or equivalent durable queryable output.
- `artifact`: any durable output the user can inspect, export, rerun from, or attach to a run.
- `run`: a bounded unit of agent/workbench activity with events and evidence.
- `validation`: deterministic and later model-backed checks attached to artifacts or runs.
- `skill package`: a reusable operational package with instructions, references, scripts, and examples.
- `skill resource`: a concrete file inside a skill package, such as `SKILL.md`, a reference, a script, or an example.
- `user action`: a user-confirmed write, save, overwrite, approval, retry, or export decision.

Near-term contract decisions:

- Prioritize the golden-flow nouns: `source`, `target`, `schema profile`, `generated SQL`, `result view`, `export artifact`, and `run evidence`.
- Make the schema/file browser contract reliable first: file name, source type, parse status, row/column counts, column names/types, available targets, and sparse extraction warnings.
- Treat a saved SQL view and its exported CSV as artifacts.
- [DONE] Store queryable durable outputs as SQLite views for now.
- Store saved exports and other user-meaningful evidence under `artifacts/` when the workflow actually creates durable output.
- [DONE] Keep workspace state local-first; do not spend near-term roadmap energy on auth, multi-user separation, or remote deployment.
- Keep private local paths and internal implementation details out of browser-facing and model-facing payloads unless a trusted backend tool explicitly needs them.
  - [DONE] SQL artifact and source payloads are filtered before reaching the browser-facing API.
- Make lineage normal, not optional: source -> target -> generated SQL -> result view -> export artifact -> run evidence.

Next contract focus:

- [DONE] Define the first browser/model-safe schema profile payload for extracted tables and saved views.
- [DONE] Link source records to extracted target profiles in bootstrap payloads.
- Keep the shared profile lean: source display name, source type, parse status, target names, row/column counts, columns, compact warnings, and lineage.
- Keep raw absolute paths and private catalog tables behind backend tools.

This component is the product grammar. Everything else should speak this grammar.

## 2. Agent Engine And Chat Lifecycle

Goal: make the native general agent explicit as a product engine, not just a chat endpoint.

The agent engine owns the lifecycle of a user turn:

1. **Receive:** accept the user message, attachments, selected UI context, and current workspace pointer.
2. **Context build:** load only the source, target, artifact, skill, run, or result details needed for the turn.
3. **Route:** decide whether the turn needs a direct answer, setup/discovery, tool use, skill-backed workflow, or a longer run.
4. **Plan:** create a small next-step plan when tool use is needed.
5. **Act:** call workbench tools through typed tool contracts, not by reaching directly into UI state.
6. **Observe:** convert tool results into structured observations, warnings, artifact updates, and event updates.
7. **Loop:** use a bounded ReAct-style plan/act/observe loop when the task benefits from iteration.
8. **Approve:** ask for user confirmation before important writes unless the user already explicitly requested that write.
9. **Synthesize:** answer in chat while pointing to visible state, artifacts, warnings, and next action.
10. **Record:** create or update run events, tool-call logs, validation summaries, and run notes.

Agent behavior principles:

- [DONE] Answer ordinary conversation directly when no tools are needed.
- [DONE] Keep predefined routes internal; the user should ask normally while the agent chooses workflow paths.
- [DONE] Use workbench tools when operational grounding improves the answer.
- Keep context progressive instead of dumping the whole workspace into every prompt.
- Keep assistant messages, tool events, and artifact updates distinct.
- [DONE] Make near-term agent-callable work concrete for prep and query: inspect sources, extract CSV/XLSX/PDF tables, run or repair SQL, and save a view.
  - Export exists as a backend/UI saved-view CSV download, but it is not yet an agent-recorded export artifact.
- Avoid fake autonomy. If a tool or write path is not implemented, the agent should say so and suggest the closest real action.

Near-term engine work:

- Optimize for tool-choice accuracy: reliably decide when a user turn needs file/schema inspection, SQL generation, PDF-table extraction, or a direct answer.
- Use the schema/file browser state as the primary context for data questions instead of dumping the entire workspace into prompts.
- Make generated SQL visible enough to inspect when it matters, not hidden assistant behavior.
- Prefer SQL-backed answers for questions over uploaded tabular data, and ground final answers in the returned result rows/columns.
- Surface tool actions enough for debugging without making internal routes the user-facing product model.
- Bound tool use by step count and clear failure states; if a tool path is not implemented, say so and offer the closest real action.
- Make direct-answer turns and tool-using turns feel like one coherent assistant, not two systems.

## 3. Harness-Agnostic Tool And IO Layer

Goal: put all operational IO logic behind reusable product tools that are independent of the chat provider, frontend framework, agent harness, or orchestration engine.

The tool layer owns real workbench operations:

- [DONE] workspace/bootstrap state;
- [DONE] source upload, discovery, inspection, and metadata;
- [DONE] tabular extraction and target listing;
- [DONE] SQL execution, repair context, saved views, and bounded previews;
- artifact creation, lookup, export, and download metadata;
- [DONE] filesystem reads/writes that are safe for the backend to perform;
- [DONE] skill package browse/load/save;
- [DONE] skill resource reads/writes;
- script execution or verification when explicitly allowed;
- validation checks and warning creation;
- run-note and evidence-file creation.

Tool design principles:

- Tools should use explicit typed inputs and structured outputs.
- Tools should be callable from the agent engine, API routes, tests, scripts, and future harnesses without depending on LangGraph, AI SDK, React, or browser state.
- UI and agent integrations should be adapters around the tool layer, not the place where core IO behavior lives.
- Tool results should return observations the agent and UI can both understand:
  - status,
  - changed objects,
  - artifact IDs or names,
  - warnings,
  - validation summaries,
  - generated files,
  - safe user-facing messages,
  - internal debug details only when appropriate.
- Reads should be easy and safe; writes should be explicit, visible, and reported.
- SQL should be bounded and read-only by default.
- Browser-provided paths should never become trusted filesystem paths.
- Script edits require preview/diff before save and verification after save.

Near-term tool priorities:

1. [DONE] Upload and parse CSV/XLSX sources with dependable schema inference, previews, and clear parse errors.
2. Extract table targets from PDFs well enough to join the same schema/file browser and SQL flow.
   - [DONE] Backend PDF inspect/extract tools exist.
   - [DONE] `prep_pdf` exists beside `prep_csv` and loads recovered PDF tables into shared SQLite.
   - Next: keep PDF source/target/profile records aligned with CSV/XLSX and add only light extraction coverage.
3. Inspect sources and targets through one structured schema/profile output.
   - [DONE] Individual source preview and SQL artifact describe/list routes exist.
   - Next: keep source previews and SQL artifact profiles consistent without forcing one oversized profile shape.
4. [DONE] Generate SQL from a natural-language question using the current prepared-data context.
5. [DONE] Keep SQL execution bounded/read-only, expose repair context on runtime failure, and validate the executed result before saving.
6. [DONE] Run SQL and expose repair context on failure.
7. [DONE] Save a useful SQL result as a durable view artifact.
8. Export a saved view as CSV when the user needs a durable file.
   - [DONE] Saved SQLite views can be downloaded as CSV through the API/UI.
   - Next: persist exported CSVs as artifact files only when save/export semantics need durable evidence.
9. Create lightweight run notes only for saved/exported workflows.

This layer should become the app's portable IO substrate. The agent engine decides *when* to use tools; the tool layer defines *what actually happens*.

## 4. Skills As Operational Source Of Truth

Goal: make skills the durable structure for repeatable operations and agent-assisted management.

A skill package is an operational package, not just a prompt:

- `SKILL.md` is the routing and instruction layer.
- `references/` holds canonical source-of-truth material such as SQL contracts, schemas, prompts, formulas, provider notes, and workflow constraints.
- `scripts/` holds runnable operations the agent can execute, inspect, adapt, or verify when the workflow calls for them.
- examples document expected usage, fixtures, inputs, and outputs.

The product should let users and agents:

- [DONE] browse skills;
- [DONE] inspect `SKILL.md`;
- [DONE] inspect references and scripts;
- understand which skill package influenced a run;
- see which resources were loaded or touched;
- [DONE] edit/save existing `SKILL.md`, reference resources, and scripts when the user explicitly asks;
- preview/diff edits before saving;
- verify executable script changes;
- report saved paths, modified state, reload behavior, and verification results.

Skill governance principles:

- Avoid hidden auto-maintenance.
- Do not silently rewrite operational source of truth just because a run found something useful.
- When the user asks the agent to preserve a workflow improvement, the agent can propose or apply scoped edits to `SKILL.md`, references, or scripts.
- Connect skills to runs and artifacts so repeatable procedures have visible provenance.
- Treat skills as operational memory, not just prompt memory.

## 5. Observability And Run Evidence

Goal: make agent work inspectable to users and debuggable to developers.

Observability owns four related layers:

1. **User-visible activity:** what the user sees in the workbench while the agent works.
   - [DONE] Chat streaming exposes compact backend tool traces and stage trace summaries.
2. **Product run events:** durable state transitions for meaningful saved/exported workflows.
3. **Tool-call evidence:** tool name, safe input summary, output summary, errors, warnings, changed objects, and artifacts when useful.
   - [DONE] Tool traces and stage traces exist for current chat turns.
4. **Developer traces/logs:** enough internal detail to debug the system without leaking secrets or private local paths into browser/model payloads.

Possible product event stream:

- run started;
- context loaded;
- route selected;
- source inspected;
- target or view selected;
- tool/action executed;
- SQL run or repaired;
- artifact created;
- validation updated;
- approval requested;
- warning or error raised;
- run note written;
- run completed.

Run note shape:

- actions taken;
- sources used;
- SQL/view names;
- skill packages and skill resources touched;
- artifacts created;
- validation warnings;
- next recommended action.

Near-term observability stance:

- Default to sparse, factual warnings. Avoid turning extraction into a data-quality report or blocking workflow unless the data is unusable.
- Show validation as part of artifacts, not as hidden backend detail.
- Keep assistant messages, tool events, artifact updates, and validation updates separate.
- Persist enough run state for review, comparison, and later resume.
- Let the UI render progress and artifacts without knowing internal engine details.

Trust should come from visible evidence, not from the assistant sounding confident.

## 6. Workbench UI And Human Controls

Goal: make the browser workbench the visible control surface for the agent's work.

The UI should expose the golden flow as visible state, with the schema/file browser as the first-class surface:

- [DONE] a source list for uploaded CSV, XLSX, and uploaded PDF files;
- parse status, row counts, column counts, compact warnings, and available targets per source;
- column names and inferred types for each target;
- an obvious path from selected source/target to asking a question;
- generated SQL shown as an inspectable object before or beside the result;
- [DONE] SQL results in a result grid with export affordances;
- [DONE] saved view artifacts and saved-view CSV downloads;
- validation warnings attached to results/artifacts only when they materially help review;
- chat messages that reference visible schema, SQL, results, and artifacts instead of replacing them.

Next UI focus:

- Make the schema/file browser the primary data surface instead of only a navigation list.
- Show the same lean target profile for CSV/XLSX/PDF-derived tables: source, target, row count, column count, column names/types, compact warnings, and lineage.
- Let the chat panel reference selected source/target context without making the user type route names.

UI principles:

- Make data exploration feel first-class, not hidden behind chat.
- The user should not have to trust only the chat transcript.
- An agent action should leave visible state whenever possible.
- Avoid fake controls or fake completed states.
- If a capability is not implemented, show the limitation honestly and offer the closest real action.
- Keep manual controls useful even while the agent becomes more autonomous.

The workbench should make the agent feel operational: the agent chooses the right tool, the UI shows the source/schema/SQL/result evidence, and the user can inspect, correct, or export the work.


## 7. Source, Artifact, And Validation Expansion

Goal: broaden usefulness only after the core loop and tool surface are coherent.

Source expansion is narrowed for the next build cycle:

1. **CSV/XLSX first:** [DONE] dependable upload, parsing, schema inference, previews, and SQL target creation.
2. **PDF tables next:** extraction into reviewable table targets that can enter the same schema browser and SQL flow.
   - [DONE] PDF inspect/extract tools and `prep_pdf` are implemented.
   - Next: browser/API reviewability and light extraction coverage.
3. **Images, databases, APIs, folders, and batch connectors later:** defer until the CSV/XLSX/PDF-table loop is reliable.

Artifact expansion should keep the same artifact model:

- [DONE] extracted table;
- [DONE] generated SQL;
- [DONE] SQL result view;
- [DONE] saved view;
- CSV export when saved;
- lightweight validation note when useful;
- run note for saved/exported workflows;
- Markdown report later if it is generated from real evidence.

Validation should stay deterministic, factual, and tied to the data-exploration loop:

- [DONE] parse success/failure;
- [DONE] row counts;
- [DONE] column names and inferred types;
- [DONE] query references to known tables/columns;
- [DONE] result row/column shape;
- light source coverage for PDF-table extraction.

Use model-backed review only after deterministic checks establish the basic shape. Do not add profiling or blocking checks until a workflow has clear risk rules.

Breadth should come from plugging into the same loop, not adding one-off mini apps.

## 8. Repeatability And Workflow Packaging

Goal: make recurring jobs feel easier the second time.

Repeatability should let users:

- rerun prior skill-backed workflows against new sources;
- track required companion files and missing inputs;
- compare sources, row counts, totals, validation warnings, and output views across runs;
- attach skill packages and resources to runs;
- preserve workflow improvements in skill resources when explicitly requested;
- make the next similar job explicit.

The long-term value is not a single generated answer. It is a reusable operational loop.

## 9. Engine And Runtime Choices

Goal: make architecture changes only after the product contract makes them useful.

Revisit engines when the agent lifecycle, run events, artifact model, workbench tools, and observability layer are stable enough to compare implementations.

Practical evaluation questions:

- Does this choice improve the native general-agent workbench loop?
- Does it make ReAct-style tool use easier to bound, observe, and debug?
- Does it reduce friction for visible user workflows?
- Does it preserve harness-agnostic tools?
- Does it keep domain skills reusable?
- Does it improve generalizability rather than optimizing for one workflow?

Treat engine adapters and runtime migration as later implementation tracks, not roadmap drivers.

## Near-Term Build Order

The next implementation sequence should follow the chosen golden flow rather than treating each component as a silo:

1. **Ingest:** make CSV/XLSX upload and PDF-table extraction produce consistent source/target/schema records.
   - [DONE] CSV/XLSX upload extracts into SQLite targets.
   - [DONE] PDF extraction can produce SQLite targets through `prep_pdf`.
   - [DONE] Bootstrap now links CSV/XLSX/PDF sources to compact target/schema records.
   - Next: add light extraction coverage and sparse warnings for obvious source/schema issues.
2. **Browse:** make the frontend schema/file browser the primary place to inspect files, targets, columns, warnings, and samples.
   - [DONE] Explorer groups exist for sources, extracted tables, queried views, and skills.
   - [DONE] Source and SQL artifact inspectors show target profiles, sizes, and column chips.
   - Next: expose compact warnings in the same surface.
3. **Ask:** route natural-language data questions toward the right tool path, with tool-choice accuracy as the main agent quality bar.
   - [DONE] Orchestrator has separate `prep_csv`, `prep_pdf`, and `query_stage` tools.
   - Next: prove route choice across attached CSV/XLSX/PDF cases with focused backend tests.
4. **Generate SQL:** create inspectable SQL from the selected/current schema context.
   - [DONE] Query stage generates SQL and persists SQL artifacts.
5. **Validate and run:** check generated SQL against available schema, run it bounded/read-only, and expose repair context on failure.
   - [DONE] Query stage runs bounded SQL, repairs runtime failures, validates the executed result, and saves only after that loop.
   - Next: keep deterministic preflight limited to table/column existence and SQL safety before first execution.
6. **Inspect and export:** show results clearly, save useful views as artifacts, and export saved-view CSVs.
   - [DONE] Saved views can be inspected and downloaded as CSV.
   - Next: persist CSV exports as artifact records/files when the user saves or exports them.
7. **Record evidence:** keep lightweight notes for saved/exported workflows so the answer can be reviewed later.
   - Next: add minimal run notes only after saved/exported artifact semantics are clear.

## Narrowed Direction So Far

Resolved tuning choices:

- **Golden flow:** upload file -> ask question -> generated SQL -> inspect/export result.
- **Frontend priority:** make data exploration first-class, starting with the schema/file browser.
- **Agent priority:** improve tool-choice accuracy so the assistant reliably chooses direct answer, schema inspection, PDF-table extraction, SQL generation, SQL repair, or export based on the task.
- **Ingestion priority:** robust common file support for CSV, XLSX, and PDF tables.
- **SQL/query priority:** reliable natural-language-to-SQL over uploaded/queryable data.
- **Ops stance:** local-first for now; defer auth, protected workspaces, hosted deployment, and observability dashboards.
- **Backend stance:** add only the API/tool contracts needed for the golden flow unless implementation friction proves typed/generated contracts are necessary sooner.
- **Artifact model:** a saved SQL view is an artifact; a saved-view CSV export is also an artifact.
- **Initial export scope:** saved-view CSV only; defer bundle/report/all-artifact export until artifact registry semantics are stronger.
- **Durable evidence bundle:** generated SQL, result view, saved-view CSV export, and a lightweight note when the workflow creates durable output.
- **Run note minimum:** actions taken, sources used, SQL/view names, material warnings, and next recommended action.
- **Run note storage:** begin with saved/exported artifact workflows; avoid making every chat turn a managed run.
- **Validation:** default to sparse deterministic checks, especially parse status, schema shape, generated-SQL references, and result shape.
- **Routes:** keep predefined routes internal; the user should ask normally while the agent chooses workflow paths.
- **Deferred components:** broad connectors, images, multi-user workspaces, auth, deployment, engine/runtime migration, and skill-editing UX are not part of the narrowed near-term path.

## Remaining Implementation Choices

These are not roadmap blockers, but they should be decided while implementing the golden flow:

- Whether every SQL-backed chat turn creates a durable `run`, or only turns that save/export artifacts.
- How much generated-SQL validation happens before first execution versus inside a repair loop after SQL errors.
- Whether PDF-table extraction needs a manual correction/review step before the extracted table becomes queryable.
- Which schema/profile fields are mandatory for the first useful schema/file browser.
- Whether query history and saved views must survive backend restart immediately, or can begin as session-local state.

## Near-Term Bias

Prioritize the local data-exploration workbench loop. Make uploaded CSV/XLSX/PDF-table sources inspectable, make schema state visible, make the agent choose the right tool path, make generated SQL inspectable and reliable, and make results exportable with evidence. Defer ops, connectors, and runtime arguments until this loop is strong enough to expose the next real bottleneck.
