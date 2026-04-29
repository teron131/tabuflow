"""Shared constants for the workbench API."""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_DIST = REPO_ROOT / "frontend" / "dist"
PREPARED_DATABASE_PATH = REPO_ROOT / "data" / "tabular.sqlite"

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
