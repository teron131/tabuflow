# Artifact Workspace Tools Plan

> **For implementation sessions:** Treat this as the current design source of truth. This thread is for discussion; implementation should happen in a separate session and update this checklist as tasks land.

**Goal:** Make the mixed `artifacts/` workspace easy for agents and humans to navigate, search, manage, query, and persist without forcing callers to know whether a result came from SQLite or the filesystem.

**Core idea:** `map` is the relationship overview. `search` is the retriever. `add` and `remove` are risk-scoped management. `query` and `save-view` remain the SQL execution and persistence tools for now.

---

## First-Pass Tool Surface

Keep the artifact tool surface small:

```text
artifacts map
artifacts search <query>
artifacts add <path>
artifacts remove <artifact-id>
artifacts query <sql-or-@file>
artifacts save-view <view-name> <sql-or-@file>
```

Compatibility and cleanup:

- Keep `artifacts query` and `artifacts save-view` as-is for the first pass.
- Keep `artifacts from-source` temporarily as a compatibility shortcut.
- Keep `artifacts suggest` available only as a temporary lightweight finder; replace it with the richer `artifacts search` direction.
- Do not add a separate `grep` tool; use `artifacts search --scope rows|files|metadata|all`.
- Do not add `artifacts profile` yet. Search results should include enough bounded context to avoid a search -> profile-one-by-one loop.
- Do not implement `artifacts sql ...` yet. Leave that namespace for future cleanup.
- Do not generalize non-JSON/human-readable output formats in this pass. Keep `map`'s existing pretty CLI trace as a narrow exception, and make new `search`, `add`, and `remove` commands JSON payloads first.

## Mental Model

`artifacts/` is a mixed workspace:

```text
artifacts/
  tabular.sqlite       # queryable tables/views and relationship metadata
  sql/                 # reusable SQL files
  outputs/             # produced deliverables
  pdf/                 # PDF workspaces, manifests, text, extracted CSV drafts
```

Clean layers:

- **SQLite layer:** extracted raw tables, typed views, saved views, source lineage, fingerprints, relationship metadata.
- **Filesystem layer:** SQL files, output files, PDF workspaces, PDF text, manifests, CSV table drafts.
- **Adapter layer:** uses SQLite for DB-backed artifacts and `rg` for text files.
- **Normalization layer:** returns one stable result shape with backend-native match details plus bounded artifact context.

## `artifacts map`

Purpose: overview relationship map, not retrieval.

Default CLI output should be a compact, pretty trace tree. It should read like a lineage chain, with compact `rg`-style workspace-relative paths:

```text
FROM input_file examples/gcp/cost_table.csv
  INSIDE pdf_workspace artifacts/pdf/gcp_cost_pdf_workspace
  EXTRACTED table artifacts/tabular.sqlite:cost_table_typed
    APPLIED sql_file artifacts/sql/gcp_cost_table_preview.sql
      DERIVED result_file artifacts/outputs/gcp_cost_table_preview.csv
```

Rules:

- Prefer typed views over raw tables in the default map.
- Hardcode workspace discovery to the current project root and `artifacts/tabular.sqlite`; do not expose root/database path args.
- Always include managed file artifacts. Do not expose an `include_files` option.
- Show only traceable filepaths and table references in the default output.
- Link SQL files to tables by referenced table/view names.
- Link result files to SQL files by managed artifact stem.
- Link PDF workspaces to input files through their manifests.
- Prefer compact workspace-relative paths; keep absolute paths only when a source lives outside the workspace.
- Keep unlinked managed files visible under `UNLINKED` so files do not disappear just because the relationship cannot be inferred yet.
- Return a normal `status: ok` payload with `database_path` for tool/MCP callers.
- Treat unreadable managed SQL files or malformed PDF manifests as non-fatal diagnostics. The artifact should remain visible under `UNLINKED` and the rest of the map should still render.
- Keep the Python API structured for tool/MCP reuse, but let the CLI print the pretty representation on success. Error payloads can remain JSON-shaped.

