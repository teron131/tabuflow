"""Workspace data discovery for the local workbench API."""

from __future__ import annotations

from pathlib import Path
import sqlite3

from ..tools.tabular.storage import SQLITE_SOURCES_TABLE
from .constants import PREPARED_DATABASE_PATH


class WorkspaceDataMissingError(RuntimeError):
    """Raised when the local prepared data required by the workbench is missing."""


def list_prepared_source_summaries(database_path: Path) -> list[dict[str, str]]:
    """Return browser-safe source descriptors discovered from the local catalog."""
    files: list[dict[str, str]] = []
    query = f"""
	SELECT source_format, COUNT(DISTINCT source_path)
	FROM {SQLITE_SOURCES_TABLE}
	GROUP BY source_format
	ORDER BY source_format
	"""
    try:
        with sqlite3.connect(str(database_path)) as connection:
            rows = connection.execute(query).fetchall()
    except sqlite3.Error as exc:
        raise WorkspaceDataMissingError("Prepared source catalog is missing.") from exc

    source_index = 1
    for source_format, count in rows:
        kind = str(source_format or "file").upper()
        for _ in range(int(count or 0)):
            files.append(
                {
                    "id": f"private-source-{source_index}",
                    "name": f"Private source {source_index}",
                    "kind": kind,
                    "status": "prepared",
                }
            )
            source_index += 1
    if database_path.exists():
        files.append(
            {
                "id": f"private-source-{source_index}",
                "name": f"Private source {source_index}",
                "kind": "SQLITE",
                "status": "prepared",
            }
        )
    return files


def default_database_path() -> Path:
    """Return the prepared workspace database or fail explicitly."""
    if not PREPARED_DATABASE_PATH.exists():
        raise WorkspaceDataMissingError("Prepared database is missing.")
    return PREPARED_DATABASE_PATH


def resolve_database_path() -> str:
    """Return the private workspace database path."""
    return str(default_database_path())
