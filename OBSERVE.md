# Observe Notes

## Current Direction: Standalone Tool Layer Rework

Date: `2026-05-21`.

The current refactor is no longer trying to make every internal agent tool equally public. The goal is to isolate reusable data operations from the custom LangChain/LangGraph agent stack so Tabuflow can be used by Any Coding Agent, scripts, and CLI workflows without inheriting graph-state shapes.

Any Coding Agent means the ordinary coding-agent setup rather than a Tabuflow-specific agent: OpenCode, Pi, Codex, or another agent that can run commands, read files, edit files, and call repo-local scripts. These agents should be able to use Tabuflow as a small toolbelt for robust data operations while keeping their own native file and shell workflow.

### Boundary that now feels right

- `src/tools` is the reusable layer. It should expose ordinary Python functions for tabular inspection/extraction, PDF inspection/extraction, artifact catalog/query/view operations, deterministic SQLite repair hints, filesystem sandbox helpers, and workspace skill file helpers.
- `src/cli.py` is a small preset surface over the useful standalone operations. It should wrap robust data workflows for Any Coding Agent usage, not generic file reading/editing that a coding agent already has through shell/read/edit tools.
- `src/agents/tool_adapter.py` is the LangChain tool adapter layer. It turns standalone functions into LangChain tools and binds workspace/storage paths outside the model-visible schemas.
- `src/agents` owns custom Tabuflow agent behavior: prep agents, Query Stage SQL reuse/history, SQL-file edits, validation retries, fixer, orchestration state, and graph routing.
- `src/tools/artifacts` owns artifact structure and SQLite-backed artifact helpers. It is not the same thing as skills. Skills are reusable guidance contracts; artifacts are concrete working outputs and metadata from runs.

### What changed in the tool split

- LangChain tool wrappers moved out of the core tool layer and now sit under `src/agents/tool_adapter.py`.
- Fixer is treated as agent-centric and stays under `src/agents/fixer`, because other coding agents do not need Tabuflow's fixer graph to work with the data tools.
- SQL history reuse, SQL artifact file editing, and Query Stage repair loops moved under `src/agents/query_stage` because they are custom-agent orchestration behavior, not a general-purpose public tool surface.
- The old standalone SQL-tool idea has narrowed into `src/tools/artifacts`: list/describe queryable artifacts, run read-only SQLite queries, save views, name SQL artifacts when an LLM namer is available, and provide deterministic repair hints.
- Filesystem sandbox/hashline helpers are isolated under `src/tools/fs`, with workspace-specific helpers in `workspace.py`. They remain useful for adapters and custom agents, but they are not the main CLI value proposition.

### State and field cleanup

The agent state is being trimmed by ownership rather than deleted blindly. The old broad SQL artifact state mixed reuse decisions, SQL execution output, validation retries, and runtime repair details. It is now separated into reuse, execution, validation, and runtime state slices in `src/agents/orchestrator/state.py`, while compatibility remains for older imports.

The runtime finalization path now uses a smaller SQL-stage output object instead of treating the full graph state as the final SQL result. This matters because final save/result code only needs terminal SQL fields such as status, SQL path, selected artifacts, candidate SQL, result payload, last error, and trace. It does not need reuse candidates, validation retry instructions, hashlines, or repair counters.

### CLI posture

The CLI should stay minimal:

```bash
tabuflow tabular inspect path/to/file.csv
tabuflow tabular profile path/to/file.xlsx
tabuflow tabular extract path/to/file.csv
tabuflow pdf inspect path/to/file.pdf
tabuflow pdf extract path/to/file.pdf
tabuflow artifacts list
tabuflow artifacts describe artifact_name
tabuflow artifacts query "select * from artifact_name limit 20"
tabuflow artifacts query @query.sql
tabuflow artifacts save-view saved_view_name @query.sql
```

Do not expose storage root or database path as ordinary model-controlled CLI or LangChain tool arguments. Source paths and SQL text are valid user/agent choices; artifact storage configuration is not.

The intended user is not only the custom Tabuflow workbench agent. OpenCode, Pi, Codex, or another coding agent should be able to treat `tabuflow` commands as higher-quality presets for messy tabular/PDF/artifact work, then use their normal edit/run/review loop around the produced files and views.

### LLM-dependent operations

The tools should be able to run without an LLM setup where possible. LLM-dependent behavior should be optional or live in `src/agents`.

Current examples:

