# data-agentics

`data-agentics` is a local workbench for helping non-technical users do coding-style data analysis over SQL without having to operate vendor-specific tools, cloud consoles, notebook platforms, or BI products directly.

The project exists to put an agentic coding workflow around ordinary business data: observe the source, plan the analysis, prepare queryable tables, write SQL, inspect results, repair mistakes, and save useful artifacts. The user should be able to ask for the analysis they need while the system handles the mechanics that normally require a data engineer, analyst notebook, warehouse UI, or vendor dashboard.

Real source files are still messy. CSVs can start with metadata, spreadsheets can hide several sparse tables in one sheet, and PDFs often need a different extraction path entirely. The useful shape is not "ask a model to read a file"; it is a bounded system where Python tools inspect and prepare the data, the agent chooses the right stage, SQL does the deterministic work, and the UI shows what happened.

Two notes capture the current direction:

- [OBSERVE.md](OBSERVE.md) records what has been learned from real files and tool experiments.
- [PLAN.md](PLAN.md) records the stabilization plan for the agent stack and workbench UI.

## Architecture

```mermaid
flowchart LR
    accTitle: data-agentics architecture
    accDescr: Shows how a user asks for analysis, the agent prepares messy files into SQL artifacts, and the workbench returns inspectable results without direct vendor-platform usage.

    User["User"] --> Ask["Ask for analysis"]
    Ask --> Workbench["Workbench UI"]

    subgraph App["Local data-agentics app"]
        Workbench --> API["FastAPI transport"]
        API --> Orchestrator["Agent orchestrator"]
        Orchestrator --> ObservePlan["Observe and plan"]
        ObservePlan --> Prep{"Source type"}
        Prep -->|"CSV / XLSX"| PrepCsv["prep_csv"]
        Prep -->|"PDF tables"| PrepPdf["prep_pdf"]
        PrepCsv --> SQLStore["SQLite artifacts"]
        PrepPdf --> SQLStore
        SQLStore --> QueryStage["query_stage"]
        QueryStage --> SQLLoop["Write, run, repair, validate SQL"]
        SQLLoop --> SavedView["Saved view"]
        SavedView --> Answer["Answer and evidence"]
    end

    Sources["Messy business files"] --> Prep
    Skills["Reusable skills"] --> Orchestrator
    ScopedFiles["Scoped file edits"] --> Orchestrator
    Answer --> Workbench

    VendorTools["Vendor tools and platforms"] -. "not required" .- Workbench
```

The orchestrator is the user-facing layer. It can answer ordinary questions directly, but for data work it calls explicit stage tools:

- `prep_csv` prepares CSV/XLSX sources into SQL artifacts.
- `prep_pdf` prepares PDF table sources into SQL artifacts.
- `query_stage` writes or reuses SQL, executes it, repairs runtime failures, validates the result, and saves a view.

The backend stays Python-first because the hard parts are data inspection, parsing, OCR/table extraction, SQLite artifacts, and LangGraph workflows. The frontend is the operator surface: files, targets, SQL, saved views, skills, tool traces, and chat.

## Current Shape

The main runtime path is:

```text
Workbench UI -> FastAPI -> orchestrator -> prep_csv/prep_pdf -> query_stage -> saved view -> answer
```

The lower-level posture is:

- observe real files before inventing schemas,
- keep extraction conservative and inspectable,
- use Python libraries for parsing instead of shell-driven parsing,
- use SQL artifacts as the deterministic bridge between messy inputs and user-facing answers,
- keep write access scoped to SQL artifacts and workspace skill files.
