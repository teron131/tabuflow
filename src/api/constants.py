"""Shared constants for the workbench API."""

IMAGE_UPLOAD_EXTENSIONS = {".gif", ".jpeg", ".jpg", ".png", ".webp"}
TABULAR_UPLOAD_EXTENSIONS = {".csv", ".xlsx"}
UPLOAD_EXTENSIONS = {*TABULAR_UPLOAD_EXTENSIONS, ".pdf", *IMAGE_UPLOAD_EXTENSIONS}

DEFAULT_SQL = """SELECT
  'ready' AS status,
  'Select a source, table, or saved result to inspect.' AS message;"""

SUGGESTED_QUESTIONS = [
    "What sources are prepared?",
    "Show available SQL targets.",
    "Preview the selected result.",
]

STAGE_CARDS = [
    {"name": "Prep", "status": "ready", "summary": "Inspect files and recover queryable tables."},
    {"name": "Query", "status": "ready", "summary": "Draft, execute, repair, and validate SQL."},
    {"name": "Save", "status": "ready", "summary": "Persist useful results as SQLite views."},
]
