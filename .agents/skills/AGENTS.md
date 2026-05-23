# Tabuflow Skill Context

These repo skills are for choosing the right Tabuflow data tools and domain workflow.

The important boundary is simple: `src/tabuflow` and the `tabuflow` CLI are the reusable tool layer. Use them for direct data inspection, extraction, artifact lookup, SQL querying, and saved views.

## Skill Routing

- Use `tabuflow-standalone-tools` when the task starts from messy CSV, XLS, XLSX, PDF, EML, MSG, or prepared SQLite-backed artifacts and you need to inspect, extract, query, or save a view.
- Use `billing-tabular-pipeline` when the user wants a reusable billing spreadsheet pipeline: inspect source shape, normalize business columns, validate totals, and produce a stable output contract.
- Use `gcp-cost-pipeline` for GCP cost-table transformation into aggregated reconciliation results and IBS charge-item upload rows.
- Use `aws-invoice-pdf-tables` for AWS invoice PDF table extraction and cleanup. Treat adjacent `.eml` or `.msg` files as supporting reference context unless the user explicitly asks for email-derived rows.
- Use `skill-evolution-loop` when improving these skills from real artifacts, reference outputs, isolated pressure tests, or user corrections.

Load the domain skill first when the user names a domain outcome. Load `tabuflow-standalone-tools` first when the main uncertainty is how to inspect, extract, or query a local source file.

## Use The CLI For Data Work

Start from the repo root. Prefer the installed command:

```bash
tabuflow --help
```

If it is not on PATH, use the project runner:

```bash
uv run tabuflow --help
```

All CLI commands print JSON. Treat a nonzero exit or a payload with `status: "error"` as a real failure. Inspect the message, fix the source path/options/config problem, and rerun the smallest relevant command.

Use tabular tools for CSV, XLS, and XLSX:

```bash
uv run tabuflow tabular inspect path/to/file.xlsx --sheet "Sheet1" --start-row 1 --limit 10
uv run tabuflow tabular profile path/to/file.xlsx --sheet "Sheet1"
uv run tabuflow tabular extract path/to/file.xlsx --sheet "Sheet1"
```

Inspect before extracting. Do not assume row 1 is the header. Watch for metadata rows, repeated headers, blank spacer rows, footer totals, merged-ish spreadsheet layout, and several sparse tables in one sheet.

Use PDF tools in two passes:

```bash
uv run tabuflow pdf inspect path/to/file.pdf --page-start 1 --page-limit 3
uv run tabuflow pdf inspect path/to/file.pdf --page-start 1 --page-limit 3 --include-images
uv run tabuflow pdf prepare path/to/file.pdf
```

`pdf inspect` is for bounded page text and optional rendered images. `pdf prepare` renders every page and creates a lean normalized-filename workspace under `artifacts/pdf/...` with `manifest.json`, `pages/*.jpg`, and `work/`. It defaults to 150 DPI and stops above the page-count guard unless `--max-pages` is raised. Write recovered tables into the work directory, then import them through tabular or artifact tooling when ready. If layout or page boundaries are ambiguous, say so instead of presenting the extraction as complete.

Use email tools only for reference context:

```bash
uv run tabuflow email inspect path/to/message.eml
uv run tabuflow email inspect path/to/message.msg
```

Email inspection returns metadata, body preview, body length, and attachment names. It does not create billing-table artifacts. Do not replace spreadsheet/PDF billing rows with email body text unless the user explicitly asks for that.

Use artifact tools after extraction:

```bash
uv run tabuflow artifacts list
uv run tabuflow artifacts from-source path/to/file.xlsx
uv run tabuflow artifacts describe artifact_name
uv run tabuflow artifacts query "select * from artifact_name limit 20"
uv run tabuflow artifacts query @query.sql
uv run tabuflow artifacts save-view saved_view_name @query.sql
```

Use `artifacts from-source` to find outputs for a specific input file. Use `describe` before writing SQL. Start with a small `limit` query. Put non-trivial SQL in a normal `.sql` file and pass it as `@query.sql` so the logic stays reviewable. Quote generated artifact names that contain hyphens: `select * from "service-usage-1cca2e" limit 20;`.

## Use Ordinary Tools For Ordinary Work

Keep normal repo work in ordinary shell/editor tools: `rg`, focused file reads, code edits, SQL drafts, reports, tests, diffs, and verification. Do not wrap ordinary filesystem reading, writing, searching, or shell work in Tabuflow just because a helper exists.

Use Tabuflow when it adds domain value: robust spreadsheet/PDF inspection, conservative extraction, artifact catalog lookup, read-only SQLite querying, saved views, and schema-aware SQL repair hints. Use plain shell/editor tools for everything else.

Do not ask the model to choose artifact storage roots or database paths. Tabuflow resolves those from local runtime configuration. The caller may choose source paths, sheet/page options, bounded limits, and SQL text.

## Python Tool Layer

When the CLI JSON shape is awkward, call the standalone Python functions directly from repo code or a scratch script. Import from:

- `tabuflow.tabular` for tabular inspection, profiling, and extraction.
- `tabuflow.pdf` for PDF inspection and preparation.
- `tabuflow.mail` for EML/MSG inspection.
- `tabuflow.artifacts` for artifact listing, description, read-only queries, saved views, artifact lookup, and SQL repair hints.

## Validation Bar

Before calling data work done:

- Confirm the source was inspected before extraction.
- Confirm extracted artifacts can be found with `artifacts list` or `artifacts from-source`.
- Describe the artifact before writing non-trivial SQL.
- Run a small `limit` query before larger SQL.
- Reconcile row counts, totals, months, vendors, or service labels back to the source when the task is analytical.
- Save a view only after the query result is validated.
- Report incomplete extraction, ambiguous headers, missing mappings, OCR/layout uncertainty, and any guessed business interpretation plainly.

Generated table names, hashes, page chunk names, row counts, and one run's saved view names are not durable business concepts. Use them as local artifact handles, not as domain truth.
