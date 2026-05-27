"""Workspace SQLite database path, lock, and identifier helpers."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import os
from pathlib import Path
import sqlite3
import time

SQLITE_FILENAME = "tabular.sqlite"
ARTIFACTS_DIRNAME = "artifacts"
ARTIFACTS_SQL_DIRNAME = "sql"
ARTIFACTS_OUTPUTS_DIRNAME = "outputs"
ARTIFACTS_PDF_DIRNAME = "pdf"
SQLITE_CONTENTS_TABLE = "_tabular_contents"
SQLITE_SOURCES_TABLE = "_tabular_sources"
LOCK_POLL_SECONDS = 0.1
LOCK_TIMEOUT_SECONDS = 10.0


@dataclass(frozen=True)
class ArtifactWorkspace:
    """Fixed artifact directories owned by one Tabuflow workspace."""

    workspace_dir: Path
    artifacts_dir: Path
    tabular_database_path: Path
    pdf_dir: Path
    sql_dir: Path
    outputs_dir: Path

    def relative_path(self, path: Path) -> str:
        """Return a workspace-relative path when possible."""
        try:
            return str(path.resolve().relative_to(self.workspace_dir))
        except ValueError:
            return str(path.resolve())

    def as_payload(self) -> dict[str, str]:
        """Return the JSON-friendly artifact workspace metadata."""
        return {
            "workspace_dir": str(self.workspace_dir),
            "artifacts_dir": str(self.artifacts_dir),
            "tabular_database_path": str(self.tabular_database_path),
            "pdf_dir": str(self.pdf_dir),
            "sql_dir": str(self.sql_dir),
            "outputs_dir": str(self.outputs_dir),
        }


def resolve_root_dir(*, root_dir: str | Path | None = None) -> Path:
    """Resolve the tabular workspace root, defaulting to the current working directory."""
    return Path.cwd().resolve() if root_dir is None else Path(root_dir).expanduser().resolve()


def sqlite_database_path(*, root_dir: str | Path | None = None) -> Path:
    """Return the shared SQLite path for extracted tabular data."""
    database_path = artifact_workspace(root_dir=root_dir).tabular_database_path
    database_path.parent.mkdir(parents=True, exist_ok=True)
    return database_path


def artifact_workspace(
    *,
    root_dir: str | Path | None = None,
) -> ArtifactWorkspace:
    """Return the fixed artifact workspace paths without creating directories."""
    workspace_dir = resolve_root_dir(root_dir=root_dir)
    artifacts_dir = workspace_dir / ARTIFACTS_DIRNAME
    return ArtifactWorkspace(
        workspace_dir=workspace_dir,
        artifacts_dir=artifacts_dir,
        tabular_database_path=artifacts_dir / SQLITE_FILENAME,
        pdf_dir=artifacts_dir / ARTIFACTS_PDF_DIRNAME,
        sql_dir=artifacts_dir / ARTIFACTS_SQL_DIRNAME,
        outputs_dir=artifacts_dir / ARTIFACTS_OUTPUTS_DIRNAME,
    )


def requested_database_path(
    *,
    root_dir: str | Path | None = None,
    database_path: str | Path | None = None,
) -> Path:
    """Return the requested SQLite path before existence checks."""
    return artifact_workspace(root_dir=root_dir).tabular_database_path if database_path is None else Path(database_path)


def open_read_only_connection(database_path: Path) -> sqlite3.Connection:
    """Open one SQLite connection in read-only mode."""
    return sqlite3.connect(f"{database_path.resolve().as_uri()}?mode=ro", uri=True)


def resolve_db_path(
    *,
    root_dir: str | Path | None = None,
    database_path: str | Path | None = None,
) -> Path:
    """Resolve the SQLite database path and ensure it exists."""
    resolved_path = requested_database_path(root_dir=root_dir, database_path=database_path)
    if not resolved_path.exists():
        raise ValueError(f"SQLite database does not exist: {resolved_path}")
    return resolved_path


@contextmanager
def sqlite_write_lock(database_path: Path):
    """Serialize SQLite writers with a lightweight lock file."""
    lock_path = database_path.with_suffix(f"{database_path.suffix}.lock")
    start_time = time.monotonic()

    while True:
        try:
            descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError as error:
            if time.monotonic() - start_time >= LOCK_TIMEOUT_SECONDS:
                raise TimeoutError(f"Timed out waiting for SQLite lock: {lock_path}") from error
            time.sleep(LOCK_POLL_SECONDS)

    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(str(os.getpid()))
        yield
    finally:
        lock_path.unlink(missing_ok=True)


def quote_identifier(identifier: str) -> str:
    """Quote a SQL identifier safely."""
    return '"' + identifier.replace('"', '""') + '"'
