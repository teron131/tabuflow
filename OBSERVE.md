# Observe Notes

## Current Direction: Command-First, Tool-Layer First

Tabuflow should stay centered on reusable local data tools, not on a custom agent graph. The useful product is a small command/Python toolbelt for messy business files: inspect them, extract recoverable tables, keep source lineage, query artifacts, write reusable SQL artifacts, and write reviewable outputs.

Any Coding Agent means a normal coding agent such as Codex, Pi, OpenCode, or another shell/read/edit-capable agent. It should be able to use Tabuflow without learning LangGraph state, LangChain transcripts, or Tabuflow-specific orchestration internals.

## Boundary

- `src/tools` is the reusable layer. It should expose ordinary Python functions for tabular inspection/extraction, PDF inspection/extraction, email reference inspection, artifact catalog/query/view operations, and deterministic repair hints.
- `src/cli.py` is a small preset surface over the useful standalone operations. It should wrap robust data workflows, not generic file reading/editing that coding agents already have.
- `src/agents` owns custom Tabuflow workbench behavior: prep agents, Query Stage SQL reuse/history, SQL-file edits, validation retries, fixer, orchestration state, and graph routing.
- `src/agents/tool_adapter.py` is a compatibility adapter for LangChain. LangChain is a consumer of the tool layer, not the foundation.
- `src/tools/artifacts` owns concrete run outputs and SQLite-backed artifact metadata. SQL files should also live with artifacts because they are produced/reusable project work, while skills stay as guidance contracts about desired outcomes, validation, and failure modes.

## Generic Tool Bar

Tabuflow tools are valuable only when they improve robustness, repeatability, or efficiency over freestyle shell, Python, SQL, and file reads. Freestyle commands are the baseline. If a one-off script is clearer and no less reliable, use it.

Good generic targets:

- CSV, XLS, XLSX, PDF, EML, MSG,
- extracted SQLite artifacts,
- SQL files,
- output CSV/XLSX files,
- source/catalog metadata.

Bad generic targets:

- one vendor's billing meaning,
- one customer's mapping,
- one month's GCP/AWS/Alibaba convention,
- generated content hashes as business concepts,
- LangChain graph state.

## Where The Tools Earn Their Keep

The tabular tools are useful because they do more than `head` or a quick `pandas.read_csv`: they detect encoding/dialect, stream bounded CSV previews, normalize XLS/XLSX rows, preserve merged-cell values, identify likely headers and table regions, and load recovered tables into SQLite with source lineage and typed views.

The latest tabular improvement made the useful hints explicit:

- `tabular inspect` and `tabular profile` return `structure_hints` with likely header and data-start rows.
- `tabular profile --all-sheets` summarizes workbook sheets in one call.
- `tabular extract` returns `excluded_row_hints` for footer-like rows left outside the loaded table, such as `Rounding error` and `Total`.

The artifact tools are useful because they turn extracted data into a repeatable query boundary: `list`, `from-source`, `describe`, bounded read-only `query`, SQL repair hints, and saved views. `artifacts from-source` now returns a preferred artifact plus quoted preview SQL so agents can start from source lineage instead of memorizing generated table names.

The artifact database is the working data warehouse for prepared files. Users may already have many source files loaded into SQLite, so normal analysis should begin by listing/describing artifacts and writing ordinary SQL files against the database, not by asking an agent to remember file names or table names from chat history. Agents can author and run SQL, but the reusable unit should be the `.sql` artifact and the saved view/output it proves.

PDF tools are useful when deterministic text/image inspection or model-backed visual table extraction saves manual page work. They should report ambiguity instead of pretending OCR/layout output is complete.

Email inspection is reference context only. Emails can explain approvals, periods, account IDs, attachments, and reporting context, but they are not billing-table truth unless the task explicitly asks for email-derived data.

## Data Input/Output Test Insights

Keep these because they came from real files and still guide the generic tool design.

### CSV and spreadsheet inputs