## `artifacts search`

Purpose: retriever.

Expected UX:

```bash
tabuflow artifacts search "Cost ($)"
tabuflow artifacts search "HKT-IAD"
tabuflow artifacts search "HKT.*IAD" --regex
tabuflow artifacts search "billing cost" --scope files
tabuflow artifacts search "HKT-IAD" --scope rows --artifact cost_table_typed
```

Public defaults:

- literal search by default
- regex only with `--regex`
- `--scope all` by default
- bounded `--max-matches`
- case-insensitive or smart-case matching

Backend behavior:

- Filesystem text uses `rg --json`.
- Literal file search uses `rg -F --json`.
- Regex file search uses `rg --json -e`.
- SQLite metadata search uses generated read-only SQL with quoted identifiers and bound parameters.
- SQLite row search uses bounded SQL predicates across selected text-ish columns.
- SQLite regex mode registers a local Python `REGEXP` function on the search connection.

Search result shape should preserve the backend-native match while adding artifact context:

```json
{
  "type": "sqlite_view",
  "id": "cost_table_typed",
  "backend": "sqlite",
  "match": {
    "kind": "row_value",
    "column": "Billing account name",
    "row_number": 42,
    "value": "HKT-IAD - pccw.com - 1"
  },
  "context": {
    "source": "examples/gcp/cost_table.csv",
    "rows": 19057,
    "columns": ["Billing account name", "Project name", "Cost ($)"]
  },
  "next": {
    "query_hint": "SELECT * FROM \"cost_table_typed\" LIMIT 20;"
  }
}
```

For file hits:

```json
{
  "type": "sql_file",
  "id": "artifacts/sql/gcp_summary.sql",
  "backend": "rg",
  "match": {
    "path": "artifacts/sql/gcp_summary.sql",
    "line": 12,
    "text": "FROM cost_table_typed"
  },
  "context": {
    "references": ["cost_table_typed"]
  },
  "next": {
    "run": "artifacts query @artifacts/sql/gcp_summary.sql"
  }
}
```

Ranking should prefer:

- exact source path or filename matches
- typed views over raw tables
- direct source relationships over loose text matches
- column-name matches over random row-value matches
- saved views over scratch SQL only when current and dependency-linked
- current relationship metadata over stale or orphaned artifacts
- clear row count and table shape in table/view results

No semantic search in the first pass. Add FTS5 or semantic/hybrid search only after lexical search fails on real workflows.

## `artifacts add`

Purpose: risk-scoped import/register operation.

First-pass behavior:

- CSV/XLS/XLSX: delegate to `tabular extract` / `extract_tabular_source`, then return created/reused artifacts and next hints.
- SQL files under `artifacts/sql`: register as managed SQL file if needed.
- Output files under `artifacts/outputs`: register as managed output file if needed.
- PDF workspaces under `artifacts/pdf`: register existing managed workspace metadata if needed.

Do not silently move arbitrary files around in the first pass. If a path is outside managed artifact directories and not a tabular source, return a clear unsupported-type response.

## `artifacts remove`

Purpose: risk-scoped cleanup operation.

Rules:

- Dry-run by default, or require `--yes` for destructive removal.
- Refuse ambiguous IDs.
- Never delete original source files by default.
- Only remove managed file artifacts inside known artifact directories.
- Saved views can be removed when unambiguous.
- Raw table/fingerprint removal should wait until dependency checks are solid.
- If a typed view/raw table/saved view depends on an artifact, show blockers in dry-run output.

First useful remove targets:

- saved view
- managed SQL file
- managed output file
- managed PDF workspace

SQLite content deletion can come later if dependency handling is not simple enough in the first implementation pass.

## SQL Direction For Later

Leave a dedicated namespace for the future:

```text
artifacts sql list
artifacts sql profile <file>
artifacts sql run <file-or-inline-sql>
artifacts sql save-view <view-name> <file-or-inline-sql>
```