- artifact naming can use an LLM-backed namer when configured, but should have a deterministic fallback,
- PDF handling may eventually benefit from an LLM-assisted inspection/OCR/table path, but the baseline tool should still expose extracted text/images and deterministic extraction outputs,
- custom validation/retry behavior belongs in the agent layer, not in a supposedly standalone artifact query function.

### Superseded older notes

Older notes below mention `sql_list`, `sql_describe`, `sql_query`, and a standalone SQL package shape. The useful part of those notes still stands: list targets, inspect schemas, run bounded read-only SQL, and recover from SQLite errors. The module boundary has changed. That work now belongs to `src/tools/artifacts` when it is a general artifact/catalog/query operation, and to `src/agents/query_stage` when it depends on Query Stage SQL history, SQL files, graph state, or custom validation loops.

## Experiment: Unknown CSV Inspection

Goal: treat `examples/gcp/cost_table.csv` as an unknown artifact and inspect it without assuming a fixed schema or a prebuilt pipeline.

### Tools used

1. `file examples/gcp/cost_table.csv`
2. `wc -l examples/gcp/cost_table.csv`
3. `sed -n '1,8p' examples/gcp/cost_table.csv`
4. `sed -n '9,24p' examples/gcp/cost_table.csv`
5. `awk 'NF{print NR ":" $0; count++; if (count==20) exit}' examples/gcp/cost_table.csv`

### Why these tools

- `file`: confirm the basic file type without trusting the extension.
- `wc -l`: estimate file size and record count.
- `sed`: sample the first rows and the next block without loading the whole file.
- `awk`: show line numbers for the first non-empty rows to detect where metadata ends and the real table begins.
- `rg`: useful as a follow-up if we need to search for likely header markers or repeated patterns.

### What we learned from this file

- It is valid CSV text.
- It has `19068` lines.
- Rows `1-8` contain invoice/report metadata rather than the main table.
- The actual tabular header starts at line `9`.
- The table contains quoted fields and sparse values, so a generic parser must rely on CSV parsing rules rather than string splitting.

### Generic takeaway

A generic CSV ingestion path should not assume:

- the first row is the header,
- the file contains only one table-like section,
- every row has the same semantic role,
- commas can be parsed safely with naive `split(",")`.

Instead, the ingestion flow should:

1. detect file type,
2. sample early rows,
3. identify metadata versus tabular rows,
4. detect the true header row,
5. parse with a real CSV parser,
6. load the normalized table into an in-memory database for ad hoc SQL.

### Likely next tool for a generic implementation

Use `uv run python` with the standard library `csv` module first to:

- sniff delimiter and quoting,
- detect the header row,
- split metadata from data rows,
- convert the cleaned table into an in-memory database such as DuckDB or SQLite.

## Observation: Prefer Python Libraries Over Shell Commands

For the actual generic agent design, shell commands should not be the primary implementation layer.

### Why

- Shell tools are helpful for local exploration and debugging.
- Shell availability differs across macOS, Linux distributions, and Docker images.
- Command behavior can vary across BSD, GNU, and BusyBox variants.
- Relying on arbitrary command execution makes permissions and reproducibility harder to control.
- Weaker models are less reliable when they must invent inspection commands or parsing code on the fly.

### Preferred direction

Put the core ingestion and inspection logic in Python libraries, then expose stable higher-level tools to the agent.

Examples:

- CSV parsing and sampling: standard library `csv`
- PDF extraction: `pdfplumber`, `pymupdf`, or `pypdf`
- In-memory SQL: `duckdb`
- File handling: `pathlib`, `mimetypes`, optional stronger file-type helpers

### Agent design implication

The model should not need to know how to manually operate `csv`, shell utilities, or PDF parsers.

Instead, it should call stable tools such as:

- `inspect_csv`
- `inspect_pdf`
- `load_to_memory_db`
- `run_sql`

This pushes the general-case logic into reusable tools and leaves the model responsible mainly for orchestration and interpretation.

### Practical conclusion

Use shell commands as optional operator/debug helpers. Use Python libraries as the main implementation path for any generic CSV/PDF ingestion and in-memory SQL workflow.

## Observation: Extraction Layer vs Read Layer

The tabular tool should be treated as an extraction layer, not a full-fidelity read layer.

### Extraction layer goal

- recover usable table blocks,
- recover non-table blocks as metadata,
- normalize enough for downstream querying,
- stay conservative when the spreadsheet is messy.

### Read layer goal

- preserve original ordering,
- preserve surrounding context,
- preserve the fuller document picture in a markdown-like faithful representation.

### Design implication

The extraction layer does not need to preserve every visual nuance or spreadsheet trick. It should focus on:

