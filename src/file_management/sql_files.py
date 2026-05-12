"""File management helpers for SQL query artifacts."""

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any

from ..pipelines.namer import name_sql_artifact
from ..tools.fs import HashlineEdit, SandboxFS

DEFAULT_SQL_DIR = "data/sql"
DEFAULT_SQL_DESCRIPTION = "SQL query artifact."
MAX_SQL_ARTIFACT_PREVIEW_CHARS = 4_000
SQL_HEADER_RE = re.compile(r"^--\s*([^:]+):\s*(.*)$")


@dataclass(frozen=True)
class SQLFileLocation:
    """Resolved SQL artifact location inside a sandbox."""

    user_path: str
    path: Path
    fs: SandboxFS


def _root_path(root_dir: str | Path | None = None) -> Path:
    """Return the root directory that bounds SQL artifact paths."""
    return Path.cwd().resolve() if root_dir is None else Path(root_dir).expanduser().resolve()


def _sql_user_path(
    sql_path: str | Path | None = None,
    *,
    root_dir: str | Path | None = None,
    run_id: str = "default",
    description: str = DEFAULT_SQL_DESCRIPTION,
    filename_hint: str | None = None,
) -> str:
    """Return the SQL artifact path in sandbox user-path form."""
    if sql_path is None:
        stem = name_sql_artifact(filename_hint or description, run_id)
        return f"{DEFAULT_SQL_DIR}/{stem}.sql"

    path = Path(sql_path).expanduser()
    if not path.is_absolute():
        return str(path)

    root_path = _root_path(root_dir)
    resolved_path = path.resolve()
    try:
        return str(resolved_path.relative_to(root_path))
    except ValueError as exc:
        raise ValueError(f"SQL artifact path must stay inside {root_path}.") from exc


def _sandbox(root_dir: str | Path | None = None) -> SandboxFS:
    """Return the sandboxed filesystem wrapper for SQL artifacts."""
    return SandboxFS(_root_path(root_dir))


def _sql_location(
    sql_path: str | Path | None = None,
    *,
    root_dir: str | Path | None = None,
    run_id: str = "default",
    description: str = DEFAULT_SQL_DESCRIPTION,
    filename_hint: str | None = None,
) -> SQLFileLocation:
    """Return the sandbox path data for one SQL artifact."""
    user_path = _sql_user_path(
        sql_path,
        root_dir=root_dir,
        run_id=run_id,
        description=description,
        filename_hint=filename_hint,
    )
    fs = _sandbox(root_dir)
    resolved_path = fs.resolve(user_path)
    if resolved_path.suffix != ".sql":
        raise ValueError("SQL artifact path must use a .sql extension.")
    return SQLFileLocation(
        user_path=user_path,
        path=resolved_path,
        fs=fs,
    )


def _comment_value(value: str) -> str:
    """Return one SQL-line-comment-safe value."""
    return " ".join(value.strip().split())


def _parse_sql_header(sql: str) -> dict[str, Any]:
    """Return metadata from the standard SQL artifact comment header."""
    metadata: dict[str, Any] = {
        "description": DEFAULT_SQL_DESCRIPTION,
        "run_id": "",
        "selected_sql_artifacts": [],
    }
    for line in sql.splitlines():
        stripped_line = line.strip()
        if not stripped_line:
            if metadata.get("description") != DEFAULT_SQL_DESCRIPTION or metadata.get("run_id"):
                break
            continue
        match = SQL_HEADER_RE.match(stripped_line)
        if match is None:
            break
        key = match.group(1).strip().lower()
        value = match.group(2).strip()
        if key == "description" and value:
            metadata["description"] = value
        elif key == "run id":
            metadata["run_id"] = value
        elif key == "sql artifacts":
            metadata["selected_sql_artifacts"] = [item.strip() for item in value.split(",") if item.strip()]
    return metadata


def _sql_artifact_payload(
    fs: SandboxFS,
    path: Path,
) -> dict[str, Any]:
    """Return one SQL artifact payload with header metadata and preview text."""
    relative_path = path.relative_to(fs.root_dir).as_posix()
    sql = fs.read_text(relative_path)
    metadata = _parse_sql_header(sql)
    return {
        "path": relative_path,
        "sql_path": str(path),
        "description": metadata["description"],
        "run_id": metadata["run_id"],
        "selected_sql_artifacts": metadata["selected_sql_artifacts"],
        "sql_preview": sql[:MAX_SQL_ARTIFACT_PREVIEW_CHARS].rstrip(),
    }


