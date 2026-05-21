"""Query-stage SQL artifact file helpers."""

from pathlib import Path
from typing import Any

from ...tools.artifacts.naming import name_sql_artifact
from ...tools.fs.workspace import (
    WorkspaceFile,
    edit_workspace_hashlines,
    read_workspace_hashlines,
    read_workspace_text,
    replace_workspace_text,
    resolve_workspace_file,
    workspace_root,
)
from .sql_history import DEFAULT_SQL_DESCRIPTION, DEFAULT_SQL_DIR


def _sql_user_path(
    sql_path: str | Path | None = None,
    *,
    root_dir: str | Path | None = None,
    run_id: str = "default",
    description: str = DEFAULT_SQL_DESCRIPTION,
    filename_hint: str | None = None,
) -> str:
    """Return the query-stage SQL artifact path in workspace-relative form."""
    if sql_path is None:
        stem = name_sql_artifact(
            filename_hint or description,
            identifier=run_id,
        )
        return f"{DEFAULT_SQL_DIR}/{stem}.sql"

    path = Path(sql_path).expanduser()
    if not path.is_absolute():
        return str(path)

    root_path = workspace_root(root_dir)
    resolved_path = path.resolve()
    try:
        return str(resolved_path.relative_to(root_path))
    except ValueError as exc:
        raise ValueError(f"SQL artifact path must stay inside {root_path}.") from exc


def _sql_location(
    sql_path: str | Path | None = None,
    *,
    root_dir: str | Path | None = None,
    run_id: str = "default",
    description: str = DEFAULT_SQL_DESCRIPTION,
    filename_hint: str | None = None,
) -> WorkspaceFile:
    """Return workspace path data for one query-stage SQL artifact."""
    user_path = _sql_user_path(
        sql_path,
        root_dir=root_dir,
        run_id=run_id,
        description=description,
        filename_hint=filename_hint,
    )
    location = resolve_workspace_file(user_path, root_dir=root_dir)
    if location.path.suffix != ".sql":
        raise ValueError("SQL artifact path must use a .sql extension.")
    return location


def _comment_value(value: str) -> str:
    """Return one SQL-line-comment-safe value."""
    return " ".join(value.strip().split())


def _sql_with_header(
    sql: str,
    *,
    run_id: str,
    description: str,
    selected_sql_artifacts: list[str] | None,
) -> str:
    """Return SQL text with the standard query-stage artifact header."""
    normalized_sql = sql.strip()
    header_lines = [
        f"-- Description: {_comment_value(description) or DEFAULT_SQL_DESCRIPTION}",
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
    """Return the shared query-stage SQL file error payload."""
    return {
        "status": "error",
        "error_type": error_type,
        "message": message,
        "sql_path": str(sql_path or ""),
    }


def read_sql_artifact(
    sql_path: str | Path,
    *,
    root_dir: str | Path | None = None,
    run_id: str = "default",
) -> dict[str, Any]:
    """Read one query-stage SQL artifact."""
    try:
        location = _sql_location(
            sql_path,
            root_dir=root_dir,
            run_id=run_id,
        )
        return {
            "status": "ok",
            "sql_path": str(location.path),
            "sql": read_workspace_text(location),
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


def read_sql_artifact_hashlines(
    sql_path: str | Path,
    *,
    root_dir: str | Path | None = None,
    run_id: str = "default",
) -> dict[str, Any]:
    """Read one query-stage SQL artifact as hashline-addressed text."""
    try:
        location = _sql_location(
            sql_path,
            root_dir=root_dir,
            run_id=run_id,
        )
        return {
            "status": "ok",
            "sql_path": str(location.path),
            "hashlines": read_workspace_hashlines(location),
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


def edit_sql_artifact(
    sql_path: str | Path,
    edits: list[Any],
    *,
    root_dir: str | Path | None = None,
    run_id: str = "default",
) -> dict[str, Any]:
    """Apply hashline edits to one query-stage SQL artifact."""
    try:
        location = _sql_location(
            sql_path,
            root_dir=root_dir,
            run_id=run_id,
        )
        sql_text = edit_workspace_hashlines(location, edits)
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


def write_sql_artifact(
    sql: str,
    sql_path: str | Path | None = None,
    *,
    root_dir: str | Path | None = None,
    run_id: str = "default",
    description: str = DEFAULT_SQL_DESCRIPTION,
    filename_hint: str | None = None,
    selected_sql_artifacts: list[str] | None = None,
) -> dict[str, Any]:
    """Write one query-stage SQL artifact."""
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
        replace_workspace_text(location, sql_text)
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