- stable table extraction,
- minimal semantic invention,
- preserving weird content as metadata instead of over-normalizing it.

## Observation: No Automatic Header Merging

Automatic header merging caused the most obvious semantic corruption.

### What went wrong when merging headers

- simple CSVs could absorb the first data row into the header,
- summary or prefix rows could leak values into column names,
- spreadsheet rates and month markers could turn into fake schema names such as `7.85 Charge to Customer (HKD)`.

### Current rule

Do not merge header rows together automatically.

Instead:

- keep the detected header row literal,
- keep rows above the table as metadata,
- fill only truly blank column names with stable placeholders such as `row_label` or `column_N`.

### Why this is better

- safer,
- more faithful to source structure,
- less likely to hallucinate schema,
- better aligned with the future read layer keeping full context separately.

## Observation: Box-Based Segmentation Works Better Than Row Density

Wide accountant spreadsheets often have sparse rows that still belong to the same table.

### Problem with row-density continuation

A row can be sparse in isolation but still clearly belong to the surrounding table box. Using a hard row-level non-empty threshold caused valid rows to be dropped in wide monthly matrices.

### Better mental model

Treat the table as a 2D box:

- rows belong if they stay inside the same box,
- sparse cells are allowed if the surrounding rows support the same column bands,
- emptiness inside the box should be treated as holes, not immediate evidence that the row is outside the table.

### Practical effect

This improved behavior on wide spreadsheet matrices such as `examples/gcp/r&c.xlsx`, where later sparse rows should still remain inside one large table region.

## Stress Test Notes

Ran the extraction tool across all real `csv` and `xlsx` files in the repo, excluding virtualenv fixtures.

### Files that now look good

- config CSVs in `data/config/`
- GCP raw/export CSVs in `data/uploads/gcp/`
- `examples/gcp/cost_table.csv`
- `examples/aws/AWS Rev and Cost File.xlsx`
- `examples/gcp/aggregated_cost_table.xlsx`
- `examples/gcp/aggregated_cost_table2.xlsx`
- `examples/gcp/r&c.xlsx`
- `examples/ali/202602/Alibaba Cloud Revenue & Cost Summary 2026 (Simple ver).xlsx`

### Notable improvements

- merged-cell AWS workbook now stays one table instead of collapsing after the first row block,
- side mini-table in `aggregated_cost_table.xlsx` stays isolated as its own table,
- wide GCP monthly matrix in `r&c.xlsx` now remains one table instead of being cut off early,
- simple two-column CSVs no longer absorb their first data row into the header,
- Alibaba summary workbook now keeps summary rows as metadata and the main table starts at the real header row.

### Remaining posture

The current extraction behavior is intentionally conservative:

- preserve literal headers,
- avoid semantic invention,
- tolerate weird spreadsheets,
- keep non-table or uncertain content as metadata blocks.

This is a better fit for accountant-made spreadsheets than trying to force a polished unified schema too early.

## Current State: Tabular Tool Shape

The current tabular surface is now intentionally split into three tools:

- `inspect_tabular`: bounded raw read
- `profile_tabular`: read-only structural summary
- `extract_tabular`: recover table blocks and load them into the shared SQL cache

### Why this split

- `inspect_tabular` should behave like a safe grid reader, not a parser with opinions.
- `profile_tabular` should help the agent understand the file shape without committing to a final schema.
- `extract_tabular` is the first tool allowed to operationalize the file into queryable tables.
- SQL execution should stay explicit and deterministic, but it does not need to live in the same module as file ingestion.

## Current State: Overall Tool Flow

The current end-to-end tool stack is best understood as three layers:

1. inspect and understand a tabular file conservatively,
2. extract recovered tables into the shared SQLite cache,
3. navigate or query that cache directly, or let the SQL agent orchestrate SQL planning.

### Layer 1: Tabular read tools

- `inspect_tabular` is the safest raw window into a file.
- `profile_tabular` gives structural hints such as header candidates, region boxes, row counts, and a fast fingerprint.
- These tools should help the model understand shape without inventing schema.

### Layer 2: Tabular extraction and storage

- `extract_tabular` recovers metadata blocks and table blocks from CSV/XLSX input.
- Recovered tables are loaded into the shared SQLite cache.
- The storage layer keeps catalog metadata such as table names, row counts, source mappings, and content fingerprints.
- This layer is the bridge between messy accountant spreadsheets and deterministic SQL tooling.

### Layer 3: SQL navigation and execution

The SQL helper layer is intentionally standalone from file parsing once data is in SQLite.