Do not implement this namespace now. Current `query` and `save-view` are good enough while `map`, `search`, `add`, and `remove` settle.

## Output Formatting Direction For Later

Human-readable/non-JSON command output should become a cross-tool formatting pass later, not part of the first `search` / `add` / `remove` implementation.

Rough future direction:

- Keep every tool's Python/MCP boundary structured JSON.
- Add CLI-level output modes consistently across tools, such as `--format json|text` or similar.
- Let command-specific text output exist only after the JSON payload shape is stable.
- Treat the current `artifacts map` pretty trace as a useful special case, not the template for all new commands yet.

## Implementation Tasks

### Task 1: Map

**Files:**
- Create: `src/tabuflow/artifacts/map.py`
- Modify: `src/tabuflow/artifacts/schemas.py`
- Modify: `src/tabuflow/artifacts/__init__.py`
- Modify: `src/tabuflow/cli/commands/artifacts.py`
- Modify: `src/tabuflow/cli/main.py`

- [x] Add a schema/dumper for artifact map results.
- [x] Implement `map_artifacts(include_internal=False)` with hardcoded current workspace artifact paths.
- [x] Build trace entries from existing catalog source metadata.
- [x] Prefer typed views over raw tables by default.
- [x] Always include managed SQL files, outputs, and PDF workspaces in the map or `UNLINKED`.
- [x] Link SQL files to referenced tables/views.
- [x] Link SQL result files to SQL files by matching managed stems.
- [x] Link PDF workspaces to input files from `manifest.json`.
- [x] Add compact semantic pretty output for the CLI.
- [x] Return consistent `status: ok` and `database_path` fields for successful map payloads.
- [x] Keep map generation resilient when a managed SQL file or PDF manifest cannot be read.
- [x] Include non-fatal map diagnostics in structured payloads and CLI pretty output.
- [x] Refactor overdefined wrappers/helpers out of the implementation.
- [x] Verify with the actual `uv run tabuflow artifacts map` command and demo artifacts.

### Task 2: Search

**Files:**
- Create: `src/tabuflow/artifacts/search.py`
- Modify: `src/tabuflow/artifacts/schemas.py`
- Modify: `src/tabuflow/artifacts/__init__.py`
- Test: `test_artifacts_workspace_tools.py`

- [ ] Implement SQLite metadata search.
- [ ] Implement bounded SQLite row-value search.
- [ ] Implement filesystem text search through `rg --json`.
- [ ] Add bounded Python UTF-8 fallback if `rg` is unavailable.
- [ ] Support `--scope metadata|rows|files|all`.
- [ ] Support literal default and explicit `--regex`.
- [ ] Return backend-native match details plus normalized artifact context.
- [ ] Add tests for one search returning metadata, row-value, and SQL-file matches.

### Task 3: Add

**Files:**
- Create: `src/tabuflow/artifacts/management.py`
- Modify: `src/tabuflow/artifacts/schemas.py`
- Modify: `src/tabuflow/artifacts/__init__.py`
- Test: `test_artifacts_workspace_tools.py`

- [ ] Implement `add_artifact(path, *, root_dir=None, artifact_type="auto")`.
- [ ] Delegate CSV/XLS/XLSX to `extract_tabular_source`.
- [ ] Return created/reused artifacts with next hints.
- [ ] For first pass, return explicit unsupported responses for file types that are not safely managed yet.
- [ ] Add tests for tabular add and unsupported-path behavior.

### Task 4: Remove

**Files:**
- Modify: `src/tabuflow/artifacts/management.py`
- Modify: `src/tabuflow/artifacts/schemas.py`
- Test: `test_artifacts_workspace_tools.py`

- [ ] Implement `remove_artifact(artifact_id, *, root_dir=None, dry_run=True, yes=False)`.
- [ ] Implement dry-run output with planned actions and blockers.
- [ ] Support saved-view removal when unambiguous.
- [ ] Support managed SQL/output/PDF file removal only inside known artifact directories.
- [ ] Refuse raw table/fingerprint deletion until dependency checks are safe.
- [ ] Add tests for dry-run default, ambiguous ID refusal, and managed-file scope checks.

