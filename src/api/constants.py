"""Shared constants for the workbench API."""

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_DIST = REPO_ROOT / "frontend" / "dist"
IMAGE_UPLOAD_EXTENSIONS = {".gif", ".jpeg", ".jpg", ".png", ".webp"}
TABULAR_UPLOAD_EXTENSIONS = {".csv", ".xlsx"}
UPLOAD_EXTENSIONS = {*TABULAR_UPLOAD_EXTENSIONS, ".pdf", *IMAGE_UPLOAD_EXTENSIONS}


def _configured_path(env_name: str, default: Path) -> Path:
    """Resolve a path from configuration while keeping a local default."""
    configured = os.environ.get(env_name)
    if not configured:
        return default
    return Path(configured).expanduser()


PREPARED_DATABASE_PATH = _configured_path(
    "DATA_AGENTICS_PREPARED_DATABASE_PATH",
    REPO_ROOT / "data" / "tabular.sqlite",
)
UPLOADS_DIR = _configured_path(
    "DATA_AGENTICS_UPLOADS_DIR",
    REPO_ROOT / "data" / "uploads",
)
WORKBENCH_SOURCE_ROOT = _configured_path(
    "DATA_AGENTICS_WORKBENCH_SOURCE_ROOT",
    REPO_ROOT,
)

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