- `list_targets` / `sql_list`: list available tables and views.
- `describe_target` / `sql_describe`: inspect schema, sample rows, source mappings, and text-value hints.
- `suggest_targets` / `sql_suggest`: do lightweight target matching from a natural-language question.
- `run_sql` / `sql_query`: run bounded read-only SQL.

Important posture:

- SQL execution stays explicit.
- Queries are bounded.
- Only read-only SQL is allowed.
- The real safety boundary is the read-only SQLite connection, not prompt discipline.

### SQL agent layer

`SQLAgent` is now a thin orchestration layer on top of the standalone SQL helpers rather than a file-ingestion tool.

Its loop is:

1. suggest likely targets,
2. inspect the best few targets,
3. ask the planner LLM for one read-only SQL query,
4. execute the query,
5. optionally repair once or twice if SQLite returns an error.

### New repair behavior

The SQL layer now includes deterministic repair hints for common SQLite failures.

Examples:

- missing column -> suggest nearby inspected column names,
- missing table/view -> suggest nearby available targets,
- ambiguous column -> suggest which inspected targets need qualification.

This gives the agent a tighter recovery path when the model hallucinates a schema detail or uses the wrong identifier.

### Mental model to preserve

- tabular tools answer: what is in this file,
- extraction answers: what recoverable tables should enter the cache,
- SQL tools answer: what targets exist and how can they be queried safely,
- SQL agent answers: how do we turn a user question into one safe SQL query with minimal repair.

### What has been implemented

- The shared tabular cache now lives at `data/tabular.sqlite`.
- The raw extracted table content is stored in SQLite tables such as `content_<hash-prefix>`.
- Internal mapping tables track content identity and source linkage: - `_tabular_contents` - `_tabular_sources`
- `extract_tabular` computes: - a `fast_fingerprint` for cheap routing/cache hints - a `content_id` for exact table-content identity
- extracted tables are loaded into a shared SQLite cache that can be queried by a separate SQL tool layer.

### Why SQLite replaced DuckDB

DuckDB was fine for SQL execution, but local editor/database-extension ergonomics were worse than expected. For this stage, SQLite gives a simpler local browsing experience while still being perfectly adequate for:

- raw extracted tables,
- helper views,
- ad hoc SQL over accountant-style CSV/XLSX outputs.

This does not change the read/extract/query separation. It only changes the storage backend.

## Current State: GCP Cost Table Example

The GCP cost-table example now works end to end on top of the generic tabular tools.

### Raw extracted table

- Source: `examples/gcp/cost_table.csv`
- Shared DB: `data/tabular.sqlite`
- Loaded raw table: `content_23ac1d333f4101ab`
- Extracted row count: `19057`

The two footer rows, `Rounding error` and `Total`, are intentionally excluded from the extracted table body.

### API-style SQLite views

The helper script `test_sqlite_gcp_views.py` materializes stable SQLite views that mirror the current API-side naming:

- `gcp_raw_invoice_header`
- `gcp_raw_cost_item`
- `gcp_rule_account_category`
- `gcp_rule_special_discount`
- `gcp_cost_item_typed_view`
- `gcp_account_payload_view`
- `gcp_category_payload_view`
- `gcp_summary_payload_view`

This keeps the generic extraction layer generic while still letting us reproduce the current GCP billing payload shape in SQL.

### Why this matters

This confirms the generic tools are already sufficient to support the existing GCP billing flow:

1. inspect/profile to locate metadata and header rows
2. extract to load the raw cost items and rule CSVs
3. SQL views to recreate typed/account/category/summary outputs

## Observation: NL -> SQL Toolkits Are Mostly Schema + Prompt + SQL

The LlamaIndex and LangChain SQL integrations are useful, but the core mechanism is simpler than the marketing can make it sound.

### What they mainly do

1. inspect database schema and visible tables/views
2. build a schema/context prompt
3. ask an LLM to generate SQL
4. optionally run a query-check step
5. execute SQL
6. optionally synthesize a natural-language answer from the SQL result

### LlamaIndex takeaway

`NLSQLTableQueryEngine` / `NLSQLRetriever` mainly:

- gather table schema context,
- prompt the model with schema + question + SQL dialect,
- parse a SQL string out of the model response,
- execute it,
- optionally synthesize a prose answer afterward.

It is not a magical symbolic planner. The quality mostly comes from:

- how clean the exposed schema is,
- how restricted the visible tables are,
- how good the prompting/model is.

### LangChain takeaway

