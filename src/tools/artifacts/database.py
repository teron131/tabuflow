"""SQLite database access helpers for artifact queries and saved views."""

from __future__ import annotations

from contextlib import closing
from datetime import date, datetime, time as datetime_time
from decimal import Decimal
from itertools import zip_longest
from pathlib import Path
import re
import sqlite3
from typing import Any, cast

from ..tabular.storage import quote_identifier, sqlite_database_path, sqlite_write_lock

MAX_QUERY_ROWS = 200
MISSING_VALUE = object()
READ_ONLY_SQL_PREFIXES = ("SELECT", "WITH", "EXPLAIN")
VIEW_SQL_PREFIXES = ("SELECT", "WITH")
LEADING_SQL_COMMENT = re.compile(r"\A(?:\s+|--[^\n]*(?:\n|\Z)|/\*.*?\*/)*", re.DOTALL)
VIEW_NAME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)*$")
UNQUOTED_HYPHENATED_REFERENCE = re.compile(r"\b(?:FROM|JOIN)\s+([A-Za-z_][\w$]*(?:-[A-Za-z0-9_]+)+)\b", re.IGNORECASE)


def jsonable_value(value: object) -> object:
    """Convert SQL query results into JSON-friendly values."""
    if isinstance(value, bytes):
        return {
            "kind": "bytes",
            "hex": value.hex(),
            "length": len(value),
        }
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date, datetime_time)):
        return value.isoformat()
    if isinstance(value, (list, tuple)):
        return [jsonable_value(item) for item in value]
    return value


def zip_exact(left: list[str], right: tuple[Any, ...]) -> list[tuple[str, Any]]:
    """Zip two sequences and fail if their lengths differ."""
    pairs: list[tuple[str, Any]] = []
    for left_item, right_item in zip_longest(left, right, fillvalue=MISSING_VALUE):
        if left_item is MISSING_VALUE or right_item is MISSING_VALUE:
            raise ValueError("SQL result row width did not match the reported column metadata.")
        pairs.append((cast(str, left_item), right_item))
    return pairs


def error_result(
    *,
    database_path: str | Path | None,
    error_type: str,
    message: str,
    **extra: Any,
) -> dict[str, Any]:
    """Build a stable error payload for tool callers."""
    payload: dict[str, Any] = {
        "status": "error",
        "error_type": error_type,
        "message": message,
    }
    if database_path is not None:
        payload["database_path"] = str(database_path)
    payload.update(extra)
    return payload


def query_summary(
    *,
    row_count: int,
    column_count: int,
    truncated: bool,
) -> str:
    """Build one compact summary for a SQL query result."""
    summary = f"Returned {row_count} row(s) across {column_count} column(s)."
    if truncated:
        summary += " Result rows were truncated."
    return summary


def normalized_column_names(column_names: list[str | None]) -> tuple[list[str], list[str]]:
    """Return stable, unique column names for row dictionaries."""
    seen: set[str] = set()
    normalized: list[str] = []
    originals: list[str] = []
    for index, raw_name in enumerate(column_names, start=1):
        base_name = raw_name or f"column_{index}"
        originals.append(base_name)
        suffix = 1
        candidate_name = base_name
        while candidate_name in seen:
            suffix += 1
            candidate_name = f"{base_name}__{suffix}"
        seen.add(candidate_name)
        normalized.append(candidate_name)
    return normalized, originals


def requested_database_path(
    *,
    root_dir: str | Path | None = None,
    database_path: str | Path | None = None,
) -> Path:
    """Return the requested SQLite path before existence checks."""
    return sqlite_database_path(root_dir=root_dir) if database_path is None else Path(database_path)


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


def leading_sql_keyword(sql: str) -> str:
    """Extract the first SQL keyword after whitespace and comments."""
    stripped = LEADING_SQL_COMMENT.sub("", sql, count=1).lstrip()
    if not stripped:
        return ""
    return stripped.split(None, 1)[0].upper()


def is_read_only_sql(sql: str) -> bool:
    """Return whether a SQL statement looks read-only."""
    return leading_sql_keyword(sql) in READ_ONLY_SQL_PREFIXES


def is_view_sql(sql: str) -> bool:
    """Return whether a SQL statement can be embedded in CREATE VIEW AS."""
    return leading_sql_keyword(sql) in VIEW_SQL_PREFIXES


