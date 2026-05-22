# Tabuflow

Tabuflow is a local workbench for coding-style data analysis over messy business files.

The core idea is simple: use robust tools to turn CSV, XLS, XLSX, PDF, and email reference files into inspectable artifacts, then let a coding agent, script, SQL file, or workbench flow do the actual reasoning and output work.

Tabuflow should not become a giant custom agent. The useful part is the tool layer.

## Core Boundary

`src/tools` is the reusable layer. It should expose ordinary Python functions and CLI commands that work without LangGraph state, chat messages, or LangChain tool-call transcripts.

`src/agents` is the custom Tabuflow agent layer. It can orchestrate multi-step workbench flows, validation, trace messages, SQL reuse, and fixer behavior, but it should not define the shape of the reusable tools.

LangChain is an adapter, not the foundation.

```mermaid
flowchart LR
    AnyAgent[Any Coding Agent] --> CLI[tabuflow CLI]
    AnyAgent --> Python[Python APIs]
    Workbench[Workbench UI] --> Agent[src/agents]
    Agent --> Tools[src/tools]
    CLI --> Tools
    Python --> Tools
    Tools --> Artifacts[SQLite artifacts and output files]
```

## Principles

- Inspect before extracting.
- Preserve source lineage.
- Keep extraction conservative and reviewable.
- Prefer explicit SQL/Python recipes over hidden workbook formulas or agent-only state.
- Keep generated artifact names out of business logic; rediscover outputs through catalog/source metadata.
- Use Tabuflow tools only where they beat ordinary shell/read/edit work.
- Keep domain skills outcome-first: inputs, outputs, validation, and failure modes, not command transcripts.

## Tool Shape

The reusable tools are intentionally small:

- `tools.tabular`: inspect/profile/extract CSV, XLS, and XLSX files.
- `tools.pdf`: inspect PDF text/images and extract PDF tables.
- `tools.mail`: inspect EML/MSG as reference context.
- `tools.artifacts`: list, rediscover, describe, query, repair, and save SQLite-backed artifacts.

The CLI mirrors those useful presets:

```bash
tabuflow tabular inspect path/to/file.csv
tabuflow tabular profile path/to/file.xlsx
tabuflow tabular profile path/to/file.xlsx --all-sheets
tabuflow tabular extract path/to/file.csv
tabuflow pdf inspect path/to/file.pdf
tabuflow pdf extract path/to/file.pdf
tabuflow email inspect path/to/message.msg
tabuflow artifacts from-source path/to/file.xlsx
tabuflow artifacts describe artifact_name
tabuflow artifacts query @query.sql
tabuflow artifacts save-view saved_view_name @query.sql
```

The tool output should help a coding agent avoid dumb mistakes:

- `structure_hints` points to likely header and data-start rows.
- `excluded_row_hints` reports footer-like rows left outside extracted tables.
- `artifacts from-source` returns a preferred artifact and quoted preview SQL.
- Generated SQL artifact names may contain hyphens, so quote them: `select * from "service-usage-1cca2e" limit 20;`.

Use `--root-dir` and artifact `--database-path` for repeatable local runs across working directories. Workbench and adapter paths should still bind workspace/storage outside model-visible schemas.

## Current Direction

The stable direction is command-first and recipe-backed.

Generic tools prepare and expose artifacts. Domain recipes, SQL files, small Python transforms, or skills can then produce outputs such as GCP Summary + IBS or AWS invoice tables. The workbench agent can orchestrate that flow, but the reusable truth should stay in repo-native commands, modules, artifacts, and output recipes.

Read next:

- [OBSERVE.md](OBSERVE.md): real-file lessons and decisions.
- [PLAN.md](PLAN.md): stabilization plan.