- Do not assume row 1 is the header. `examples/gcp/cost_table.csv` has invoice metadata in rows 1-8 and the real table header on row 9.
- Parse CSVs with a real CSV parser. Quoting, sparse cells, and embedded commas are normal in billing exports.
- Keep rows above the detected table as metadata instead of merging them into headers.
- Do not automatically merge multi-row headers. Earlier header merging corrupted simple CSVs, absorbed first data rows into schemas, and created fake column names such as month/rate-prefixed labels.
- Fill only truly blank column names with stable placeholders such as `row_label` or `column_N`.
- Treat wide accountant spreadsheets as 2D regions, not just row-density streams. Sparse rows can still belong inside the same table box when surrounding rows support the same column bands.

### Extraction outputs

- The tabular extractor is an extraction layer, not a full-fidelity read layer. It should recover usable table blocks, keep uncertain/non-table blocks as metadata, and avoid semantic invention.
- A future read/render layer can preserve fuller visual context, ordering, and surrounding text. That should not be forced into the extraction contract.
- Extracted tables should be queryable through SQLite artifacts with source lineage, row counts, exact content fingerprints, typed views, and enough catalog metadata to rediscover outputs later.
- `fingerprint` is the single table-content identity. It is a SHA-256 hash over ordered columns plus all stored rows, and it is the uniqueness key for deduping table artifacts. Do not reintroduce a second `content_id` concept or a sampled fingerprint for storage.
- Preview limits belong to inspect/profile/describe surfaces only. `tabular inspect`, `tabular profile`, and `artifacts describe` should show enough rows to orient a human or coding agent without dumping the dataset; extraction should always store the full recovered table in SQLite.
- Footer rows such as `Rounding error` and `Total` should be excluded from loaded table bodies when they are clearly footers, but reported through `excluded_row_hints` so the agent can reconcile totals honestly.

### Real-file pressure tests

Useful behaviors observed across the repo examples:

- GCP raw/export CSVs are handled when metadata rows, real headers, and footer totals are separated.
- AWS revenue/cost XLSX examples need merged-cell preservation and conservative table boundaries.
- Wide GCP monthly matrices need box-based segmentation so sparse later rows do not get dropped.
- Side mini-tables in aggregated workbooks should stay isolated instead of being merged into the main table.
- Alibaba-style summary workbooks should keep summary rows as metadata and start the main table at the real header.

### Output contracts

- Generic extraction should not produce final business outputs by itself. It prepares artifacts.
- Domain outputs need maintained rules/mappings/configuration: customer mappings, categories, discounts, exchange rates, template constants, rounding rules, and validation totals.
- GCP Summary + IBS proved the generic tools are sufficient for inspection/extraction/querying, but the final outputs belong to a recipe/output layer or maintained implementation.
- AWS invoice work should start from PDF table extraction. Email files beside the invoices are reference evidence only unless the task explicitly asks for an email-derived dataset.

## Domain Skills

Repo skills should stay outcome-first. They should say what source artifacts count, what result must exist, and how to validate it. They should not become command transcripts.

### GCP

The GCP skill treats one raw monthly cost table as the required input. Reference workbooks can explain shape and intent, but they are not default runtime inputs.

Required outputs:

- aggregated GCP reconciliation result,
- IBS charge-item upload result.

The generic tools are enough to inspect/extract/query the source, but the business output still needs domain rules: category rules, maintained customer mapping, discounts, HKD rate, IBS constants, total-row behavior, and rounding. That belongs in a recipe/output layer or maintained implementation, not in the generic tabular tool.

### AWS

AWS examples are mostly PDFs, so the minimum useful result is coherent tabular data extracted from invoices. Direct text extraction is the first pass for text PDFs. OCR/visual extraction should be reserved for pages with no text, incomplete text extraction, or ambiguous layout.

Adjacent `.eml` and `.msg` files are supporting reference context, not default structured billing output.

## Improvement Priorities

1. Keep roots and database paths explicit for repeatable CLI work. `--root-dir` and artifact `--database-path` are useful for coding agents running from different working directories.

