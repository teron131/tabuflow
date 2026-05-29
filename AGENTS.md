# Tabuflow Agent Notes

Use this file as lightweight project guidance for source-editing sessions in this repo. Prefer the current code for exact behavior, and keep this file focused on principles and ideas.

## Document Glossary

- `AGENTS.md` at the repo root is for project principles, ideas, and source-editing defaults.
- `.agents/AGENTS.md` is for the bundled agent tool/skill package and command-use guidance.
- `OBSERVE.md` is the lab document: implementation notes, what changed, why it changed, pressure tests, and lessons from real files.
- `README.md` is the minimal existence/landing document. Do not treat it as the main place for ongoing design decisions.

## North Star

- Tabuflow is meant to be a CLI tool for coding agents first. Agents should call `tabuflow` to inspect messy files, produce artifacts, query results, and verify outputs.
- Treat `tabuflow` as the primary agent-facing surface. MCP can mirror the same operations, but the CLI should remain the canonical way agents use the tool from a shell.
- Prefer command-first, artifact-backed, recipe-friendly workflows. Tools should prepare inspectable artifacts; skill references, skill scripts, and artifact workspace SQL/Python can do the business-specific reasoning.

## Boundaries

- Treat `src/tabuflow` as the reusable tool layer. Keep ordinary Python functions and command surfaces usable without app-runtime assumptions.
- LangChain, LangGraph, and UI/workbench surfaces do not matter for the current direction. Treat them as backup consumers, not design drivers.
- Keep context-specific business rules out of generic code. Vendor, customer, billing-period, and one-off file knowledge belongs in skills, skill references/scripts, or artifact workspace recipes, and should still be flexible enough for next month's data.
- Prefer package-by-responsibility splits and plain functions over OO or strategy layers unless existing shared behavior clearly earns the abstraction.
- Remove wrapper-only helpers, barrels, or facades when they only forward arguments or imports. Keep helpers that protect real I/O, parsing, cache, artifact, or compatibility contracts.
- Keep artifact layout predictable under `./artifacts/`: tabular SQLite data, PDF workspaces, reusable SQL, and validated outputs should stay easy to rediscover from source metadata.

## PDF Extraction

- Treat PDF extraction as a PyMuPDF-powered puzzle workflow. If a well-written PyMuPDF script can extract a layout correctly, the tool should make that workflow easier to reproduce, not less capable.
- Enhancing the PDF tool should not make existing correct extraction workflows worse; compare against real tool usage and preserve proven behavior.

## Working Habits

- Inspect the current package shape before refactoring; older notes may lag behind recent package moves.
- Make narrow, behavior-preserving edits and avoid compatibility shims unless the caller contract really needs one.
- For verification, choose focused checks that match the change: `uv run ruff check .`, `uv run ruff format --check .`, targeted tests, and CLI or MCP smoke checks when a command surface changes.
- When tool behavior changes, test it through your own Tabuflow tool usage and then inspect the outputs visually and semantically, not only by exit status.
