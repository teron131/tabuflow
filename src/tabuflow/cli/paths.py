"""CLI path and SQL argument resolution helpers."""

from __future__ import annotations

import argparse
from pathlib import Path


def read_sql_argument(
    sql: str,
    *,
    root_dir: Path | None = None,
) -> str:
    """Return inline SQL or the text from an @file argument."""
    sql_path = resolve_sql_argument_path(sql, root_dir=root_dir)
    if sql_path is None:
        return sql
    return sql_path.read_text(encoding="utf-8")


def resolve_sql_argument_path(
    sql: str,
    *,
    root_dir: Path | None = None,
) -> Path | None:
    """Return the resolved @file path for a SQL argument when present."""
    if not sql.startswith("@"):
        return None
    sql_path = Path(sql[1:]).expanduser()
    if not sql_path.is_absolute() and root_dir is not None:
        sql_path = root_dir / sql_path
    return sql_path.resolve()


def resolve_cli_root(args: argparse.Namespace) -> Path | None:
    """Resolve the optional CLI workspace root."""
    root_dir = getattr(args, "root_dir", None)
    if root_dir is None:
        return None
    return Path(root_dir).expanduser().resolve()


def resolve_cli_path(
    path: str,
    args: argparse.Namespace,
) -> Path:
    """Resolve a source path against the optional CLI workspace root."""
    source_path = Path(path).expanduser()
    if source_path.is_absolute():
        return source_path.resolve()
    root_dir = resolve_cli_root(args)
    if root_dir is None:
        return source_path
    return (root_dir / source_path).resolve()


def resolve_cli_database_path(args: argparse.Namespace) -> Path | None:
    """Resolve the optional artifact database path against the CLI workspace root."""
    database_path = getattr(args, "database_path", None)
    if database_path is None:
        return None
    path = Path(database_path).expanduser()
    if path.is_absolute():
        return path.resolve()
    root_dir = resolve_cli_root(args)
    if root_dir is None:
        return path
    return (root_dir / path).resolve()


def add_root_argument(parser: argparse.ArgumentParser) -> None:
    """Add the shared workspace-root argument."""
    parser.add_argument("--root-dir", default=None, help="Workspace root for relative source paths and the default artifact database.")