The SQL toolkit is similarly pragmatic. The useful pieces are mostly:

- `sql_db_list_tables`
- `sql_db_schema`
- `sql_db_query`
- `sql_db_query_checker`

This is effectively a tool bundle for:

- discover schema,
- inspect tables,
- generate/check SQL,
- execute SQL.

### Design implication for this repo

If we add NL -> SQL later, the safest posture is:

- expose curated views, not raw `content_<hash>` tables,
- keep query execution deterministic,
- prefer returning raw SQL results over agent-written prose when possible,
- treat the model as a SQL generator/compiler rather than a final answering layer.

That is a better match for billing data, where auditability matters more than conversational polish.

## Observation: The Important Difference Is Grounding Strategy, Not SQL Generation

After reading the docs and source more closely, the meaningful difference between LangChain and LlamaIndex is not "both can do NL to SQL." Both can. The more important question is how they ground the model before SQL generation.

### LangChain: tool loop around a shared schema dump

LangChain's SQL setup is operationally clean but conceptually simple.

The toolkit is mostly four tools:

- `sql_db_list_tables`
- `sql_db_schema`
- `sql_db_query_checker`
- `sql_db_query`

The key implementation details:

- `SQLDatabase.get_table_info()` builds prompt context from real `CREATE TABLE` DDL, not just column-name summaries.
- It can append sample rows and indexes to that schema text.
- `SQLDatabase.get_context()` exposes two prompt-friendly fields: `table_names` and `table_info`.
- `run_no_throw()` and `get_table_info_no_throw()` return formatted error strings instead of raising, which keeps the agent loop simple.
- `QuerySQLCheckerTool` is a narrow LLM pass for common SQL mistakes such as `NOT IN` with `NULL`, wrong join columns, quoting mistakes, and type mismatches.

What is actually special here is not some deep SQL reasoning layer. It is a robust agent tool loop:

1. discover tables,
2. inspect schema plus samples,
3. optionally check the query,
4. execute and retry on error.

This is good when the schema is small enough that a shared schema dump is acceptable. It is less interesting when the main problem is selecting the right subset of schema or surfacing the right values.

### LlamaIndex: retrieve schema and values before asking for SQL

LlamaIndex is more notable in how it narrows context.

The important piece is `NLSQLRetriever`, not just the query-engine wrapper. Its design allows the system to retrieve the most relevant context per question before generating SQL.

The key implementation details:

- tables can be fixed up front or selected dynamically through a `table_retriever`,
- `SQLTableNodeMapping` turns each table schema plus optional human-authored context into retrievable nodes,
- `rows_retrievers` can inject relevant example rows into the prompt,
- `cols_retrievers` can inject relevant values from specific text columns,
- `handle_sql_errors` can convert SQL failures into returned error nodes instead of hard failures,
- `sql_only` supports a debugging posture where the model emits SQL without execution,
- parser modes include a `PGVECTOR` path that replaces `[query_vector]` placeholders with a real embedding.

This is the genuinely interesting part. LlamaIndex is not just "generate SQL from schema." It is closer to:

1. retrieve the right table schema for this question,
2. retrieve example rows or values that disambiguate business language,
3. then generate SQL from a smaller, more relevant context.

That is a much better fit for wide or semantically messy data.

### Why that matters for billing spreadsheets

Our extracted tables are likely to be:

- wide,
- sparsely documented,
- full of business labels,
- vulnerable to ambiguous value matching.

In that setting, the most useful ideas are not:

- a generic SQL checker,
- or a polished natural-language answer synthesizer.

The more important ideas are:

- dynamic table selection,
- schema text with real example values,
- row/value retrieval for disambiguation,
- explicit `sql_only` mode for inspection and debugging.

### Updated takeaway for this repo

If we borrow from these libraries later, the highest-value combination is probably:

- LangChain's explicit inspect/check/query tool separation,
- LangChain's safe error-string execution style,
- LlamaIndex's query-time schema selection,
- LlamaIndex's retrieval of relevant rows and relevant text-column values.

In short:

- LangChain's strongest contribution is the agent-facing workflow.
- LlamaIndex's strongest contribution is retrieval-augmented grounding.

For accountant-made billing tables, the second one feels more structurally important.

### References

