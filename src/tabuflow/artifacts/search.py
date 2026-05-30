"""Mixed artifact workspace search."""

from __future__ import annotations

from contextlib import closing
import json
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
from typing import Any, Literal, TextIO, cast

from ..workspace_db import artifact_workspace, open_read_only_connection, quote_identifier, requested_database_path
from .catalog import CatalogMetadataError, database_catalog
from .catalog.metadata import DatabaseCatalog, SqlArtifactInfo
from .database import error_result, jsonable_value
from .relationships import referenced_artifact_names
from .schemas import dump_artifact_search_result

ArtifactSearchScope = Literal["metadata", "rows", "files", "all"]

ARTIFACT_SEARCH_SCOPES = {"metadata", "rows", "files", "all"}
DEFAULT_ARTIFACT_SEARCH_MATCHES = 20
MAX_ARTIFACT_SEARCH_MATCHES = 100
MAX_METADATA_MATCH_VALUE_CHARS = 300
MAX_MATCH_TEXT_CHARS = 1_000
MAX_FILE_SCAN_LINE_CHARS = 8_000
SKIPPED_FILE_SUFFIXES = {".db", ".jpeg", ".jpg", ".png", ".sqlite", ".sqlite-shm", ".sqlite-wal", ".webp"}
RG_SKIP_GLOBS = ("!*.sqlite", "!*.sqlite-*", "!*.png", "!*.jpg", "!*.jpeg", "!*.webp")
FILE_TYPE_BY_ARTIFACT_DIR = {
    "sql": "sql_file",
    "outputs": "output_file",
    "pdf": "pdf_file",
}
TEXT_TYPE_MARKERS = ("CHAR", "CLOB", "TEXT", "VARCHAR")
ROW_VALUE_ALIAS = "__tabuflow_search_value__"
ROW_NUMBER_ALIAS = "__tabuflow_search_row_number__"


def _artifact_context(artifact: SqlArtifactInfo) -> dict[str, Any]:
    """Return bounded navigation context for one SQLite artifact."""
    context: dict[str, Any] = {
        "rows": artifact.row_count,
        "column_count": len(artifact.columns),
    }
    if artifact.source_paths:
        context["source"] = artifact.source_paths[0]
    return context


def _preview_text(
    value: str,
    *,
    max_chars: int = MAX_METADATA_MATCH_VALUE_CHARS,
) -> str:
    """Return a compact text value for search payloads."""
    if len(value) <= max_chars:
        return value
    return value[: max_chars - 3] + "..."


def _row_value(value: object) -> object:
    """Return one bounded row value for public search output."""
    json_value = jsonable_value(value)
    if not isinstance(json_value, str):
        return json_value
    return _preview_text(json_value, max_chars=MAX_MATCH_TEXT_CHARS)


def _kind_bias(artifact: SqlArtifactInfo) -> int:
    """Rank typed/queryable artifacts ahead of raw or internal tables."""
    if artifact.kind == "typed_content_view":
        return 5
    if artifact.kind == "view_or_table":
        return 3
    if artifact.kind == "raw_content_table":
        return -2
    if artifact.kind == "internal_catalog":
        return -6
    return 0


def _text_matches(value: str, query: str, *, regex: bool, case_sensitive: bool) -> bool:
    """Return whether one text value matches the search query."""
    if regex:
        flags = 0 if case_sensitive else re.IGNORECASE
        return re.search(query, value, flags=flags) is not None
    if case_sensitive:
        return query in value
    return query.lower() in value.lower()


def _metadata_fields(artifact: SqlArtifactInfo) -> list[tuple[str, str, int]]:
    """Return searchable metadata fields and rank weights."""
    fields = [
        ("name", artifact.name, 80),
        ("type", artifact.sqlite_type, 20),
        ("kind", artifact.kind, 20),
    ]
    fields.extend(("column", str(column["name"]), 70) for column in artifact.columns)
    fields.extend(("source_path", source_path, 75) for source_path in artifact.source_paths)
    if artifact.create_sql:
        fields.append(("create_sql", artifact.create_sql, 30))
    return fields


