---
name: tabuflow-standalone-tools
description: Use when inspecting, extracting, or querying messy CSV, XLS, XLSX, EML, MSG, or PDF business data with Tabuflow's standalone CLI or Python tool layer.
---

# Tabuflow Standalone Tools

Use this skill when you need Tabuflow's robust data-file presets for local business files.

The useful boundary is simple: use Tabuflow for best-fit presets around messy data preparation and artifact queries; use ordinary shell/editor tools for file reading, editing, reports, SQL draft files, and one-off exploration. Do not hardcode generated names, row counts, months, or vendor-specific layouts as if they were stable contracts.

## Goal

Turn messy local business files into inspectable SQLite-backed artifacts that can be queried and saved:

- inspect CSV/XLS/XLSX/PDF sources before assuming their structure,
- extract tables into Tabuflow's configured artifact store,
- inspect EML/MSG files as reference context,
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

## CSV, XLS, and XLSX Workflow

1. Inspect a bounded raw window before extracting.

```bash
tabuflow tabular inspect path/to/file.csv
tabuflow tabular inspect path/to/file.xls --sheet "Bill Item" --start-row 1 --limit 10
tabuflow tabular inspect path/to/file.xlsx --sheet "Sheet1" --start-row 1 --limit 10
```

2. Profile structure when headers, metadata rows, or multiple table regions are unclear.

```bash
tabuflow tabular profile path/to/file.xlsx
```

3. Extract only after you understand the likely table shape.

```bash
tabuflow tabular extract path/to/file.csv
tabuflow tabular extract path/to/file.xls --sheet "Bill Item"
tabuflow tabular extract path/to/file.xlsx --sheet "Sheet1"
```

Do not assume row 1 is the header. Watch for metadata rows, repeated headers, blank spacer rows, footers, and several sparse tables in one sheet.

## PDF Workflow

1. Use inspection first. It returns extracted page text and, when requested, rendered page images.

```bash
tabuflow pdf inspect path/to/file.pdf
tabuflow pdf inspect path/to/file.pdf --page-start 1 --page-limit 3 --include-images
```

2. Prepare a durable PDF artifact workspace when visual table recovery needs more than a quick preview.

```bash
tabuflow pdf prepare path/to/file.pdf
```

`pdf inspect` is for bounded page text and optional rendered images. `pdf prepare` copies the source PDF, renders every page, and creates a normalized-filename workspace with `manifest.json`, `pages/*.jpg`, `text/*.txt`, and `work/` under the root-owned `artifacts/pdf/...` path.

For text PDFs with a repeatable layout, use CLI-shaped PyMuPDF extraction options:

```bash
tabuflow pdf extract path/to/file.pdf tables detected --page-start 1 --min-rows 2
tabuflow pdf extract path/to/file.pdf tables detected --strategy text --require-header --page-start 2 --page-end 3 --min-rows 2
tabuflow pdf extract path/to/file.pdf tables detected --vertical-strategy text --horizontal-strategy lines --page-start 3 --page-end 13 --output-columns model,organization,score --min-filled-cells 2 --merge-tables auto
tabuflow pdf extract path/to/file.pdf tables line-value --value-pattern '^\d+\s*$' --label-column device --value-column score --output-columns device,score
tabuflow pdf extract path/to/file.pdf tables line-value --value-preset money --section '^(Section A|Section B)$' --context 'group=^Group: (?P<value>.+)$' --split-sections --drop-empty-split --include-page
tabuflow pdf extract path/to/file.pdf tables coordinate --pages 2 --y-min 180 --y-max 760 --column model:50:190 --column score:190:260 --required-columns model,score
```