- LangChain SQL toolkit docs: <https://docs.langchain.com/oss/python/integrations/tools/sql_database>
- LangChain toolkit source: <https://github.com/langchain-ai/langchain-community/blob/main/libs/community/langchain_community/agent_toolkits/sql/toolkit.py>
- LangChain SQL tools source: <https://github.com/langchain-ai/langchain-community/blob/main/libs/community/langchain_community/tools/sql_database/tool.py>
- LangChain SQL database utility source: <https://github.com/langchain-ai/langchain-community/blob/main/libs/community/langchain_community/utilities/sql_database.py>
- LlamaIndex structured data docs: <https://developers.llamaindex.ai/python/framework/understanding/putting_it_all_together/structured_data/>
- LlamaIndex SQL query engine source: <https://github.com/run-llama/llama_index/blob/main/llama-index-core/llama_index/core/indices/struct_store/sql_query.py>
- LlamaIndex SQL retriever source: <https://github.com/run-llama/llama_index/blob/main/llama-index-core/llama_index/core/indices/struct_store/sql_retriever.py>
- LlamaIndex table node mapping source: <https://github.com/run-llama/llama_index/blob/main/llama-index-core/llama_index/core/objects/table_node_mapping.py>

## Experiment: Explicit SQL Trials Against The Shared Tabular Cache

Goal: keep using the current note file to evaluate the query surface even before any NL -> SQL layer is implemented.

### What was available

The shared SQLite cache at `data/tabular.sqlite` already contains:

- raw content tables such as `content_23ac1d333f4101ab`
- linkage tables `_tabular_contents` and `_tabular_sources`
- stable GCP helper views such as:
  - `gcp_raw_cost_item`
  - `gcp_cost_item_typed_view`
  - `gcp_account_payload_view`
  - `gcp_summary_payload_view`

### Queries tried

Worked:

```sql
select * from gcp_summary_payload_view limit 5;
```

Returned one aggregated row for `examples/gcp/cost_table.csv` with:

- `gcp_total_cost = 632889.976284`
- `gcp_net_cost = 595825.436286`
- `customer_charge = 601927.8467164`
- `item_count = 19057`

Worked:

```sql
select
  service_description,
  round(sum(unrounded_cost_usd), 2) as total_unrounded_cost_usd,
  count(*) as item_count
from gcp_cost_item_typed_view
group by service_description
order by total_unrounded_cost_usd desc
limit 5;
```

Top rows:

- `Cloud Storage | 145281.85 | 1778`
- `Compute Engine | 118017.16 | 7504`
- `BigQuery | 76026.38 | 776`
- `Support | 41232.62 | 14`
- `Cloud Composer | 24937.27 | 231`

Worked:

```sql
select
  account_id,
  account_name,
  round(customer_charge_hkd, 2) as charge_hkd,
  round(gross_profit, 2) as gross_profit
from gcp_account_payload_view
order by customer_charge_hkd desc
limit 5;
```

Failed first:

```sql
select service_description, round(sum(gcp_net_cost), 2)
from gcp_cost_item_typed_view
group by service_description;
```

Error:

- `no such column: gcp_net_cost`

Recovered by checking the view schema and using `unrounded_cost_usd` instead.

Failed first:

```sql
select billing_account_id, category, round(sum(gcp_net_cost), 2)
from gcp_account_payload_view
group by billing_account_id, category;
```

Error:

- `no such column: billing_account_id`

Recovered by checking the view schema and using `account_id`.

### What this shows about the future query tool

The query layer is already useful with explicit SQL, but the success pattern is clear:

1. know the stable view names,
2. inspect the target schema,
3. run SQL,
4. recover from column-name mismatches quickly.

The main friction is not SQL execution itself. It is schema navigation.

### Design implication

If the SQL query tool remains a raw SQL executor, it should be paired with an easy schema-discovery path. At minimum, the surrounding workflow needs:

- a reliable way to list available tables/views,
- a reliable way to inspect the schema of one chosen table/view,
- fast recovery when a query references a plausible but wrong column name.

This strengthens the earlier conclusion:

- stable curated views are the right abstraction boundary,
- raw `content_<hash>` tables are useful storage artifacts but poor user-facing query targets,
- the eventual NL or agent layer should bias strongly toward curated view names and schema-aware retries.

### Concrete takeaway

Even before NL -> SQL exists, the current split is already productive:

- `extract_tabular` loads reproducible SQL targets,
- the SQL layer can stay a deterministic SQL runner,
- the next missing piece is likely a lightweight schema-navigation helper, not a heavier answer-synthesis layer.

## Current State: First Query-Tool Development Slice

Started implementation on the query side without attempting a full LangChain or LlamaIndex clone.

### Added tools

- `sql_list`
- `sql_describe`
- `sql_query`

### Code placement

The query-side implementation now lives in a standalone SQL package:

