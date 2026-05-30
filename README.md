# Tabuflow

Tabuflow is a local tool layer for turning messy business files into inspectable artifacts that coding agents, scripts, and SQL can work with.

The reusable boundary lives in `src/tabuflow`: inspect/extract tabular files, prepare/extract PDF workspaces, inspect email reference files, and query artifact data. The app/agent code under `src/backend` is a consumer, not the foundation.

Run from the project root:

```bash
uv run tabuflow ...
```

To use `tabuflow` from other workspaces, install it once with `uv tool install /path-to-this-repo-tabuflow`.

Artifacts live under `./artifacts/`:

- `tabular.sqlite` for extracted tables/views and lineage metadata
- `sql/` for reusable SQL
- `outputs/` for generated deliverables
- `pdf/` for PDF workspaces