def normalized_sql(sql: str) -> str:
    """Normalize one SQL statement for validation and embedding."""
    return sql.strip().rstrip(";").strip()


def sql_error_repair_hints(
    *,
    sql: str,
    error_message: str,
    root_dir: str | Path | None = None,
    database_path: str | Path | None = None,
) -> list[dict[str, Any]]:
    """Return deterministic repair hints for a failed artifact query."""
    hints: list[dict[str, Any]] = []
    referenced_hyphenated_names = list(dict.fromkeys(UNQUOTED_HYPHENATED_REFERENCE.findall(sql)))
    if referenced_hyphenated_names:
        hints.append(
            {
                "kind": "quote_sql_artifact",
                "identifier": referenced_hyphenated_names[0],
                "candidates": [{"name": name, "quoted": quote_identifier(name)} for name in referenced_hyphenated_names],
                "message": "Quote SQL artifact names that contain hyphens, for example FROM " + quote_identifier(referenced_hyphenated_names[0]) + ".",
            }
        )

    try:
        from .repair import suggest_sql_error_repair

        hints.extend(
            suggest_sql_error_repair(
                error_message,
                root_dir=root_dir,
                database_path=database_path,
            )
        )
    except (sqlite3.Error, ValueError, RuntimeError):
        pass
    return hints


def run_query(
    sql: str,
    *,
    root_dir: str | Path | None = None,
    database_path: str | Path | None = None,
    max_rows: int = MAX_QUERY_ROWS,
) -> dict[str, Any]:
    """Run SQL against a SQLite database and return a bounded result."""
    requested_path = requested_database_path(root_dir=root_dir, database_path=database_path)
    safe_max_rows = max(1, max_rows)
    query_sql = normalized_sql(sql)
    try:
        if not query_sql:
            return error_result(
                database_path=requested_path,
                error_type="empty_sql",
                message="SQL query must not be empty.",
                max_rows=safe_max_rows,
            )
        # This is a quick UX guard. The read-only SQLite connection is the actual safety boundary.
        if not is_read_only_sql(query_sql):
            return error_result(
                database_path=requested_path,
                error_type="disallowed_sql",
                message="Only read-only SELECT, WITH, and EXPLAIN queries are allowed.",
                max_rows=safe_max_rows,
            )

        resolved_path = resolve_db_path(
            root_dir=root_dir,
            database_path=database_path,
        )
        with closing(open_read_only_connection(resolved_path)) as connection:
            cursor = connection.execute(sql)
            description = cursor.description
            if not description:
                return {
                    "database_path": str(resolved_path),
                    "status": "ok",
                    "max_rows": safe_max_rows,
                    "row_count": 0,
                    "truncated": False,
                    "columns": [],
                    "rows": [],
                    "summary": query_summary(row_count=0, column_count=0, truncated=False),
                }

            column_names, original_columns = normalized_column_names([cast(Any, column[0]) for column in description])
            raw_rows = cursor.fetchmany(safe_max_rows + 1)
            truncated = len(raw_rows) > safe_max_rows
            result_rows = raw_rows[:safe_max_rows]
            rows = []
            for row in result_rows:
                rows.append({column_name: jsonable_value(value) for column_name, value in zip_exact(column_names, row)})

            payload = {
                "database_path": str(resolved_path),
                "status": "ok",
                "max_rows": safe_max_rows,
                "row_count": len(rows),
                "truncated": truncated,
                "columns": column_names,
                "rows": rows,
                "summary": query_summary(row_count=len(rows), column_count=len(column_names), truncated=truncated),
            }
            if column_names != original_columns:
                payload["original_columns"] = original_columns
            return payload
    except ValueError as exc:
        return error_result(
            database_path=requested_path,
            error_type="missing_database",
            message=str(exc),
            max_rows=safe_max_rows,
        )
    except (sqlite3.Error, sqlite3.Warning) as exc:
        return error_result(
            database_path=requested_path,
            error_type="sql_execution_error",
            message=str(exc),
            max_rows=safe_max_rows,
            repair_hints=sql_error_repair_hints(
                sql=query_sql,
                error_message=str(exc),
                root_dir=root_dir,
                database_path=database_path,
            ),
        )