- `llm-harness/llm_harness/tools/sql/query.py`
- `llm-harness/llm_harness/tools/sql/tools.py`

This keeps the split cleaner:

- `storage.py`: content loading, catalog registration, typed-view creation, fingerprints
- `tabular/tools.py`: file inspection, profiling, and extraction only
- `sql/query.py`: SQL execution plus table/view listing and schema description
- `sql/tools.py`: public LangChain tool wiring for the standalone SQL layer

### Why these first

The earlier experiments showed that the main pain point was not SQL execution. It was:

1. finding the right stable view/table name,
2. seeing the actual available columns,
3. recovering quickly from plausible-but-wrong column names.

These SQL tools address that directly:

- `sql_list` lists tables/views from the shared SQLite cache and labels raw content tables versus typed views versus internal catalog tables,
- `sql_describe` returns column info, the SQL definition, row-count/catalog metadata when available, and source mappings for extracted content,
- `sql_query` runs bounded read-only SQL against the same cache without being coupled to file ingestion behavior.

### Design takeaway

This reinforces the idea that we do not need to reimplement entire upstream frameworks.

The highest-value path is to build a narrow local layer that keeps:

- deterministic SQLite execution,
- stable curated views,
- explicit schema navigation,
- optional higher-level query planning later.

If we later add an agentic or NL -> SQL layer, it can sit on top of these primitives instead of replacing them.

## Second Source Pass: What Is Still Worth Stealing

After another pass through the current LangChain and LlamaIndex SQL docs/source, the useful ideas are still fairly narrow.

### LangChain: the useful parts are operational, not magical

The current LangChain SQL toolkit is still basically a four-step loop:

1. list tables,
2. inspect schema plus sample rows,
3. run SQL,
4. optionally run an LLM query checker before execution.

The more important details are in `SQLDatabase`, not the agent wrapper:

- `sample_rows_in_table_info`
- `indexes_in_table_info`
- `custom_table_info`
- `include_tables` / `ignore_tables`
- `view_support`
- `lazy_table_reflection`
- `get_table_info_no_throw`
- `run_no_throw`

This means the most useful LangChain ideas for this repo are:

- schema inspection should include a few example rows, not just column names,
- table/view metadata should allow custom business context,
- the inspect and run steps should fail as data, not as exceptions,
- the query surface should be able to hide irrelevant targets.

The LLM query checker is the least compelling part to copy directly. It helps generic agents, but it adds another model call while still depending on the model to spot SQL mistakes. For this repo, a deterministic repair path around SQLite errors and schema inspection likely gives better value first.

### LlamaIndex: the useful part is query-time grounding

The strongest LlamaIndex idea remains query-time retrieval around SQL generation.

The current `SQLTableRetrieverQueryEngine` still centers on:

- `table_retriever`
- `rows_retrievers`
- `cols_retrievers`
- `context_str_prefix`
- `sql_only`

The practical meaning is:

- retrieve the right table schemas first,
- retrieve a few semantically relevant rows for grounding,
- retrieve relevant distinct text-column values for categorical filters and fuzzy names,
- optionally stop at SQL generation without executing it.

That is more important than the top-level query-engine wrapper itself. For messy billing exports, picking the right target and the right values is often harder than writing the final SQL.

### Improvement implications for our local SQL layer

Based on those upstream patterns, the next improvements with the best cost/value ratio look like:

1. Add sample-row context to `sql_describe`. Not full tables, just a few rows and maybe distinct examples for text-heavy columns.

2. Add optional custom target context. Business descriptions, join hints, metric units, and "prefer this curated view for X" notes.

3. Add a target-suggestion helper. Input: natural-language question. Output: likely tables/views plus why. This is the lightest version of LlamaIndex's table retriever idea.

4. Add lightweight value-grounding helpers. For example: suggest matching account IDs, customer names, project names, SKUs, or regions from actual column values.

5. Add `sql_plan` or `sql_only` mode. Return candidate SQL without executing it. Useful for inspection, review, and safer multi-step agent loops.

6. Add deterministic query-repair helpers. On missing table/column errors, suggest nearby targets/columns from the schema instead of relying on an LLM checker.

7. Add target allowlists / preferred surfaces. Bias toward curated summary views and keep raw `content_*` tables de-emphasized.

### What still does not look worth copying yet

- a full LangChain toolkit clone,
- a full LlamaIndex retriever/query-engine stack,
- automatic response synthesis over SQL results,
- an LLM query checker as a default mandatory step.

For this repo, the upstream lesson is still:

