"""Workspace SQLite database path, lock, and identifier helpers."""

from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import time

SQLITE_FILENAME = "tabular.sqlite"
SQLITE_CONTENTS_TABLE = "_tabular_contents"
SQLITE_SOURCES_TABLE = "_tabular_sources"
LOCK_POLL_SECONDS = 0.1
LOCK_TIMEOUT_SECONDS = 10.0


def resolve_root_dir(*, root_dir: str | Path | None = None) -> Path:
    """Resolve the tabular workspace root, defaulting to the current working directory."""
    return Path.cwd().resolve() if root_dir is None else Path(root_dir).expanduser().resolve()


def sqlite_database_path(*, root_dir: str | Path | None = None) -> Path:
    """Return the shared SQLite path for extracted tabular data."""
    resolved_root = resolve_root_dir(root_dir=root_dir)
    data_dir = resolved_root / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir / SQLITE_FILENAME


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