def _sql_with_header(
    sql: str,
    *,
    run_id: str,
    description: str,
    selected_sql_artifacts: list[str] | None,
) -> str:
    """Return SQL text with the standard artifact header."""
    normalized_sql = sql.strip()
    header_lines = [
        f"-- Description: {_comment_value(description) or 'SQL query artifact.'}",
        f"-- Run ID: {_comment_value(run_id)}",
    ]
    if selected_sql_artifacts:
        header_lines.append(f"-- SQL artifacts: {_comment_value(', '.join(selected_sql_artifacts))}")
    if normalized_sql:
        return "\n".join([*header_lines, "", normalized_sql, ""])
    return "\n".join([*header_lines, ""])


def _error_result(
    *,
    error_type: str,
    message: str,
    sql_path: str | Path | None,
) -> dict[str, Any]:
    """Return the shared file-management error payload."""
    return {
        "status": "error",
        "error_type": error_type,
        "message": message,
        "sql_path": str(sql_path or ""),
    }


def _replace_sql_text(location: SQLFileLocation, sql_text: str) -> None:
    """Replace an existing SQL artifact through the hashline edit path."""
    refs = location.fs.read_hashline(location.user_path).splitlines()
    if not refs:
        location.fs.write_text(location.user_path, sql_text)
        return

    location.fs.edit_hashline(
        location.user_path,
        [
            HashlineEdit(
                operation="replace_range",
                start_ref=refs[0].split(":", maxsplit=1)[0],
                end_ref=refs[-1].split(":", maxsplit=1)[0],
                lines=sql_text.splitlines(),
            )
        ],
    )


def read_sql_file(
    sql_path: str | Path,
    *,
    root_dir: str | Path | None = None,
    run_id: str = "default",
) -> dict[str, Any]:
    """Read one SQL artifact from the bounded filesystem workspace."""
    try:
        location = _sql_location(
            sql_path,
            root_dir=root_dir,
            run_id=run_id,
        )
        return {
            "status": "ok",
            "sql_path": str(location.path),
            "sql": location.fs.read_text(location.user_path),
        }
    except OSError as exc:
        return _error_result(
            error_type="read_failed",
            message=str(exc),
            sql_path=sql_path,
        )
    except ValueError as exc:
        return _error_result(
            error_type="invalid_sql_path",
            message=str(exc),
            sql_path=sql_path,
        )


def list_sql_files(
    *,
    root_dir: str | Path | None = None,
    sql_dir: str = DEFAULT_SQL_DIR,
    max_files: int = 100,
) -> dict[str, Any]:
    """List saved SQL files with their standard artifact header metadata."""
    try:
        fs = _sandbox(root_dir)
        sql_root = fs.resolve(sql_dir)
        if not sql_root.exists():
            return {"status": "ok", "artifacts": []}
        if not sql_root.is_dir():
            return _error_result(
                error_type="invalid_sql_dir",
                message=f"SQL artifact directory is not a directory: {sql_dir}",
                sql_path=sql_dir,
            )

        artifacts: list[dict[str, Any]] = []
        for path in sorted(sql_root.rglob("*.sql"), key=lambda item: item.stat().st_mtime, reverse=True):
            artifacts.append(_sql_artifact_payload(fs, path))
            if len(artifacts) >= max(0, max_files):
                break
        return {"status": "ok", "artifacts": artifacts}
    except OSError as exc:
        return _error_result(
            error_type="list_failed",
            message=str(exc),
            sql_path=sql_dir,
        )
    except ValueError as exc:
        return _error_result(
            error_type="invalid_sql_dir",
            message=str(exc),
            sql_path=sql_dir,
        )


def _search_tokens(value: str) -> set[str]:
    """Return coarse lexical tokens for SQL artifact discovery."""
    return {token for token in re.findall(r"[a-z0-9_]+", value.lower()) if len(token) > 2}