def save_view(
    sql: str,
    view_name: str,
    *,
    root_dir: str | Path | None = None,
    database_path: str | Path | None = None,
    replace: bool = False,
) -> dict[str, Any]:
    """Save one read-only SQL query as a named SQLite view."""
    requested_path = requested_database_path(root_dir=root_dir, database_path=database_path)
    query_sql = normalized_sql(sql)
    normalized_view_name = view_name.strip()
    try:
        if not query_sql:
            return error_result(
                database_path=requested_path,
                error_type="empty_sql",
                message="SQL query must not be empty.",
                view_name=normalized_view_name,
                replace=replace,
            )
        if not normalized_view_name:
            return error_result(
                database_path=requested_path,
                error_type="empty_view_name",
                message="View name must not be empty.",
                replace=replace,
            )
        if not VIEW_NAME_PATTERN.fullmatch(normalized_view_name):
            return error_result(
                database_path=requested_path,
                error_type="invalid_view_name",
                message="View name must start with a letter and contain only letters, digits, and hyphen-separated words.",
                view_name=normalized_view_name,
                replace=replace,
            )
        if normalized_view_name.startswith("sqlite_"):
            return error_result(
                database_path=requested_path,
                error_type="reserved_view_name",
                message="View name must not start with 'sqlite_'.",
                view_name=normalized_view_name,
                replace=replace,
            )
        if not is_view_sql(query_sql):
            return error_result(
                database_path=requested_path,
                error_type="disallowed_sql",
                message="Only read-only SELECT and WITH queries can be saved as views.",
                view_name=normalized_view_name,
                replace=replace,
            )

        resolved_path = resolve_db_path(
            root_dir=root_dir,
            database_path=database_path,
        )
        with sqlite_write_lock(resolved_path), closing(sqlite3.connect(str(resolved_path))) as connection:
            existing_row = connection.execute(
                """
                SELECT type
                FROM sqlite_master
                WHERE name = ?
                """,
                [normalized_view_name],
            ).fetchone()
            if existing_row is not None:
                existing_type = cast(str, existing_row[0])
                if existing_type != "view":
                    return error_result(
                        database_path=resolved_path,
                        error_type="name_conflict",
                        message=f"SQLite object already exists and is not a view: {normalized_view_name}",
                        view_name=normalized_view_name,
                        replace=replace,
                    )
                if not replace:
                    return error_result(
                        database_path=resolved_path,
                        error_type="view_exists",
                        message=f"SQLite view already exists: {normalized_view_name}",
                        view_name=normalized_view_name,
                        replace=replace,
                    )
                connection.execute(f"DROP VIEW IF EXISTS {quote_identifier(normalized_view_name)}")

            connection.execute(f"CREATE VIEW {quote_identifier(normalized_view_name)} AS {query_sql}")
            connection.commit()

        from .catalog import describe_sql_artifact

        description = describe_sql_artifact(
            normalized_view_name,
            root_dir=root_dir,
            database_path=resolved_path,
        )
        return {
            "database_path": str(resolved_path),
            "status": "ok",
            "view_name": normalized_view_name,
            "replace": replace,
            "saved_sql": query_sql,
            "sql_artifact": description,
        }
    except ValueError as exc:
        return error_result(
            database_path=requested_path,
            error_type="missing_database",
            message=str(exc),
            view_name=normalized_view_name,
            replace=replace,
        )
    except (sqlite3.Error, sqlite3.Warning) as exc:
        return error_result(
            database_path=requested_path,
            error_type="sql_execution_error",
            message=str(exc),
            view_name=normalized_view_name,
            replace=replace,
        )


def query_artifacts(
    sql: str,
    *,
    root_dir: str | Path | None = None,
    database_path: str | Path | None = None,
    max_rows: int = MAX_QUERY_ROWS,
) -> dict[str, Any]:
    """Run read-only SQL against the artifact cache and return bounded JSON rows."""
    return run_query(
        sql,
        root_dir=root_dir,
        database_path=database_path,
        max_rows=max_rows,
    )


def save_artifact_view(
    sql: str,
    view_name: str,
    *,
    root_dir: str | Path | None = None,
    database_path: str | Path | None = None,
    replace: bool = False,
) -> dict[str, Any]:
    """Save a read-only artifact-cache query as a named SQLite view."""
    return save_view(
        sql,
        view_name,
        root_dir=root_dir,
        database_path=database_path,
        replace=replace,
    )
