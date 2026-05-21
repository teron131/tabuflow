# Tabuflow

`Tabuflow` is a local workbench for helping non-technical users do coding-style data analysis over SQL without having to operate vendor-specific tools, cloud consoles, notebook platforms, or BI products directly.

The project exists to put an agentic coding workflow around ordinary business data: observe the source, plan the analysis, prepare queryable tables, write SQL, inspect results, repair mistakes, and save useful artifacts. The user should be able to ask for the analysis they need while the system handles the mechanics that normally require a data engineer, analyst notebook, warehouse UI, or vendor dashboard.

Real source files are still messy. CSVs can start with metadata, spreadsheets can hide several sparse tables in one sheet, and PDFs often need a different extraction path entirely. The useful shape is not "ask a model to read a file"; it is a bounded system where Python tools inspect and prepare the data, the agent chooses the right stage, SQL does the deterministic work, and the UI shows what happened.

Two notes capture the current direction:

- [OBSERVE.md](OBSERVE.md) records what has been learned from real files and tool experiments.
- [PLAN.md](PLAN.md) records the stabilization plan for the agent stack and workbench UI.

## Architecture

```mermaid
flowchart TB
    accTitle: Tabuflow architecture
    accDescr: Shows the browser workbench, FastAPI shell, direct backend routes, chat orchestrator, stage tools, scoped filesystem tools, skills, and persisted artifacts.

    Workbench["Workbench UI<br/>files, SQL, skills, chat"] --> ApiLayer
    ApiLayer --> Orchestrator
    Orchestrator --> Stores
    Stores --> Response["Workbench response"]

    subgraph ApiLayer["FastAPI shell"]
        direction LR
        Api["FastAPI app<br/>CORS, request logging,<br/>optional static frontend"]
        ChatRoutes["chat and chat/stream"]
        DirectRoutes["direct routes<br/>settings, upload, preview,<br/>SQL, explainer, skills"]
        Api --> ChatRoutes
        Api --> DirectRoutes
    end

    subgraph Orchestrator["LangGraph orchestrator"]
        direction LR
        ChatGraph["chat graph<br/>skills, model, ToolNode,<br/>summary"]
        StageTools["stage tools<br/>prep_csv, prep_pdf,<br/>query_stage"]
        ScopedTools["scoped tools<br/>filesystem and skills"]
        ChatGraph --> StageTools
        ChatGraph --> ScopedTools
    end

    subgraph Stores["backend services and stores"]
        direction LR
        SourceStore["uploads and previews"] ~~~ PreparedStore["SQLite cache<br/>targets and profiles"]
        PreparedStore ~~~ SqlStore["SQL artifacts<br/>views and downloads"]
        SqlStore ~~~ SkillsStore["skills/"]
        SkillsStore ~~~ ExplanationStore["cached explanations"]
    end
```

The orchestrator graph expands into the chat path and tool inventory:

```mermaid
flowchart TB
    accTitle: orchestrator graph and tools
    accDescr: Shows the user-facing chat graph and the tools it may call.

    ChatPath --> ToolSurface

    subgraph ChatPath["user-facing chat path"]
        direction LR
        ChatBridge["chat bridge<br/>graph input and AI SDK stream"]
        ChatBridge --> SkillsNode["skills node<br/>workspace overview"]
        SkillsNode --> Model["model node<br/>decides next step"]
        Model -->|"answer directly"| DirectAnswer["direct answer"]
        Model -->|"call tools"| ToolNode["ToolNode"]
        ToolNode --> Model
        Model -->|"tool run finished"| Summarize["summarize tool run"]
    end

    subgraph ToolSurface["orchestrator tool surface"]
        direction LR
        StageTools["stage tools"] --> PrepCsv["prep_csv"]
        StageTools --> PrepPdf["prep_pdf"]
        StageTools --> QueryStage["query_stage"]
        FsTools["sandbox fs tools<br/>list, search, read,<br/>hashline edit"] ~~~ SkillTools["skill tools<br/>create, search, load"]
    end
```

The `prep_csv` tool owns CSV/XLSX inspection and extraction:

```mermaid
flowchart TB
    accTitle: prep_csv loop
    accDescr: Shows how prep_csv inspects CSV or XLSX files, profiles structure, extracts tables into SQLite, and returns prepared target metadata.

    SourceFiles["CSV/XLSX files"] --> PrepCsvAgent["prep_csv ReAct<br/>structured decision"]
    PrepCsvAgent --> Inspect["inspect_tabular<br/>raw grid"]
    PrepCsvAgent --> Profile["profile_tabular<br/>schema hints"]
    PrepCsvAgent --> Extract["extract_tabular<br/>load SQLite"]
    Inspect --> Decision{"ready?"}
    Profile --> Decision
    Decision -->|"inspect more"| PrepCsvAgent
    Decision -->|"extract"| Extract
    Extract --> PreparedTargets["prepared targets<br/>profiles, source refs"]
    PreparedTargets --> QueryStageInput["query_stage input"]
```

The `prep_pdf` tool owns PDF table inspection and extraction:

```mermaid
flowchart TB
    accTitle: prep_pdf loop
    accDescr: Shows how prep_pdf inspects PDF pages, extracts visual tables into SQLite, and returns prepared target metadata.

    SourceFiles["PDF files"] --> PrepPdfAgent["prep_pdf ReAct<br/>structured decision"]
    PrepPdfAgent --> Inspect["inspect_pdf<br/>page text, images"]
    PrepPdfAgent --> Extract["extract_pdf<br/>load SQLite"]
    Inspect --> Decision{"tables found?"}
    Decision -->|"inspect more"| PrepPdfAgent
    Decision -->|"extract"| Extract
    Extract --> PreparedTargets["prepared targets<br/>profiles, source refs"]
    PreparedTargets --> QueryStageInput["query_stage input"]
```

The `query_stage` tool owns the SQL loop:

```mermaid
flowchart TB
    accTitle: query_stage loop
    accDescr: Shows how query_stage reuses or writes SQL, executes it, repairs runtime failures, validates results, and saves a view.

    Prepared["prepared targets<br/>or saved SQL"] --> ExistingSql{"check_existing_sql"}
    ExistingSql -->|"new SQL needed"| WriteSql["write_sql"]
    ExistingSql -->|"reuse accepted SQL"| ExecuteSql["execute_sql"]
    WriteSql --> ExecuteSql
    ExecuteSql -->|"result ready"| Validate["validate result"]
    Validate -->|"accepted or final"| SaveView["save_view"]
    SaveView --> Artifact["saved SQL view"]
    ExecuteSql -->|"runtime error"| RepairSql["repair_sql"]
    RepairSql --> ExecuteSql
    Validate -->|"retryable feedback"| WriteSql
```

The orchestrator is the user-facing LangGraph layer. It can answer ordinary questions directly, but for grounded work it can call stage tools, sandboxed filesystem tools, and skill tools:

- `prep_csv` prepares CSV/XLSX sources into SQL artifacts.
- `prep_pdf` prepares PDF table sources into SQL artifacts.
- `query_stage` writes or reuses SQL, executes it, repairs runtime failures, validates the result, and saves a view.
- `fs_list_files`, `fs_search_text`, `fs_read_text`, `fs_read_hashline`, and `fs_edit_hashline` keep file access sandboxed, with writes scoped to `.sql` files and workspace skill resources.
- `create_skill_package`, `search_skills`, and `load_skill` let the agent create deterministic skill frames and load situational guidance.

FastAPI also exposes direct workbench routes for upload, preview, SQL execution/download, file explanation, LLM settings, and skill editing. Those routes share the same local stores as the agent path: the SQLite cache, SQL artifacts, cached explanations, and `skills/`.

The backend stays Python-first because the hard parts are data inspection, parsing, OCR/table extraction, SQLite artifacts, and LangGraph workflows. The frontend is the operator surface: files, targets, SQL, saved views, skills, tool traces, and chat.

## Current Shape

The main runtime path is:

```text
Workbench UI -> FastAPI -> orchestrator -> prep_csv/prep_pdf -> query_stage -> validation -> saved view -> answer
```

The lower-level posture is:

- observe real files before inventing schemas,
- keep extraction conservative and inspectable,
- use Python libraries for parsing instead of shell-driven parsing,
- use SQL artifacts as the deterministic bridge between messy inputs and user-facing answers,
- keep write access scoped to SQL artifacts and workspace skill files.
