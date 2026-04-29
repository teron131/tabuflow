"""Shared constants for the workbench API."""

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_DIST = REPO_ROOT / "frontend" / "dist"


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
WORKBENCH_SOURCE_ROOT = _configured_path(
    "DATA_AGENTICS_WORKBENCH_SOURCE_ROOT",
    REPO_ROOT,
)

DEFAULT_SQL = """SELECT
  metric,
  billing_account_name,
  grand_total_cost_usd,
  total_unrounded_cost_usd,
  rank_n
FROM analysis_result
LIMIT 10;"""

SUGGESTED_QUESTIONS = [
    "Show the grand total cost.",
    "Rank billing accounts by cost.",
    "Explain the top account.",
]

STAGE_CARDS = [
    {"name": "Prep", "status": "ready", "summary": "Inspect files and recover queryable tables."},
    {"name": "Query", "status": "ready", "summary": "Draft, execute, repair, and validate SQL."},
    {"name": "Save", "status": "ready", "summary": "Persist useful results as SQLite views."},
]