def _metadata_search(
    *,
    query: str,
    catalog: DatabaseCatalog,
    include_internal: bool,
    artifact_filter: str | None,
    regex: bool,
    case_sensitive: bool,
) -> list[dict[str, Any]]:
    """Return metadata search matches from the SQLite catalog snapshot."""
    results: list[dict[str, Any]] = []
    for artifact in catalog.visible_sql_artifacts(include_internal=include_internal):
        if artifact_filter is not None and artifact.name != artifact_filter:
            continue
        matched_fields = [(field, value, rank) for field, value, rank in _metadata_fields(artifact) if _text_matches(value, query, regex=regex, case_sensitive=case_sensitive)]
        if not matched_fields:
            continue
        result_type = "sqlite_view" if artifact.sqlite_type == "view" else "sqlite_table"
        field, value, rank = sorted(matched_fields, key=lambda item: -item[2])[0]
        results.append(
            {
                "type": result_type,
                "id": artifact.name,
                "backend": "sqlite",
                "match": {
                    "kind": "metadata",
                    "field": field,
                    "value": _preview_text(value),
                },
                "context": _artifact_context(artifact),
                "next": {
                    "query_hint": f"SELECT * FROM {quote_identifier(artifact.name)} LIMIT 20;",
                },
                "_rank": rank + _kind_bias(artifact),
            }
        )
    return results


def _looks_searchable_column(column: dict[str, Any]) -> bool:
    """Return whether a SQLite column should participate in row-value search."""
    declared_type = str(column.get("type") or "").upper()
    if not declared_type:
        return True
    return any(marker in declared_type for marker in TEXT_TYPE_MARKERS)


def _regexp_function(
    pattern: str,
    value: object,
    *,
    case_sensitive: bool,
) -> int:
    """SQLite REGEXP callback returning 1 on match."""
    if value is None:
        return 0
    flags = 0 if case_sensitive else re.IGNORECASE
    return 1 if re.search(pattern, str(value), flags=flags) is not None else 0


def _row_predicate(
    *,
    query: str,
    regex: bool,
    case_sensitive: bool,
) -> tuple[str, list[Any]]:
    """Return SQL predicate and bound parameters for the row-value alias."""
    value_ref = quote_identifier(ROW_VALUE_ALIAS)
    if regex:
        return f"{value_ref} IS NOT NULL AND REGEXP(?, {value_ref})", [query]
    if case_sensitive:
        return f"{value_ref} IS NOT NULL AND instr({value_ref}, ?) > 0", [query]
    return f"{value_ref} IS NOT NULL AND instr(lower({value_ref}), ?) > 0", [query.lower()]


def _row_value_query(
    *,
    artifact_name: str,
    column_name: str,
    predicate: str,
) -> str:
    """Return a bounded row-value search query for one artifact column."""
    return f"""
    SELECT
        {quote_identifier(ROW_NUMBER_ALIAS)},
        substr({quote_identifier(ROW_VALUE_ALIAS)}, 1, ?) AS {quote_identifier(ROW_VALUE_ALIAS)}
    FROM (
        SELECT
            row_number() OVER () AS {quote_identifier(ROW_NUMBER_ALIAS)},
            CAST({quote_identifier(column_name)} AS TEXT) AS {quote_identifier(ROW_VALUE_ALIAS)}
        FROM {quote_identifier(artifact_name)}
    )
    WHERE {predicate}
    LIMIT ?
    """


