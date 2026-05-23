"""Query-stage SQL history discovery helpers."""

from pathlib import Path
import re
from typing import Any

from tabuflow.fs.workspace import (
    WorkspaceFile,
    read_workspace_text,
    resolve_workspace_file,
)

DEFAULT_SQL_DIR = "data/sql"
DEFAULT_SQL_DESCRIPTION = "SQL query artifact."
MAX_SQL_ARTIFACT_PREVIEW_CHARS = 4_000
SQL_HEADER_RE = re.compile(r"^--\s*([^:]+):\s*(.*)$")


def parse_sql_header(sql: str) -> dict[str, Any]:
    """Return metadata from the standard query-stage SQL artifact header."""
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


def sql_history_payload(location: WorkspaceFile) -> dict[str, Any]:
    """Return one SQL history payload with header metadata and preview text."""
    sql = read_workspace_text(location)
    metadata = parse_sql_header(sql)
    return {
        "path": location.relative_path,
        "sql_path": str(location.path),
        "description": metadata["description"],
        "run_id": metadata["run_id"],
        "selected_sql_artifacts": metadata["selected_sql_artifacts"],
        "sql_preview": sql[:MAX_SQL_ARTIFACT_PREVIEW_CHARS].rstrip(),
    }


def sql_history_error(
    *,
    error_type: str,
    message: str,
    sql_path: str | Path | None,
) -> dict[str, Any]:
    """Return the shared SQL-history error payload."""
    return {
        "status": "error",
        "error_type": error_type,
        "message": message,
        "sql_path": str(sql_path or ""),
    }


def list_sql_history(
    *,
    root_dir: str | Path | None = None,
    sql_dir: str = DEFAULT_SQL_DIR,
    max_files: int = 100,
) -> dict[str, Any]:
    """List saved query-stage SQL files with their standard headers."""
    try:
        sql_root = resolve_workspace_file(sql_dir, root_dir=root_dir)
        if not sql_root.path.exists():
            return {"status": "ok", "artifacts": []}
        if not sql_root.path.is_dir():
            return sql_history_error(
                error_type="invalid_sql_dir",
                message=f"SQL artifact directory is not a directory: {sql_dir}",
                sql_path=sql_dir,
            )

        artifacts: list[dict[str, Any]] = []
        paths = sorted(
            sql_root.path.rglob("*.sql"),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )
        for path in paths:
            relative_path = path.relative_to(sql_root.root_dir).as_posix()
            location = resolve_workspace_file(relative_path, root_dir=sql_root.root_dir)
            artifacts.append(sql_history_payload(location))
            if len(artifacts) >= max(0, max_files):
                break
        return {"status": "ok", "artifacts": artifacts}
    except OSError as exc:
        return sql_history_error(
            error_type="list_failed",
            message=str(exc),
            sql_path=sql_dir,
        )
    except ValueError as exc:
        return sql_history_error(
            error_type="invalid_sql_dir",
            message=str(exc),
            sql_path=sql_dir,
        )


def search_tokens(value: str) -> set[str]:
    """Return coarse lexical tokens for SQL-history discovery."""
    return {token for token in re.findall(r"[a-z0-9_]+", value.lower()) if len(token) > 2}


def sql_history_search_text(artifact: dict[str, Any]) -> str:
    """Return searchable metadata for a SQL history artifact."""
    selected_sql_artifacts = " ".join(str(item) for item in artifact.get("selected_sql_artifacts", []))
    return " ".join(
        [
            str(artifact.get("description") or ""),
            str(artifact.get("path") or ""),
            selected_sql_artifacts,
        ]
    )


def search_sql_history(
    query: str,
    *,
    root_dir: str | Path | None = None,
    top_k: int = 5,
) -> dict[str, Any]:
    """Search saved query-stage SQL files from descriptions and headers."""
    result = list_sql_history(root_dir=root_dir)
    if result.get("status") != "ok":
        return result

    query_tokens = search_tokens(query)
    scored_artifacts: list[tuple[int, int, dict[str, Any]]] = []
    for idx, artifact in enumerate(result.get("artifacts", [])):
        artifact_tokens = search_tokens(sql_history_search_text(artifact))
        score = len(query_tokens & artifact_tokens)
        if score > 0 or not query_tokens:
            scored_artifacts.append((score, -idx, artifact))

    scored_artifacts.sort(reverse=True)
    return {
        "status": "ok",
        "artifacts": [artifact for _, _, artifact in scored_artifacts[: max(0, top_k)]],
    }