Use `extract` when the PDF fits simple repeatable mechanics. It is a narrow preset layer over PyMuPDF, not a replacement for the generic `python -m pymupdf` utility commands. Use `tables detected` for PyMuPDF-detected tables, `tables coordinate` for x-coordinate table bands, `tables field-value` for configured field labels, and `tables line-value` for adjacent label/value lines. The command should name the target and preset, page ranges, cleanup rules, output columns, coordinate bands or value patterns, PyMuPDF table strategies, and optional clip rectangles, but it cannot choose output paths. Outputs always go to the root-owned PDF artifact workspace at `artifacts/pdf/<normalized-source>/work/tables`, and the manifest records the effective extraction arguments. For text-positioned tables without ruling lines, try `tables detected --strategy text`; when detector noise creates generic `column_1` tables, use `--require-header`; for page chrome or footers around the table, constrain the detector with `--clip X0,Y0,X1,Y1`. For one logical table split across pages whose later headers drift, pass `--output-columns` and, when needed, `--min-filled-cells`; page-leading first-column continuations are joined to the previous row while adjacent chunks with the same forced schema merge according to `--merge-tables auto`. Use `--merge-tables never` when repeated same-schema tables are visually separate, and `--merge-tables always` when inspection shows the detector split one logical table into fragments. For field/value specs with multiline values, use `--collect-until-next-field`; for line/value or field/value PDFs with repeated sections or parent labels, use `--value-preset money`, `--section REGEX`, `--context FIELD=REGEX`, and `--clear-context FIELD=REGEX` to carry bounded context columns alongside the raw rows. A context regex may use a named `value` capture, otherwise the first capture or full line is used. Use `--split-by FIELD`, or `--split-sections` for the common section case, when a carried context column should become separate CSV outputs; use `--drop-empty-split` when header metadata should not become its own table. For coordinate tables where a label column wraps across multiple baselines, use `--continuation-column` and, if needed, `--anchor-y-slop`; stable required columns then anchor each row and nearby continuation text joins that row. `tables detected` defaults to `--merge-tables auto`, which merges same-schema chunks only when geometry looks continuous, and records source pages/tables/bounding boxes in the manifest; treat those outputs as raw detector tables and inspect them before import. Empty outputs include diagnostics so no-table text PDFs can be distinguished from PDFs with no extractable text. If the layout family is not clear, inspect text and a few page images first; do not feed all page images by default.

## Email Reference Workflow

Use this when adjacent email files may explain source grouping, attachments, or workflow context. It does not create billing-table artifacts.

```bash
tabuflow email inspect path/to/message.eml
tabuflow email inspect path/to/message.msg
```

Treat `reference_only: true` as a boundary. The generic payload gives message metadata, body preview, body length, and attachment names. Use a domain skill or project-specific logic to interpret the body; do not replace PDF or spreadsheet billing rows with email text unless the user explicitly asks for email reconciliation data.

## Artifact Query Workflow

After extraction, use artifacts as the stable query boundary.

```bash
tabuflow artifacts list
tabuflow artifacts from-source path/to/file.xlsx
tabuflow artifacts describe artifact_name
tabuflow artifacts query "select * from artifact_name limit 20"
tabuflow artifacts query @query.sql
tabuflow artifacts save-view saved_view_name @query.sql
```

`artifacts list` returns a compact, bounded index by default. Use `--max-items`, `--all`, or `--detail full` when the compact index is not enough; use `describe` for a focused schema/sample payload before writing SQL.

Write non-trivial SQL in an ordinary `.sql` file and pass it with `@query.sql`. This keeps SQL reviewable and avoids hiding logic inside chat history.

Use `from-source` after extraction to find the reusable artifacts produced by a specific input. For PDFs, prepare the visual/text workspace first, then import reviewed table artifacts into SQLite before querying.

Generated artifact names often contain hyphens. Quote them as SQLite identifiers in SQL, for example:

```sql
select * from "service-usage-1cca2e" limit 20;
```

Source-backed table and PDF workspace names use normalized filenames such as `abc_def` or `abc_def_(2)`. If different content collides on the same normalized name, Tabuflow appends `_2`, `_3`, and so on; identical table content reuses the existing fingerprint-backed table.

## Python API Option

When CLI JSON is awkward, call the standalone Python functions directly from repo code:

- `tabuflow.tabular`: tabular inspection, profiling, and extraction,
- `tabuflow.pdf`: PDF inspection and preparation,
- `tabuflow.artifacts`: artifact listing, description, read-only query, and saved views.

## Boundaries

- Do not use Tabuflow as a generic filesystem wrapper. Ordinary shell/read/edit tools are better for that.
- Do not ask the model to choose artifact storage roots or database paths. Tabuflow resolves those from local runtime configuration.
- Do not treat generated table names, content hashes, page chunk names, row counts, or one run's view names as durable business concepts.
- Keep source paths and SQL text under caller/user control; keep artifact storage configuration outside model-editable arguments.

## Validation Bar

Before calling the work done:

- confirm the source was inspected before extraction,
- confirm extracted artifacts can be listed and described,
- run a small `limit` query before larger SQL,
- reconcile row counts or totals back to the source when the task is analytical,
- save a view only after the query result has been validated,
- report incomplete extraction, ambiguous headers, missing mappings, or PDF layout uncertainty plainly.