def _row_search(
    *,
    query: str,
    connection: sqlite3.Connection,
    catalog: DatabaseCatalog,
    include_internal: bool,
    artifact_filter: str | None,
    regex: bool,
    case_sensitive: bool,
    max_matches: int,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Return bounded SQLite row-value search matches."""
    if regex:
        connection.create_function("REGEXP", 2, lambda pattern, value: _regexp_function(pattern, value, case_sensitive=case_sensitive))

    results: list[dict[str, Any]] = []
    diagnostics: list[dict[str, str]] = []
    predicate, predicate_params = _row_predicate(query=query, regex=regex, case_sensitive=case_sensitive)
    for artifact in catalog.visible_sql_artifacts(include_internal=include_internal):
        if artifact_filter is not None and artifact.name != artifact_filter:
            continue
        searchable_columns = [str(column["name"]) for column in artifact.columns if _looks_searchable_column(column)]
        for column_name in searchable_columns:
            remaining = max_matches + 1 - len(results)
            if remaining <= 0:
                return results, diagnostics
            result_type = "sqlite_view" if artifact.sqlite_type == "view" else "sqlite_table"
            try:
                rows = connection.execute(
                    _row_value_query(
                        artifact_name=artifact.name,
                        column_name=column_name,
                        predicate=predicate,
                    ),
                    [MAX_MATCH_TEXT_CHARS, *predicate_params, remaining],
                ).fetchall()
            except (sqlite3.Error, sqlite3.Warning) as exc:
                diagnostics.append(
                    {
                        "kind": "row_search_error",
                        "artifact": artifact.name,
                        "column": column_name,
                        "message": str(exc),
                    }
                )
                continue
            for row in rows:
                results.append(
                    {
                        "type": result_type,
                        "id": artifact.name,
                        "backend": "sqlite",
                        "match": {
                            "kind": "row_value",
                            "column": column_name,
                            "row_number": int(row[0]),
                            "value": _row_value(row[1]),
                        },
                        "context": _artifact_context(artifact),
                        "next": {
                            "query_hint": f"SELECT * FROM {quote_identifier(artifact.name)} LIMIT 20;",
                        },
                        "_rank": 45 + _kind_bias(artifact),
                    }
                )
                if len(results) > max_matches:
                    return results, diagnostics
    return results, diagnostics


def _search_dirs(workspace_dir: Path) -> list[Path]:
    """Return managed artifact directories searched as filesystem text."""
    workspace = artifact_workspace(root_dir=workspace_dir.resolve())
    return [path for path in (workspace.sql_dir, workspace.outputs_dir, workspace.pdf_dir) if path.exists()]


def _skip_path(path: Path) -> bool:
    """Return whether a managed file path should be skipped by fallback search."""
    return any(part.startswith(".") or part == "__pycache__" for part in path.parts) or path.suffix.lower() in SKIPPED_FILE_SUFFIXES


def _iter_search_files(workspace_dir: Path) -> list[Path]:
    """Return searchable managed artifact files in stable order."""
    workspace_dir = workspace_dir.resolve()
    paths: list[Path] = []
    for search_dir in _search_dirs(workspace_dir):
        for path in sorted(search_dir.rglob("*")):
            if path.is_file() and not _skip_path(path.relative_to(workspace_dir)):
                paths.append(path)
    return paths


def _relative_path(path: Path, workspace_dir: Path) -> str:
    """Return a workspace-relative path when possible."""
    try:
        return str(path.resolve().relative_to(workspace_dir.resolve()))
    except ValueError:
        return str(path)


def _file_type(relative_path: str) -> str:
    """Return the normalized artifact type for a managed file hit."""
    path = Path(relative_path)
    if len(path.parts) >= 2 and path.parts[0] == "artifacts":
        return FILE_TYPE_BY_ARTIFACT_DIR.get(path.parts[1], "artifact_file")
    return "artifact_file"


def _file_context(
    *,
    path: Path,
    catalog: DatabaseCatalog | None,
) -> dict[str, Any]:
    """Return navigation context for a filesystem hit."""
    if path.suffix.lower() != ".sql" or catalog is None:
        return {}
    try:
        sql = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    references = referenced_artifact_names(
        sql,
        available_artifact_names=set(catalog.sql_artifacts_by_name),
        current_artifact_name="",
    )
    if not references:
        return {}
    return {"references": references}


def _submatch_payload(match: dict[str, Any]) -> dict[str, Any]:
    """Return one bounded submatch payload."""
    match_text = str((match.get("match") or {}).get("text") or "")
    match_preview = _preview_text(match_text, max_chars=MAX_MATCH_TEXT_CHARS)
    return {
        "start": int(match.get("start", 0)),
        "end": int(match.get("end", 0)),
        "match": match_preview,
    }


def _whole_line_submatch(line: str) -> dict[str, Any]:
    """Return a fallback submatch covering the full output line."""
    text = line.rstrip("\n")
    return {
        "start": 0,
        "end": len(text),
        "match": text,
    }


def _file_result(
    *,
    path: Path,
    relative_path: str,
    line_number: int,
    line_text: str,
    submatches: list[dict[str, Any]],
    backend: str,
    catalog: DatabaseCatalog | None,
) -> dict[str, Any]:
    """Return one normalized filesystem search result."""
    result_type = _file_type(relative_path)
    rank = 55 if result_type == "sql_file" else 35
    text_preview = _preview_text(line_text.rstrip("\n"), max_chars=MAX_MATCH_TEXT_CHARS)
    next_action = {"run": f"artifacts query @{relative_path}"} if result_type == "sql_file" else {}
    return {
        "type": result_type,
        "id": relative_path,
        "backend": backend,
        "match": {
            "kind": "file_line",
            "line": line_number,
            "text": text_preview,
            "submatches": submatches,
        },
        "context": _file_context(path=path, catalog=catalog),
        "next": next_action,
        "_rank": rank,
    }


def _matches_artifact_filter(result: dict[str, Any], artifact_filter: str | None) -> bool:
    """Return whether a file result should be kept under an artifact filter."""
    if artifact_filter is None:
        return True
    if artifact_filter in str(result.get("id", "")):
        return True
    match = result.get("match")
    if isinstance(match, dict) and artifact_filter in str(match.get("text", "")):
        return True
    context = result.get("context")
    return isinstance(context, dict) and artifact_filter in context.get("references", [])


def _finish_process(process: subprocess.Popen[str]) -> str:
    """Return process stderr after allowing a short graceful shutdown."""
    try:
        _, stderr = process.communicate(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill()
        _, stderr = process.communicate()
    return stderr


def _rg_file_search(
    *,
    query: str,
    workspace_dir: Path,
    catalog: DatabaseCatalog | None,
    artifact_filter: str | None,
    regex: bool,
    case_sensitive: bool | None,
    max_matches: int,
) -> list[dict[str, Any]] | None:
    """Return filesystem matches from ripgrep JSON output, if ripgrep is available."""
    rg_path = shutil.which("rg")
    if rg_path is None:
        return None
    workspace_dir = workspace_dir.resolve()
    search_dirs = _search_dirs(workspace_dir)
    if not search_dirs:
        return []

    args = [
        rg_path,
        "--json",
        "--line-number",
        "--with-filename",
        "--max-columns",
        str(MAX_FILE_SCAN_LINE_CHARS),
        "--no-ignore",
    ]
    for glob in RG_SKIP_GLOBS:
        args.extend(["--glob", glob])
    if case_sensitive is None:
        args.append("--smart-case")
    elif not case_sensitive:
        args.append("--ignore-case")
    if not regex:
        args.append("--fixed-strings")
    args.extend(["--", query])
    args.extend(str(path) for path in search_dirs)

    results: list[dict[str, Any]] = []
    try:
        process = subprocess.Popen(args, cwd=workspace_dir, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)  # noqa: S603
    except OSError:
        return None
    assert process.stdout is not None
    assert process.stderr is not None
    for line in process.stdout:
        if len(results) > max_matches:
            process.terminate()
            break
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") != "match":
            continue
        data = event.get("data") or {}
        path_text = str((data.get("path") or {}).get("text") or "")
        if not path_text:
            continue
        path = Path(path_text)
        relative_path = _relative_path(path, workspace_dir)
        submatches = [_submatch_payload(match) for match in data.get("submatches", [])]
        result = _file_result(
            path=path,
            relative_path=relative_path,
            line_number=int(data.get("line_number") or 0),
            line_text=str((data.get("lines") or {}).get("text") or ""),
            submatches=submatches,
            backend="rg",
            catalog=catalog,
        )
        if _matches_artifact_filter(result, artifact_filter):
            results.append(result)
    stderr = _finish_process(process)
    if process.returncode not in (0, 1, -15) and stderr:
        return None
    return results


def _grep_args(
    *,
    grep_path: str,
    query: str,
    path: Path,
    regex: bool,
    case_sensitive: bool,
) -> list[str]:
    """Return grep arguments for the backend-neutral search options."""
    args = [grep_path, "-n", "-I"]
    args.append("-E" if regex else "-F")
    if not case_sensitive:
        args.append("-i")
    args.extend(["--", query, str(path)])
    return args


def _grep_file_search(
    *,
    query: str,
    workspace_dir: Path,
    catalog: DatabaseCatalog | None,
    artifact_filter: str | None,
    regex: bool,
    case_sensitive: bool,
    max_matches: int,
) -> list[dict[str, Any]] | None:
    """Return filesystem matches from grep, if available, as a compatible fallback."""
    grep_path = shutil.which("grep")
    if grep_path is None:
        return None

    workspace_dir = workspace_dir.resolve()
    results: list[dict[str, Any]] = []
    for path in _iter_search_files(workspace_dir):
        if len(results) > max_matches:
            return results
        try:
            process = subprocess.Popen(  # noqa: S603
                _grep_args(
                    grep_path=grep_path,
                    query=query,
                    path=path,
                    regex=regex,
                    case_sensitive=case_sensitive,
                ),
                cwd=workspace_dir,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except OSError:
            return None
        assert process.stdout is not None
        assert process.stderr is not None
        for output_line in process.stdout:
            if len(results) > max_matches:
                process.terminate()
                break
            line_number_text, separator, line_text = output_line.partition(":")
            if not separator:
                continue
            try:
                line_number = int(line_number_text)
            except ValueError:
                continue
            submatches = _line_submatches(line=line_text, query=query, regex=regex, case_sensitive=case_sensitive)
            relative_path = _relative_path(path, workspace_dir)
            result = _file_result(
                path=path,
                relative_path=relative_path,
                line_number=line_number,
                line_text=line_text,
                submatches=submatches or [_whole_line_submatch(line_text)],
                backend="grep",
                catalog=catalog,
            )
            if _matches_artifact_filter(result, artifact_filter):
                results.append(result)
        stderr = _finish_process(process)
        if process.returncode not in (0, 1, -15) and stderr:
            return None
    return results


def _discard_line_remainder(handle: TextIO) -> None:
    """Discard the unread remainder of an overlong text line."""
    while True:
        chunk = handle.readline(MAX_FILE_SCAN_LINE_CHARS + 1)
        if not chunk or chunk.endswith("\n"):
            return


def _line_submatches(
    *,
    line: str,
    query: str,
    regex: bool,
    case_sensitive: bool,
) -> list[dict[str, Any]]:
    """Return simple submatch ranges for the Python fallback search."""
    if regex:
        flags = 0 if case_sensitive else re.IGNORECASE
        return [{"start": match.start(), "end": match.end(), "match": match.group(0)} for match in re.finditer(query, line, flags=flags)]
    haystack = line if case_sensitive else line.lower()
    needle = query if case_sensitive else query.lower()
    submatches: list[dict[str, Any]] = []
    start = 0
    while True:
        index = haystack.find(needle, start)
        if index < 0:
            return submatches
        end = index + len(needle)
        submatches.append({"start": index, "end": end, "match": line[index:end]})
        start = end


def _python_file_search(
    *,
    query: str,
    workspace_dir: Path,
    catalog: DatabaseCatalog | None,
    artifact_filter: str | None,
    regex: bool,
    case_sensitive: bool,
    max_matches: int,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Return filesystem matches using a bounded UTF-8 fallback."""
    workspace_dir = workspace_dir.resolve()
    results: list[dict[str, Any]] = []
    diagnostics: list[dict[str, str]] = []
    for path in _iter_search_files(workspace_dir):
        if len(results) > max_matches:
            return results, diagnostics
        relative_path = _relative_path(path, workspace_dir)
        try:
            handle = path.open(encoding="utf-8")
        except OSError as exc:
            diagnostics.append(
                {
                    "kind": "unreadable_file",
                    "path": str(path),
                    "message": str(exc),
                }
            )
            continue
        try:
            with handle:
                line_number = 0
                while True:
                    line = handle.readline(MAX_FILE_SCAN_LINE_CHARS + 1)
                    if not line:
                        break
                    line_number += 1
                    line_truncated = len(line) > MAX_FILE_SCAN_LINE_CHARS and not line.endswith("\n")
                    if line_truncated:
                        _discard_line_remainder(handle)
                    submatches = _line_submatches(line=line, query=query, regex=regex, case_sensitive=case_sensitive)
                    if not submatches:
                        continue
                    result = _file_result(
                        path=path,
                        relative_path=relative_path,
                        line_number=line_number,
                        line_text=line,
                        submatches=submatches,
                        backend="python",
                        catalog=catalog,
                    )
                    if line_truncated:
                        result["match"]["scan_truncated"] = True
                    if _matches_artifact_filter(result, artifact_filter):
                        results.append(result)
                    if len(results) > max_matches:
                        return results, diagnostics
        except UnicodeDecodeError:
            continue
    return results, diagnostics


def _file_search(
    *,
    query: str,
    workspace_dir: Path,
    catalog: DatabaseCatalog | None,
    artifact_filter: str | None,
    regex: bool,
    case_sensitive: bool | None,
    effective_case_sensitive: bool,
    max_matches: int,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Return filesystem text matches using CLI search, then Python as the last fallback."""
    workspace_dir = workspace_dir.resolve()
    rg_results = _rg_file_search(
        query=query,
        workspace_dir=workspace_dir,
        catalog=catalog,
        artifact_filter=artifact_filter,
        regex=regex,
        case_sensitive=case_sensitive,
        max_matches=max_matches,
    )
    if rg_results is not None:
        return rg_results, []
    grep_results = _grep_file_search(
        query=query,
        workspace_dir=workspace_dir,
        catalog=catalog,
        artifact_filter=artifact_filter,
        regex=regex,
        case_sensitive=effective_case_sensitive,
        max_matches=max_matches,
    )
    if grep_results is not None:
        return grep_results, [
            {
                "kind": "rg_unavailable",
                "fallback_backend": "grep",
                "message": "ripgrep was unavailable or failed. rg is required for supported artifact file search; used grep behind the same artifacts search interface. Run `tabuflow doctor` for install guidance.",
            }
        ]
    fallback_results, diagnostics = _python_file_search(
        query=query,
        workspace_dir=workspace_dir,
        catalog=catalog,
        artifact_filter=artifact_filter,
        regex=regex,
        case_sensitive=effective_case_sensitive,
        max_matches=max_matches,
    )
    diagnostics.append(
        {
            "kind": "rg_unavailable",
            "fallback_backend": "python",
            "message": "ripgrep and grep were unavailable or failed. rg is required for supported artifact file search; used bounded Python UTF-8 scanning behind the same artifacts search interface. Run `tabuflow doctor` for install guidance.",
        }
    )
    return fallback_results, diagnostics


def _sort_results(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return results ordered for artifact navigation."""
    type_order = {
        "sqlite_view": 0,
        "sqlite_table": 1,
        "sql_file": 2,
        "output_file": 3,
        "pdf_file": 4,
        "artifact_file": 5,
    }
    return sorted(
        results,
        key=lambda item: (
            -cast(int, item.get("_rank", 0)),
            type_order.get(str(item.get("type")), 99),
            str(item.get("id")),
            int(cast(dict[str, Any], item.get("match", {})).get("line", 0) or 0),
        ),
    )


def _public_search_results(sorted_results: list[dict[str, Any]], *, max_matches: int) -> list[dict[str, Any]]:
    """Return ranked search results without private ranking metadata."""
    public_results = []
    for result in sorted_results[:max_matches]:
        public_result = dict(result)
        public_result.pop("_rank", None)
        if not public_result.get("context"):
            public_result.pop("context", None)
        if not public_result.get("next"):
            public_result.pop("next", None)
        public_results.append(public_result)
    return public_results


def _search_error_result(
    *,
    database_path: Path,
    error_type: str,
    message: str,
    query: str,
    scope: str,
    artifact: str | None,
    max_matches: int,
) -> dict[str, Any]:
    """Return a normalized artifact search error payload."""
    return dump_artifact_search_result(
        error_result(
            database_path=database_path,
            error_type=error_type,
            message=message,
            query=query,
            scope=scope,
            artifact=artifact,
            max_matches=max_matches,
        )
    )


def _format_search_result(result: dict[str, Any]) -> str:
    """Return one rg-like line for a search result."""
    match = cast(dict[str, Any], result.get("match") or {})
    result_id = str(result.get("id") or "")
    kind = str(match.get("kind") or "")
    if kind == "file_line":
        return f"{result_id}:{match.get('line', 0)}:{match.get('text', '')}"
    if kind == "row_value":
        column = str(match.get("column") or "")
        value = str(match.get("value") or "")
        return f"sqlite:{result_id}:{match.get('row_number', 0)}:{column}: {value}"
    if kind == "metadata":
        field = str(match.get("field") or "")
        value = str(match.get("value") or "")
        return f"sqlite:{result_id}:{field}:{value}"
    return f"{result.get('type', 'artifact')}:{result_id}:{match.get('text') or match.get('value') or ''}"


def format_artifact_search(payload: dict[str, Any]) -> str:
    """Return compact rg-like artifact search output."""
    lines = [_format_search_result(result) for result in payload.get("search_results") or []]
    for diagnostic in payload.get("diagnostics") or []:
        lines.append(f"warning:{diagnostic.get('kind', 'diagnostic')}:{diagnostic.get('message', '')}")
    if payload.get("search_results_truncated"):
        lines.append("warning:truncated:more matches not shown")
    return "\n".join(lines)


def search_artifacts(
    query: str,
    *,
    root_dir: str | Path | None = None,
    database_path: str | Path | None = None,
    scope: ArtifactSearchScope = "all",
    artifact: str | None = None,
    regex: bool = False,
    max_matches: int = DEFAULT_ARTIFACT_SEARCH_MATCHES,
    include_internal: bool = False,
    case_sensitive: bool | None = None,
) -> dict[str, Any]:
    """Search SQLite artifact metadata, row values, and managed artifact files."""
    requested_path = requested_database_path(root_dir=root_dir, database_path=database_path)
    workspace = artifact_workspace(root_dir=root_dir)
    requested_scope = cast(str, scope)
    safe_max_matches = max(1, min(max_matches, MAX_ARTIFACT_SEARCH_MATCHES))
    query_text = query.strip()
    artifact_filter = None if artifact is None else artifact.strip()
    effective_case_sensitive = any(character.isupper() for character in query_text) if case_sensitive is None else case_sensitive

    try:
        if not query_text:
            return _search_error_result(
                database_path=requested_path,
                error_type="empty_search_query",
                message="Search query must not be empty.",
                query=query,
                scope=requested_scope,
                artifact=artifact_filter,
                max_matches=safe_max_matches,
            )
        if requested_scope not in ARTIFACT_SEARCH_SCOPES:
            raise ValueError(f"Artifact search scope must be one of: {', '.join(sorted(ARTIFACT_SEARCH_SCOPES))}.")
        if regex:
            re.compile(query_text)

        catalog = database_catalog(requested_path) if requested_path.exists() else None
        needs_sqlite = requested_scope in ("all", "metadata", "rows")
        if needs_sqlite and catalog is None and requested_scope in ("metadata", "rows"):
            raise ValueError(f"SQLite database does not exist: {requested_path}")
        if artifact_filter and catalog is not None and artifact_filter not in catalog.sql_artifacts_by_name:
            return _search_error_result(
                database_path=requested_path,
                error_type="missing_sql_artifact",
                message=f"SQLite artifact does not exist: {artifact_filter}",
                query=query_text,
                scope=requested_scope,
                artifact=artifact_filter,
                max_matches=safe_max_matches,
            )

        results: list[dict[str, Any]] = []
        diagnostics: list[dict[str, str]] = []
        if catalog is not None and requested_scope in ("all", "metadata"):
            results.extend(
                _metadata_search(
                    query=query_text,
                    catalog=catalog,
                    include_internal=include_internal,
                    artifact_filter=artifact_filter,
                    regex=regex,
                    case_sensitive=effective_case_sensitive,
                )
            )

        if catalog is not None and requested_scope in ("all", "rows"):
            with closing(open_read_only_connection(requested_path)) as connection:
                row_results, row_diagnostics = _row_search(
                    query=query_text,
                    connection=connection,
                    catalog=catalog,
                    include_internal=include_internal,
                    artifact_filter=artifact_filter,
                    regex=regex,
                    case_sensitive=effective_case_sensitive,
                    max_matches=safe_max_matches,
                )
                results.extend(row_results)
                diagnostics.extend(row_diagnostics)

        if requested_scope in ("all", "files"):
            file_results, file_diagnostics = _file_search(
                query=query_text,
                workspace_dir=workspace.workspace_dir,
                catalog=catalog,
                artifact_filter=artifact_filter,
                regex=regex,
                case_sensitive=case_sensitive,
                effective_case_sensitive=effective_case_sensitive,
                max_matches=safe_max_matches,
            )
            results.extend(file_results)
            diagnostics.extend(file_diagnostics)

        sorted_results = _sort_results(results)
        search_results = _public_search_results(sorted_results, max_matches=safe_max_matches)
        return dump_artifact_search_result(
            {
                "status": "ok",
                "search_result_count": len(search_results),
                "search_results": search_results,
                "search_results_truncated": len(sorted_results) > safe_max_matches,
                "diagnostics": diagnostics or None,
            }
        )
    except ValueError as exc:
        return _search_error_result(
            database_path=requested_path,
            error_type="invalid_artifact_search_request",
            message=str(exc),
            query=query_text,
            scope=requested_scope,
            artifact=artifact_filter,
            max_matches=safe_max_matches,
        )
    except re.error as exc:
        return _search_error_result(
            database_path=requested_path,
            error_type="invalid_regex",
            message=str(exc),
            query=query_text,
            scope=requested_scope,
            artifact=artifact_filter,
            max_matches=safe_max_matches,
        )
    except CatalogMetadataError as exc:
        return _search_error_result(
            database_path=requested_path,
            error_type="catalog_metadata_error",
            message=str(exc),
            query=query_text,
            scope=requested_scope,
            artifact=artifact_filter,
            max_matches=safe_max_matches,
        )
    except (sqlite3.Error, sqlite3.Warning) as exc:
        return _search_error_result(
            database_path=requested_path,
            error_type="sql_execution_error",
            message=str(exc),
            query=query_text,
            scope=requested_scope,
            artifact=artifact_filter,
            max_matches=safe_max_matches,
        )