def _sql_artifact_search_text(artifact: dict[str, Any]) -> str:
    """Return searchable metadata for a SQL artifact."""
    selected_sql_artifacts = " ".join(str(item) for item in artifact.get("selected_sql_artifacts", []))
    return " ".join(
        [
            str(artifact.get("description") or ""),
            str(artifact.get("path") or ""),
            selected_sql_artifacts,
        ]
    )


def search_sql_files(
    query: str,
    *,
    root_dir: str | Path | None = None,
    top_k: int = 5,
) -> dict[str, Any]:
    """Search saved SQL files from their descriptions and standard headers."""
    result = list_sql_files(root_dir=root_dir)
    if result.get("status") != "ok":
        return result

    query_tokens = _search_tokens(query)
    scored_artifacts: list[tuple[int, int, dict[str, Any]]] = []
    for idx, artifact in enumerate(result.get("artifacts", [])):
        artifact_tokens = _search_tokens(_sql_artifact_search_text(artifact))
        score = len(query_tokens & artifact_tokens)
        if score > 0 or not query_tokens:
            scored_artifacts.append((score, -idx, artifact))

    scored_artifacts.sort(reverse=True)
    return {
        "status": "ok",
        "artifacts": [artifact for _, _, artifact in scored_artifacts[: max(0, top_k)]],
    }


def read_sql_hashlines(
    sql_path: str | Path,
    *,
    root_dir: str | Path | None = None,
    run_id: str = "default",
) -> dict[str, Any]:
    """Read one SQL artifact as hashline-addressed text."""
    try:
        location = _sql_location(
            sql_path,
            root_dir=root_dir,
            run_id=run_id,
        )
        return {
            "status": "ok",
            "sql_path": str(location.path),
            "hashlines": location.fs.read_hashline(location.user_path),
        }
    except OSError as exc:
        return _error_result(
            error_type="read_failed",
            message=str(exc),
            sql_path=sql_path,
        )
    except ValueError as exc:
        return _error_result(
            error_type="invalid_sql_path",
            message=str(exc),
            sql_path=sql_path,
        )


def edit_sql_file(
    sql_path: str | Path,
    edits: list[HashlineEdit],
    *,
    root_dir: str | Path | None = None,
    run_id: str = "default",
) -> dict[str, Any]:
    """Apply hashline edits to one SQL artifact."""
    try:
        location = _sql_location(
            sql_path,
            root_dir=root_dir,
            run_id=run_id,
        )
        sql_text = location.fs.edit_hashline(location.user_path, edits)
        return {
            "status": "ok",
            "sql_path": str(location.path),
            "sql": sql_text,
        }
    except OSError as exc:
        return _error_result(
            error_type="edit_failed",
            message=str(exc),
            sql_path=sql_path,
        )
    except ValueError as exc:
        return _error_result(
            error_type="invalid_sql_edit",
            message=str(exc),
            sql_path=sql_path,
        )


def write_sql_file(
    sql: str,
    sql_path: str | Path | None = None,
    *,
    root_dir: str | Path | None = None,
    run_id: str = "default",
    description: str = DEFAULT_SQL_DESCRIPTION,
    filename_hint: str | None = None,
    selected_sql_artifacts: list[str] | None = None,
) -> dict[str, Any]:
    """Write one SQL artifact into the bounded filesystem workspace."""
    try:
        location = _sql_location(
            sql_path,
            root_dir=root_dir,
            run_id=run_id,
            description=description,
            filename_hint=filename_hint,
        )
        sql_text = _sql_with_header(
            sql,
            run_id=run_id,
            description=description,
            selected_sql_artifacts=selected_sql_artifacts,
        )
        if location.path.exists():
            _replace_sql_text(location, sql_text)
        else:
            location.fs.write_text(location.user_path, sql_text)
        return {
            "status": "ok",
            "sql_path": str(location.path),
            "sql": sql_text,
        }
    except OSError as exc:
        return _error_result(
            error_type="write_failed",
            message=str(exc),
            sql_path=sql_path,
        )
    except ValueError as exc:
        return _error_result(
            error_type="invalid_sql_path",
            message=str(exc),
            sql_path=sql_path,
        )