- borrow LangChain's explicit inspect -> query loop,
- borrow LlamaIndex's grounding and narrowing ideas,
- keep the local implementation thin and deterministic.

## Historical State: GCP CSV To One Result View

Superseded on `2026-05-20`. The current agent-facing GCP contract is the outcome-first skill in `skills/gcp-cost-pipeline/` and `.agents/skills/gcp-cost-pipeline/`. It targets one monthly cost table input, an aggregated reconciliation output, and IBS charge-item upload rows.

Older notes in this section used a single saved SQLite view as the main artifact. Do not use that older saved-view path as the current GCP target.

### Workbench UI Boundary

The app default should remain generic. Do not bake GCP-specific result columns into backend defaults.

Current default bootstrap SQL is intentionally neutral:

```sql
SELECT
  'ready' AS status,
  'Select a source, table, or saved result to inspect.' AS message;
```

The browser should display relative/source-safe paths such as `examples/gcp/cost_table.csv` and `data/tabular.sqlite`, not absolute local paths.

The API hides auto-generated `typed_content_view` targets from the browser target list so raw tables do not appear to have duplicate view versions. The saved `analysis_result` is the one user-facing view.

### Verification Used

- `uv run ruff check .`
- `pnpm --dir frontend exec tsc --noEmit`
- `pnpm build`
- direct `_bootstrap_payload(default_database_path())` check
- live `http://localhost:5174/api/bootstrap` check

## Current State: Repo Skills As Outcome Contracts

Date: `2026-05-21`.

The latest skill work shifted the repo-level skill strategy away from "tell the agent which tools and commands to use" and toward "tell the agent what result must exist, what source artifacts count, and how to tell whether the result is good."

### Skill placement

The official repo-level skill surface is `.agents/skills/`. The `skills/` directory is still preserved as a mirrored repo copy where useful, but the active session loads from `.agents/skills/`.

`.agents/skills/AGENTS.md` should stay small. It is a context/router note for the current experiment, not a generic tool manual and not a place for command transcripts. It should explain what tasks are being explored, which domain skills exist, and what process boundaries matter.

### Skill-evolution method

The useful loop is:

1. inspect real source artifacts and any reference outputs,
2. define required inputs and outputs,
3. write the smallest durable skill contract,
4. pressure-test it in a fresh or weaker isolated agent session,
5. inspect the produced files yourself,
6. revise the skill only for failures that should generalize.

Do not call a skill good because the main session solved the task manually. The proof is whether another session with the skill can find the right target, produce the right artifact shape, and report gaps honestly.

The reusable version of this method now lives in `skill-evolution-loop`.

### GCP skill boundary

The GCP skill now treats the raw monthly `cost_table.xlsx` as the only required user input. Reference workbooks can explain shape and business intent, but they are not default runtime inputs.

The required outputs are:

- an aggregated GCP reconciliation result,
- an IBS charge-item upload result.

The raw GCP column names are fixed but messy. The skill should tell agents to normalize those exact ingestion labels into semantic fields before grouping or deriving formulas. It should not teach agents to infer business meaning from pivot labels, generated table names, or one month's copied examples.

The IBS output needs closer babysitting than the aggregated result because it is a fixed upload template. The skill should preserve template column order, constants, date shape, total row behavior, and maintained defaults/mappings where raw source data does not contain IBS-only fields.

Do not bundle a hardcoded GCP script just because one working script exists. For now the GCP skill should preserve the contract and validation bar while leaving implementation freedom.

### AWS skill boundary

The AWS examples are mostly PDFs, and the minimum useful result is coherent tabular data extracted from the invoices. The current AWS skill therefore starts with PDF table extraction rather than pretending the final accounting output is already known.

Direct text extraction is the first pass because many AWS invoices are text PDFs. The helper script `extract_aws_pdf_text_tables.py` gives the agent a deterministic way to produce per-PDF CSV/JSON table rows and flag pages that need OCR. OCR/visual extraction should be reserved for pages with no text, incomplete text extraction, or ambiguous layout.

Emails in AWS example folders are reference context only. They can explain reporting, approval, forwarding, account IDs, periods, or attachments, but they are not billing-table truth and should not be emitted as CSV/table outputs unless the user explicitly asks for an email reconciliation dataset.

### Markdown style note

Repo skill docs and observation notes should not hard-wrap ordinary prose. Keep prose paragraphs and bullet text on one line unless a real Markdown structure, table, code fence, or nested list needs multiple lines. This avoids making future diffs noisy and keeps skill edits easier to review.
