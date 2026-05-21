---
name: tabuflow-standalone-tools
description: Use when a coding agent needs to inspect, extract, or query messy CSV, XLSX, or PDF business data with Tabuflow's standalone CLI or Python tool layer instead of relying on the custom LangChain/LangGraph agent.
---

# Tabuflow Standalone Tools

Use this skill when you are an Any Coding Agent such as OpenCode, Pi, Codex, or another agent with normal shell/read/edit abilities, and you need Tabuflow's robust data-file presets without entering Tabuflow's custom agent graph.

The useful boundary is simple: use Tabuflow for messy data preparation and artifact queries; use your native coding-agent tools for ordinary file reading, editing, shell commands, reports, and SQL draft files.

## Goal

Turn messy local business files into inspectable SQLite-backed artifacts that can be queried and saved:

- inspect CSV/XLSX/PDF sources before assuming their structure,
- extract tables into Tabuflow's configured artifact store,
- list and describe prepared artifacts,
- run bounded read-only SQL against artifacts,
- save useful query results as named views when the result is worth keeping.

## Start Here

From the repo root, prefer the installed command when available:

```bash
tabuflow --help
```

If the command is not on PATH, use the project runner:

```bash
uv run tabuflow --help
```

All CLI commands print JSON. Treat a nonzero exit or a JSON payload with `status: "error"` as a real failure to inspect and fix.

## CSV and XLSX Workflow

1. Inspect a bounded raw window before extracting.

```bash
tabuflow tabular inspect path/to/file.csv
tabuflow tabular inspect path/to/file.xlsx --sheet "Sheet1" --start-row 1 --limit 10
```

2. Profile structure when headers, metadata rows, or multiple table regions are unclear.

```bash
tabuflow tabular profile path/to/file.xlsx
```

3. Extract only after you understand the likely table shape.

```bash
tabuflow tabular extract path/to/file.csv
tabuflow tabular extract path/to/file.xlsx --sheet "Sheet1"
```

Do not assume row 1 is the header. Watch for metadata rows, repeated headers, blank spacer rows, footers, and several sparse tables in one sheet.

## PDF Workflow

1. Inspect pages first, optionally asking for page images when layout matters.

```bash
tabuflow pdf inspect path/to/file.pdf
tabuflow pdf inspect path/to/file.pdf --page-start 1 --page-limit 3 --include-images
```

2. Extract tables after selecting a reasonable page scope.

```bash
tabuflow pdf extract path/to/file.pdf
tabuflow pdf extract path/to/file.pdf --max-chunks 2
```

PDF extraction may use a model when configured, but do not make the rest of the workflow depend on a custom Tabuflow agent. If extraction is incomplete, report the ambiguous pages or layout gaps instead of pretending the artifact is complete.

## Artifact Query Workflow

After extraction, use artifacts as the stable query boundary.

```bash
tabuflow artifacts list
tabuflow artifacts describe artifact_name
tabuflow artifacts query "select * from artifact_name limit 20"
tabuflow artifacts query @query.sql
tabuflow artifacts save-view saved_view_name @query.sql
```

Write non-trivial SQL in an ordinary `.sql` file and pass it with `@query.sql`. This keeps SQL reviewable by any coding agent and avoids hiding logic inside chat history.

## Python API Option

When CLI JSON is awkward, call the standalone Python functions directly from repo code instead of importing custom agent modules:

- `src.tools.tabular`: tabular inspection, profiling, and extraction,
- `src.tools.pdf`: PDF inspection and extraction,
- `src.tools.artifacts`: artifact listing, description, read-only query, and saved views.

Avoid importing from `src.agents` unless you are intentionally working on Tabuflow's custom LangChain/LangGraph agent.

## Boundaries

- Do not use Tabuflow as a generic filesystem wrapper. Any Coding Agent already has shell/read/edit tools.
- Do not ask the model to choose artifact storage roots or database paths. Tabuflow resolves those from local runtime configuration.
- Do not rely on graph state, message reducers, LangChain tool-call transcripts, or `src/agents/query_stage` internals for standalone workflows.
- Do not treat generated table names, content hashes, page chunk names, row counts, or one run's view names as durable business concepts.
- Keep source paths and SQL text under agent/user control; keep artifact storage configuration outside model-editable arguments.

## Validation Bar

Before calling the work done:

- confirm the source was inspected before extraction,
- confirm extracted artifacts can be listed and described,
- run a small `limit` query before larger SQL,
- reconcile row counts or totals back to the source when the task is analytical,
- save a view only after the query result has been validated,
- report incomplete extraction, ambiguous headers, missing mappings, or PDF layout uncertainty plainly.
