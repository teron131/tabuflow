"""CLI path and SQL argument resolution helpers."""

from __future__ import annotations

from pathlib import Path


def read_sql_argument(
    sql: str,
) -> str:
    """Return inline SQL or the text from an @file argument."""
    sql_path = resolve_sql_argument_path(sql)
    if sql_path is None:
        return sql
    return sql_path.read_text(encoding="utf-8")


def resolve_sql_argument_path(sql: str) -> Path | None:
    """Return the resolved @file path for a SQL argument when present."""
    if not sql.startswith("@"):
        return None
    sql_path = Path(sql[1:]).expanduser()
    return sql_path.resolve()


def resolve_cli_path(path: str) -> Path:
    """Resolve an absolute source path or return a cwd-relative source path."""
    source_path = Path(path).expanduser()
    if source_path.is_absolute():
        return source_path.resolve()
    return source_path