### Task 5: CLI And MCP Wiring

**Files:**
- Modify: `src/tabuflow/cli/commands/artifacts.py`
- Modify: `src/tabuflow/mcp/server.py`
- Test: `test_artifacts_workspace_tools.py`

- [x] Add CLI command for `map`.
- [ ] Add CLI commands for `search`, `add`, and `remove`.
- [ ] Add MCP tools for `artifacts_map`, `artifacts_search`, `artifacts_add`, and `artifacts_remove`.
- [ ] Keep `query`, `save-view`, and temporary `from-source` working.
- [ ] Remove or de-emphasize `suggest` from public docs/tool guidance once search replaces it.
- [x] Let `artifacts map` print pretty output on success while keeping JSON errors.
- [ ] Keep first-pass `search`, `add`, and `remove` CLI outputs structured JSON; do not add custom text renderers for them yet.
- [ ] Add smoke checks for CLI behavior once `search`, `add`, and `remove` exist.

### Task 6: Guidance And Docs

**Files:**
- Modify: `README.md`
- Modify: `OBSERVE.md`
- Modify: `src/backend/agents/orchestrator/orchestrator.py`
- Modify: `src/backend/agents/query_stage/prompts.py`

- [ ] Document map as the overview tool.
- [ ] Document search as the retriever over SQLite and filesystem artifacts.
- [ ] Document add/remove as scoped management operations.
- [ ] Document query/save-view as the current SQL execution and persistence tools.
- [ ] Remove `suggest` from recommended workflows.

### Task 7: Verification

- [x] Run focused map lint: `uv run ruff check src/tabuflow/artifacts/map.py src/tabuflow/artifacts/schemas.py src/tabuflow/artifacts/__init__.py src/tabuflow/cli/main.py src/tabuflow/cli/commands/artifacts.py`.
- [x] Run focused map formatting check: `uv run ruff format --check src/tabuflow/artifacts/map.py src/tabuflow/artifacts/schemas.py src/tabuflow/artifacts/__init__.py src/tabuflow/cli/main.py src/tabuflow/cli/commands/artifacts.py`.
- [x] Run focused compile check: `uv run python -m py_compile src/tabuflow/artifacts/map.py src/tabuflow/artifacts/schemas.py src/tabuflow/artifacts/__init__.py src/tabuflow/cli/main.py src/tabuflow/cli/commands/artifacts.py`.
- [x] Run whitespace check: `git diff --check`.
- [x] Run actual map smoke check: `uv run tabuflow artifacts map`.
- [x] Run direct map diagnostic probe for an unreadable managed SQL file.
- [ ] Run `uv run ruff check src/tabuflow/artifacts src/tabuflow/cli src/tabuflow/mcp src/backend/agents --fix`.
- [ ] Run `uv run ruff format src/tabuflow/artifacts src/tabuflow/cli src/tabuflow/mcp src/backend/agents test_artifacts_workspace_tools.py`.
- [ ] Run `uv run python -m pytest test_artifacts_workspace_tools.py`.
- [ ] Run manual smoke checks:

```bash
uv run tabuflow artifacts map
uv run tabuflow artifacts search "billing cost"
uv run tabuflow artifacts search "HKT-IAD" --scope rows --max-matches 5
uv run tabuflow artifacts add examples/gcp/cost_table.csv
uv run tabuflow artifacts remove cost_table_typed --dry-run
```

Expected: `artifacts map` prints the pretty trace on success. Other commands should keep JSON payloads with `status`, bounded results, and clear next actions or blockers.

## Non-Goals

- No semantic search in the first pass.
- No SQLite sidecar text index in the first pass.
- No separate `grep` command.
- No mandatory `profile` step.
- No `artifacts sql ...` namespace yet.
- No generalized non-JSON/text output formatting pass yet.
- No silent deletion of source files.
- No broad destructive cleanup command.
