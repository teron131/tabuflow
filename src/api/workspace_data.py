"""Workspace data discovery for the local workbench API."""

from __future__ import annotations

from pathlib import Path
import hashlib
import sqlite3

from ..tools.tabular.storage import SQLITE_SOURCES_TABLE
from .constants import (
    PREPARED_DATABASE_PATH,
    UPLOAD_EXTENSIONS,
    UPLOADS_DIR,
    WORKBENCH_SOURCE_ROOT,
)


class WorkspaceDataMissingError(RuntimeError):
    """Raised when the local prepared data required by the workbench is missing."""


def list_prepared_source_summaries(database_path: Path) -> list[dict[str, str]]:
    """Return browser-safe source descriptors discovered from the local catalog."""
    files: list[dict[str, str]] = []
    query = f"""
	SELECT source_path, source_format, source_sheet_name, source_table_name
	FROM {SQLITE_SOURCES_TABLE}
	ORDER BY source_format, source_path, source_sheet_name, source_table_name
	"""
    try:
        with sqlite3.connect(str(database_path)) as connection:
            rows = connection.execute(query).fetchall()
    except sqlite3.Error as exc:
        raise WorkspaceDataMissingError("Prepared source catalog is missing.") from exc

    seen_paths: set[str] = set()
    for source_path, source_format, source_sheet_name, source_table_name in rows:
        source_path_text = str(source_path or "")
        if source_path_text in seen_paths:
            continue
        seen_paths.add(source_path_text)
        kind = str(source_format or "file").upper()
        display_path = _source_display_path(source_path_text)
        files.append(
            {
                "id": _stable_source_id(source_path_text),
                "name": _source_name(source_path_text),
                "kind": kind,
                "status": "prepared",
                "source_path": display_path,
                "destination_path": _source_display_path(str(database_path)),
                "sheet_name": str(source_sheet_name or ""),
                "table_name": str(source_table_name or ""),
            }
        )
    if database_path.exists():
        database_path_text = str(database_path)
        files.append(
            {
                "id": _stable_source_id(database_path_text),
                "name": database_path.name,
                "kind": "SQLITE",
                "status": "prepared",
                "source_path": _source_display_path(database_path_text),
                "destination_path": _source_display_path(database_path_text),
                "sheet_name": "",
                "table_name": "",
            }
        )
    return files


def list_uploaded_source_summaries(*, existing_paths: set[str] | None = None) -> list[dict[str, str]]:
    """Return uploaded files not already represented by the prepared catalog."""
    if not UPLOADS_DIR.exists():
        return []

    existing = existing_paths or set()
    files: list[dict[str, str]] = []
    for path in sorted(UPLOADS_DIR.iterdir(), key=lambda item: item.name.lower()):
        if not path.is_file() or path.suffix.lower() not in UPLOAD_EXTENSIONS:
            continue
        path_text = str(path)
        display_path = _source_display_path(path_text)
        if display_path in existing:
            continue
        kind = path.suffix.lower().lstrip(".").upper()
        files.append(
            {
                "id": _stable_source_id(path_text),
                "name": path.name,
                "kind": kind,
                "status": "uploaded",
                "source_path": display_path,
                "destination_path": "",
                "sheet_name": "",
                "table_name": "",
            }
        )
    return files


def _stable_source_id(path: str) -> str:
    """Return a stable browser ID for one local source path."""
    digest = hashlib.sha256(path.encode("utf-8")).hexdigest()[:10]
    return f"source-{digest}"


def _source_name(path: str) -> str:
    """Return a readable source name without hardcoded private placeholders."""
    if not path:
        return "Unnamed source"
    return Path(path).name or path


def _source_display_path(path: str) -> str:
    """Resolve source display paths from the configured source root."""
    if not path:
        return ""
    source_path = Path(path).expanduser()
    if source_path.is_absolute():
        try:
            return str(source_path.resolve().relative_to(WORKBENCH_SOURCE_ROOT.resolve()))
        except ValueError:
            return source_path.name or "external source"
    return str(source_path)


def default_database_path() -> Path:
    """Return the prepared workspace database or fail explicitly."""
    if not PREPARED_DATABASE_PATH.exists():
        raise WorkspaceDataMissingError("Prepared database is missing.")
    return PREPARED_DATABASE_PATH


def resolve_database_path() -> str:
    """Return the private workspace database path."""
    return str(default_database_path())
