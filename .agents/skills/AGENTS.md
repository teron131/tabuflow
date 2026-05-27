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

Start from the repo root. Prefer the installed CLI:

```bash
tabuflow --help
```

If it is not on PATH, use the repo-local runner as a fallback:

```bash
uv run tabuflow --help
```

For OpenCode or another shell-capable coding agent, do not copy Tabuflow scripts into the agent's tool directory. Call the installed `tabuflow` CLI and keep Tabuflow's implementation in the Python package.

All CLI commands print JSON. Treat a nonzero exit or a payload with `status: "error"` as a real failure. Inspect the message, fix the source path or options, and rerun the smallest relevant command.

Use tabular tools for CSV, XLS, and XLSX:

```bash
tabuflow tabular inspect path/to/file.xlsx --sheet "Sheet1" --start-row 1 --limit 10
tabuflow tabular profile path/to/file.xlsx --sheet "Sheet1"
tabuflow tabular extract path/to/file.xlsx --sheet "Sheet1"
```

Inspect before extracting. Do not assume row 1 is the header. Watch for metadata rows, repeated headers, blank spacer rows, footer totals, merged-ish spreadsheet layout, and several sparse tables in one sheet.

Use PDF tools in two passes:

```bash
tabuflow pdf inspect path/to/file.pdf --page-start 1 --page-limit 3
tabuflow pdf inspect path/to/file.pdf --page-start 1 --page-limit 3 --include-images
tabuflow pdf prepare path/to/file.pdf
tabuflow pdf extract path/to/file.pdf tables detected --page-start 1 --min-rows 2
tabuflow pdf extract path/to/file.pdf tables detected --strategy text --require-header --page-start 2 --page-end 3 --min-rows 2
tabuflow pdf extract path/to/file.pdf tables detected --vertical-strategy text --horizontal-strategy lines --page-start 3 --page-end 13 --output-columns model,organization,score --min-filled-cells 2 --merge-tables auto
tabuflow pdf extract path/to/file.pdf tables line-value --value-pattern '^\d+\s*$' --label-column device --value-column score --output-columns device,score
tabuflow pdf extract path/to/file.pdf tables coordinate --pages 2 --y-min 180 --y-max 760 --column model:50:190 --column score:190:260 --required-columns model,score
```

`pdf inspect` is for bounded page text, row geometry, and default 2x2 overview batches of four pages each. Use `--include-images` only for focused full-page image inspection. `pdf prepare` copies the source PDF, renders every page, and creates a normalized-filename workspace under the root-owned `artifacts/pdf/...` path with `manifest.json`, `pages/*.jpg`, `text/*.txt`, and `work/`.

Do not assume one PDF has one extraction strategy. Treat PDF extraction like writing a small script from inspectable puzzle pieces: inspect the page profile, table detections, row geometry, default 2x2 overview batches, focused page images when needed, and text; then make one independent extraction decision per visual table, grouped logical table, or coordinate/text region. A single PDF may need `tables detected` for ruled grids, `tables field-value` or `tables line-value` for headerless label/value blocks, `tables coordinate` for stable x-bands, and explicit ignore rules for false positive one-cell detections. Use priority inside a region, not as a global document choice: `table_region_hints` first when present, with each group treated as its own decision unit; plausible table detections next, especially `interpretation.rows` when `interpretation.usable` is true; row/field geometry for weak or missing detection; overview batches for layout and continuation checks; raw linear text only to confirm exact spelling and wrapped values.

Use `pdf extract` only after the layout mechanics are understood. It is an LLM-free, argument-driven preset layer over PyMuPDF: `tables detected` uses PyMuPDF table detection, `tables coordinate` groups words into configured x-coordinate bands, `tables field-value` collects configured fields from extracted text, and `tables line-value` pairs text lines with matching value lines. The command owns source-specific context such as page ranges, stop/skip lines, value patterns, output columns, x-coordinate bands, PyMuPDF table strategies, and optional clip rectangles, but not output paths. Outputs always go to `artifacts/pdf/<normalized-source>/work/tables` under the selected root, and the manifest records the effective extraction arguments. Table CSV filenames use page tags such as `p01p88` rather than cascade indexes; descriptor and fingerprint suffixes are fallback disambiguators only. For text-positioned tables without ruling lines, try `tables detected --strategy text`; when detector noise creates generic `column_1` tables, use `--require-header`; for page chrome or footers around the table, constrain the detector with `--clip X0,Y0,X1,Y1`. For one continuing table whose detected headers drift across pages, pass `--output-columns` and, when useful, `--min-filled-cells`; page-leading first-column continuations join the previous row before chunks are merged by `--merge-tables auto`. Use `--merge-tables never` for repeated same-schema tables that are visually separate, and `--merge-tables always` when inspection shows one logical table split into fragments. For field/value specs with multiline values, pass `--collect-until-next-field`. For coordinate tables with wrapped labels, pass `--continuation-column` so stable required columns anchor each row and nearby wrapped text joins the right row; tune `--anchor-y-slop` when continuations drift between adjacent rows. `tables detected` records source pages/tables/bounding boxes in the manifest. Empty outputs include diagnostics so no-table text PDFs can be distinguished from PDFs with no extractable text. Use images as verification evidence when text order or column bands are ambiguous, not as the default input.

Use email tools only for reference context:

```bash
tabuflow email inspect path/to/message.eml
tabuflow email inspect path/to/message.msg
```

Email inspection returns metadata, body preview, body length, and attachment names. It does not create billing-table artifacts. Do not replace spreadsheet/PDF billing rows with email body text unless the user explicitly asks for that.

Use artifact tools after extraction:

```bash
tabuflow artifacts list
tabuflow artifacts from-source path/to/file.xlsx
tabuflow artifacts describe artifact_name
tabuflow artifacts query "select * from artifact_name limit 20"
tabuflow artifacts query @query.sql
tabuflow artifacts save-view saved_view_name @query.sql
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
- `tabuflow.email` for EML/MSG inspection.
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
