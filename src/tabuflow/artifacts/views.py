"""SQLite saved-view creation and metadata registration."""

from __future__ import annotations

from contextlib import closing
from pathlib import Path
import re
import sqlite3
from typing import Any, cast

from ..workspace_db import quote_identifier, sqlite_write_lock
from .catalog import describe_sql_artifact
from .database import error_result, leading_sql_keyword, normalized_sql, requested_database_path, resolve_db_path
from .relationships import referenced_artifact_names, register_saved_view_relationships

VIEW_SQL_PREFIXES = ("SELECT", "WITH")
VIEW_NAME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)*$")


def is_view_sql(sql: str) -> bool:
    """Return whether a SQL statement can be embedded in CREATE VIEW AS."""
    return leading_sql_keyword(sql) in VIEW_SQL_PREFIXES


def save_view(
    sql: str,
    view_name: str,
    *,
    root_dir: str | Path | None = None,
    database_path: str | Path | None = None,
    sql_file_path: str | Path | None = None,
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
            )
        if not normalized_view_name:
            return error_result(
                database_path=requested_path,
                error_type="empty_view_name",
                message="View name must not be empty.",
            )
        if not VIEW_NAME_PATTERN.fullmatch(normalized_view_name):
            return error_result(
                database_path=requested_path,
                error_type="invalid_view_name",
                message="View name must start with a letter and contain only letters, digits, and hyphen-separated words.",
                view_name=normalized_view_name,
            )
        if normalized_view_name.startswith("sqlite_"):
            return error_result(
                database_path=requested_path,
                error_type="reserved_view_name",
                message="View name must not start with 'sqlite_'.",
                view_name=normalized_view_name,
            )
        if not is_view_sql(query_sql):
            return error_result(
                database_path=requested_path,
                error_type="disallowed_sql",
                message="Only read-only SELECT and WITH queries can be saved as views.",
                view_name=normalized_view_name,
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
                    )
                if not replace:
                    return error_result(
                        database_path=resolved_path,
                        error_type="view_exists",
                        message=f"SQLite view already exists: {normalized_view_name}",
                        view_name=normalized_view_name,
                    )
                connection.execute(f"DROP VIEW IF EXISTS {quote_identifier(normalized_view_name)}")

            connection.execute(f"CREATE VIEW {quote_identifier(normalized_view_name)} AS {query_sql}")
            available_artifact_names = {
                cast(str, row[0])
                for row in connection.execute(
                    """
                    SELECT name
                    FROM sqlite_master
                    WHERE type IN ('table', 'view')
                      AND name NOT LIKE 'sqlite_%'
                    """
                ).fetchall()
            }
            dependency_names = referenced_artifact_names(
                query_sql,
                available_artifact_names=available_artifact_names,
                current_artifact_name=normalized_view_name,
            )
            register_saved_view_relationships(
                connection,
                view_name=normalized_view_name,
                sql=query_sql,
                sql_file_path=sql_file_path,
                dependency_names=dependency_names,
            )
            connection.commit()

        description = describe_sql_artifact(
            normalized_view_name,
            root_dir=root_dir,
            database_path=resolved_path,
        )
        return {
            "database_path": str(resolved_path),
            "status": "ok",
            "view_name": normalized_view_name,
            "sql_artifact": description,
        }
    except ValueError as exc:
        return error_result(
            database_path=requested_path,
            error_type="missing_database",
            message=str(exc),
            view_name=normalized_view_name,
        )
    except (sqlite3.Error, sqlite3.Warning) as exc:
        return error_result(
            database_path=requested_path,
            error_type="sql_execution_error",
            message=str(exc),
            view_name=normalized_view_name,
        )