2. Improve extraction efficiency only where it beats freestyle. Large CSV extraction still has a full-layout safety cap; if large CSVs matter, add streaming/chunked ingestion or document when direct SQLite/DuckDB/Python is better.

3. Add a generic recipe/output layer. The missing middle is not another agent graph; it is a repeatable runner that can take prepared artifacts plus maintained config/mappings, run reviewable SQL/Python transforms, validate totals/row counts, and write named CSV/XLSX outputs. GCP Summary + IBS is one recipe.

4. Keep generated artifact names out of business logic. Use `from-source`, `describe`, catalog metadata, saved views, and recipe-level stable names to bridge generic extraction to repeatable outputs.

5. Add a first-class artifact file structure for SQL. The expected direction is `artifacts/sql/` for reusable query files and `artifacts/outputs/` for validated CSV/XLSX-style outputs, with SQLite catalog/run metadata indexing those files instead of hiding SQL in chat or graph state.

6. Keep the app/workbench generic. UI and API defaults should expose prepared sources, artifacts, saved views, SQL files, and output files without baking GCP/AWS-specific result columns into the core product.

## Code Improvements Landed

- `src/cli.py` accepts `--root-dir` on `tabular`, `pdf`, `email`, and `artifacts` command groups.
- `artifacts` accepts `--database-path`, with relative database paths resolved under `--root-dir`.
- `tabular inspect/profile` expose likely header and data-start hints directly.
- Workbook profiling can run across all sheets with `--all-sheets`.
- Extraction reports footer-like rows it left outside the loaded table.
- `artifacts from-source` returns a preferred typed target and a ready `SELECT ... LIMIT 20` query hint.
- SQLite tabular catalog identity is fingerprint-only: `_tabular_contents.fingerprint` is the primary key and `_tabular_sources.fingerprint` is the lineage key. The old `content_id` path was removed rather than shimmed.
- Extraction no longer exposes `sample_rows`; it stores full recovered tables. Preview defaults were raised so inspect/profile/describe are more useful without changing ingest: `tabular inspect` defaults to 20 rows, tabular profile uses 20 sample rows, and `artifacts describe` defaults to 10 sample rows with a 20-row cap.

Recent verification used:

- `uv run ruff check src/tools/tabular src/tools/artifacts src/cli.py --fix`
- `uv run ruff format src/tools/tabular src/tools/artifacts src/cli.py`
- `uv run python -m pytest tests/test_cli.py tests/test_tabular_xls.py tests/test_sql_artifact_reuse.py`
- `uv run tabuflow tabular profile examples/gcp/cost_table.csv --max-sample-rows 3`
- `uv run tabuflow tabular profile examples/gcp/IBS_ChargeItemUploadTemplate_Cloud_GCP_20260312.xls --all-sheets --max-sample-rows 2`
- `uv run ruff check src/tools/tabular/storage.py src/tools/tabular/tools.py src/tools/artifacts/catalog_metadata.py src/tools/artifacts/catalog.py src/tools/pdf/tools.py src/cli.py src/agents/tool_adapter.py tests/test_cli.py`
- `uv run python -m pytest`
- `uv run tabuflow tabular inspect examples/gcp/cost_table.csv`
- `uv run tabuflow tabular profile examples/gcp/cost_table.csv`
- `uv run tabuflow artifacts --root-dir . describe billing-account-23ac1d_typed`
- `uv run tabuflow artifacts --root-dir . list --max-items 3 --detail compact`

## Decision Rule

Use Tabuflow tools when they reduce schema/layout mistakes, preserve source lineage, make query work repeatable, or produce validated reusable outputs. Use freestyle commands when they are faster, clearer, and no less reliable.

The durable architecture is command-first and recipe-backed. The agent layer is optional orchestration around those primitives.

## Markdown Style

Do not hard-wrap ordinary prose. Keep prose paragraphs and bullet text on one line unless a real Markdown structure, table, code fence, or nested list needs multiple lines.
