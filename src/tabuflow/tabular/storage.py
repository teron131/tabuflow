"""Tabular storage and fingerprinting helpers."""

from __future__ import annotations

from collections.abc import Iterable
import hashlib
from itertools import islice
import json
from pathlib import Path
import re
import sqlite3
import time
from typing import Any, cast

from ..artifacts.naming import normalize_source_stem
from ..artifacts.relationships import ensure_artifact_relationship_tables, register_source_table_relationships
from ..workspace_db import (
    SQLITE_CONTENTS_TABLE,
    SQLITE_SOURCES_TABLE,
    quote_identifier,
    sqlite_database_path,
    sqlite_write_lock,
)

INSERT_BATCH_SIZE = 1000
DATE_PATTERNS = ("%Y-%m-%d", "%Y/%m/%d")
DATETIME_PATTERNS = (
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d %H:%M:%S",
    "%Y/%m/%d %H:%M",
    "%Y/%m/%d %H:%M:%S",
    "%Y-%m-%dT%H:%M",
    "%Y-%m-%dT%H:%M:%S",
)
INTEGER_PATTERN = re.compile(r"^[+-]?\d+$")
REAL_PATTERN = re.compile(r"^[+-]?(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d+)?$")
SQLITE_SOURCES_COLUMNS = (
    "source_path",
    "source_format",
    "source_sheet_name",
    "source_table_name",
    "fingerprint",
)
SQLITE_SOURCES_UNIQUE_COLUMNS = (
    "source_path",
    "source_sheet_name",
    "source_table_name",
    "fingerprint",
)
SQLITE_SOURCES_COLUMN_SET = frozenset(SQLITE_SOURCES_COLUMNS)
SQLITE_CONTENTS_COLUMNS = (
    "fingerprint",
    "table_name",
    "source_format",
    "row_count",
    "column_schema_json",
)
SQLITE_CONTENTS_COLUMN_SET = frozenset(SQLITE_CONTENTS_COLUMNS)


def _update_hash_rows(
    hasher: hashlib._Hash,
    rows: Iterable[list[str]],
    *,
    row_limit: int,
) -> None:
    """Update a hash from a bounded set of normalized rows."""
    for row in islice(rows, row_limit):
        for cell in row:
            hasher.update(cell.encode("utf-8"))
            hasher.update(b"\x1f")
        hasher.update(b"\x1e")


def fingerprint(columns: list[str], rows: list[list[str]]) -> str:
    """Build an exact table-content fingerprint from ordered columns and rows."""
    hasher = hashlib.sha256()
    _update_hash_rows(hasher, [columns], row_limit=1)
    hasher.update(b"\x1a")
    _update_hash_rows(hasher, rows, row_limit=len(rows))
    return hasher.hexdigest()


def _db_column_names(columns: list[str]) -> list[str]:
    """Return unique SQL-safe column names while preserving order."""
    seen: dict[str, int] = {}
    normalized: list[str] = []

    for index, column in enumerate(columns, start=1):
        base_name = column or f"column_{index}"
        suffix = seen.get(base_name, 0) + 1
        seen[base_name] = suffix
        normalized.append(base_name if suffix == 1 else f"{base_name}_{suffix}")

    return normalized


def _create_sqlite_sources_table(connection: sqlite3.Connection) -> None:
    """Create the source-linkage table for extracted content."""
    connection.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {quote_identifier(SQLITE_SOURCES_TABLE)} (
            source_path TEXT NOT NULL,
            source_format TEXT NOT NULL,
            source_sheet_name TEXT NOT NULL,
            source_table_name TEXT NOT NULL,
            fingerprint TEXT NOT NULL,
            UNIQUE(source_path, source_sheet_name, source_table_name, fingerprint)
        )
        """
    )


def _create_sqlite_contents_table(connection: sqlite3.Connection) -> None:
    """Create the content catalog table keyed by exact table fingerprint."""
    connection.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {quote_identifier(SQLITE_CONTENTS_TABLE)} (
            fingerprint TEXT PRIMARY KEY,
            table_name TEXT NOT NULL,
            source_format TEXT NOT NULL,
            row_count INTEGER NOT NULL,
            column_schema_json TEXT NOT NULL
        )
        """
    )


def _sqlite_table_columns(
    connection: sqlite3.Connection,
    table_name: str,
) -> list[str]:
    """Return the current column names for a SQLite table."""
    return [cast(str, row[1]) for row in connection.execute(f"PRAGMA table_info({quote_identifier(table_name)})").fetchall()]


def _sqlite_unique_indexes(
    connection: sqlite3.Connection,
    table_name: str,
) -> list[tuple[str, ...]]:
    """Return unique-index column tuples for a SQLite table."""
    unique_indexes: list[tuple[str, ...]] = []
    for _, index_name, is_unique, *_ in connection.execute(f"PRAGMA index_list({quote_identifier(table_name)})").fetchall():
        if not is_unique:
            continue
        columns = tuple(cast(str, row[2]) for row in connection.execute(f"PRAGMA index_info({quote_identifier(cast(str, index_name))})").fetchall())
        unique_indexes.append(columns)
    return unique_indexes


def _ensure_sqlite_contents_table_schema(connection: sqlite3.Connection) -> None:
    """Create `_tabular_contents` and require the current fingerprint-only schema."""
    columns = _sqlite_table_columns(connection, SQLITE_CONTENTS_TABLE)
    if not columns:
        _create_sqlite_contents_table(connection)
        return

    existing_columns = set(columns)
    if existing_columns == SQLITE_CONTENTS_COLUMN_SET:
        return

    raise ValueError(f"`{SQLITE_CONTENTS_TABLE}` must use the fingerprint-only schema.")


def _ensure_sqlite_sources_table_schema(connection: sqlite3.Connection) -> None:
    """Create `_tabular_sources` and require the current fingerprint-only schema."""
    columns = _sqlite_table_columns(connection, SQLITE_SOURCES_TABLE)
    if not columns:
        _create_sqlite_sources_table(connection)
        return

    existing_columns = set(columns)
    has_expected_columns = existing_columns == SQLITE_SOURCES_COLUMN_SET
    has_expected_unique_key = SQLITE_SOURCES_UNIQUE_COLUMNS in _sqlite_unique_indexes(connection, SQLITE_SOURCES_TABLE)
    if has_expected_columns and has_expected_unique_key:
        return

    raise ValueError(f"`{SQLITE_SOURCES_TABLE}` must use the fingerprint-only schema.")


def _ensure_sqlite_catalog(connection: sqlite3.Connection) -> None:
    """Create the shared SQLite catalog tables when missing."""
    _ensure_sqlite_contents_table_schema(connection)
    _ensure_sqlite_sources_table_schema(connection)

    ensure_artifact_relationship_tables(connection)


def _clean_numeric_text(value: str) -> str:
    """Normalize a numeric-looking string for safe SQL casting."""
    return value.strip().replace(",", "")


def _is_integer_value(value: str) -> bool:
    """Return whether a string safely represents an integer."""
    return bool(INTEGER_PATTERN.fullmatch(_clean_numeric_text(value)))


def _is_real_value(value: str) -> bool:
    """Return whether a string safely represents a real number."""
    normalized = _clean_numeric_text(value)
    return "." in normalized and bool(REAL_PATTERN.fullmatch(normalized))


def _matches_datetime_patterns(
    value: str,
    patterns: tuple[str, ...],
) -> bool:
    """Return whether a string matches one of the accepted datetime patterns."""
    for pattern in patterns:
        try:
            time.strptime(value.strip(), pattern)
            return True
        except ValueError:
            continue
    return False


def _infer_column_type(values: Iterable[str]) -> str:
    """Infer a conservative SQLite affinity for a column."""
    non_empty_values = [value.strip() for value in values if value.strip()]
    if not non_empty_values:
        return "TEXT"
    if all(_is_integer_value(value) for value in non_empty_values):
        return "INTEGER"
    if all(_is_integer_value(value) or _is_real_value(value) for value in non_empty_values):
        return "REAL"
    if all(_matches_datetime_patterns(value, DATETIME_PATTERNS) for value in non_empty_values):
        return "DATETIME"
    if all(_matches_datetime_patterns(value, DATE_PATTERNS) for value in non_empty_values):
        return "DATE"
    return "TEXT"


def _typed_view_expression(column_name: str, column_type: str) -> str:
    """Build a typed SQLite view expression for a raw text column."""
    identifier = quote_identifier(column_name)
    trimmed = f"TRIM({identifier})"
    empty_to_null = f"NULLIF({trimmed}, '')"
    normalized_numeric = f"REPLACE({trimmed}, ',', '')"
    normalized_date = f"REPLACE({trimmed}, '/', '-')"

    if column_type == "INTEGER":
        return f"CASE WHEN {trimmed} = '' THEN NULL ELSE CAST({normalized_numeric} AS INTEGER) END AS {identifier}"
    if column_type == "REAL":
        return f"CASE WHEN {trimmed} = '' THEN NULL ELSE CAST({normalized_numeric} AS REAL) END AS {identifier}"
    if column_type == "DATE":
        return f"CASE WHEN {trimmed} = '' THEN NULL ELSE date({normalized_date}) END AS {identifier}"
    if column_type == "DATETIME":
        return f"CASE WHEN {trimmed} = '' THEN NULL ELSE datetime({normalized_date}) END AS {identifier}"
    return f"{empty_to_null} AS {identifier}"


def _create_typed_sqlite_view(
    connection: sqlite3.Connection,
    *,
    table_name: str,
    db_columns: list[str],
    rows: list[list[str]],
) -> tuple[str, dict[str, str]]:
    """Create or replace a typed view alongside a raw extracted table."""
    typed_view_name = f"{table_name}_typed"
    inferred_types = {column_name: _infer_column_type(row[column_index] if column_index < len(row) else "" for row in rows) for column_index, column_name in enumerate(db_columns)}
    select_sql = ", ".join(_typed_view_expression(column_name, inferred_types[column_name]) for column_name in db_columns)
    connection.execute(f"DROP VIEW IF EXISTS {quote_identifier(typed_view_name)}")
    connection.execute(
        f"""
        CREATE VIEW {quote_identifier(typed_view_name)} AS
        SELECT {select_sql}
        FROM {quote_identifier(table_name)}
        """
    )
    return typed_view_name, inferred_types


def _available_content_table_name(
    *,
    source_path: str,
    connection: sqlite3.Connection,
) -> str:
    """Return an unused normalized filename-based table name."""
    base_name = normalize_source_stem(source_path)
    used_names = {
        cast(str, row[0])
        for row in connection.execute(
            f"SELECT table_name FROM {SQLITE_CONTENTS_TABLE}",
        ).fetchall()
    }
    used_names.update(cast(str, row[0]) for row in connection.execute("SELECT name FROM sqlite_master WHERE name IS NOT NULL").fetchall())

    index = 1
    while True:
        table_name = base_name if index == 1 else f"{base_name}_{index}"
        if table_name not in used_names and f"{table_name}_typed" not in used_names:
            return table_name
        index += 1


def _create_sqlite_table(
    connection: sqlite3.Connection,
    *,
    table_name: str,
    columns: list[str],
    rows: list[list[str]],
) -> None:
    """Create and populate a SQLite table from normalized text rows."""
    column_sql = ", ".join(f"{quote_identifier(column)} TEXT" for column in columns)
    connection.execute(f"CREATE TABLE {quote_identifier(table_name)} ({column_sql})")
    placeholder_sql = ", ".join("?" for _ in columns)
    insert_sql = f"INSERT INTO {quote_identifier(table_name)} VALUES ({placeholder_sql})"

    for start_index in range(0, len(rows), INSERT_BATCH_SIZE):
        batch = rows[start_index : start_index + INSERT_BATCH_SIZE]
        connection.executemany(insert_sql, batch)


def _load_or_reuse_content_table(
    connection: sqlite3.Connection,
    *,
    columns: list[str],
    rows: list[list[str]],
    source_path: str,
    source_format: str,
) -> tuple[str, str, list[str], str]:
    """Ensure a raw content table exists and return its storage metadata."""
    table_fingerprint = fingerprint(columns, rows)
    db_columns = _db_column_names(columns)
    existing = connection.execute(
        f"SELECT table_name FROM {SQLITE_CONTENTS_TABLE} WHERE fingerprint = ?",
        [table_fingerprint],
    ).fetchone()
    if existing is not None:
        return table_fingerprint, cast(str, existing[0]), db_columns, "reused"

    table_name = _available_content_table_name(
        source_path=source_path,
        connection=connection,
    )
    _create_sqlite_table(
        connection,
        table_name=table_name,
        columns=db_columns,
        rows=rows,
    )
    connection.execute(
        f"""
        INSERT INTO {SQLITE_CONTENTS_TABLE}
        (fingerprint, table_name, source_format, row_count, column_schema_json)
        VALUES (?, ?, ?, ?, ?)
        """,
        [
            table_fingerprint,
            table_name,
            source_format,
            len(rows),
            json.dumps(
                {
                    "source_columns": columns,
                    "db_columns": db_columns,
                },
                separators=(",", ":"),
            ),
        ],
    )
    return table_fingerprint, table_name, db_columns, "loaded"


def _required_metadata_text(value: Any, field_name: str) -> str:
    """Return a non-empty metadata value or fail before writing partial lineage."""
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"Extracted table metadata requires `{field_name}`.")
    return text


def load_tables_into_sqlite(
    recovered: dict[str, Any],
    *,
    root_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Load recovered tables into a shared SQLite database."""
    database_path = sqlite_database_path(root_dir=root_dir)
    loaded_tables: list[dict[str, Any]] = []
    source_path = _required_metadata_text(recovered.get("path"), "path")
    source_format = _required_metadata_text(recovered.get("format"), "format")
    source_sheet_name = recovered.get("sheet_name") or ""

    with sqlite_write_lock(database_path):
        connection = sqlite3.connect(str(database_path))
        try:
            _ensure_sqlite_catalog(connection)

            for table in recovered["tables"]:
                source_table_name = _required_metadata_text(table.get("name"), "tables[].name")
                columns = list(table["columns"])
                rows = list(table["rows"])
                table_fingerprint, table_name, db_columns, load_status = _load_or_reuse_content_table(
                    connection,
                    columns=columns,
                    rows=rows,
                    source_path=source_path,
                    source_format=source_format,
                )
                typed_view_name, typed_columns = _create_typed_sqlite_view(
                    connection,
                    table_name=table_name,
                    db_columns=db_columns,
                    rows=rows,
                )
                connection.execute(
                    f"""
                    INSERT OR REPLACE INTO {SQLITE_SOURCES_TABLE}
                    (source_path, source_format, source_sheet_name, source_table_name, fingerprint)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    [
                        source_path,
                        source_format,
                        source_sheet_name,
                        source_table_name,
                        table_fingerprint,
                    ],
                )
                register_source_table_relationships(
                    connection,
                    source_path=source_path,
                    source_format=source_format,
                    source_sheet_name=source_sheet_name,
                    source_table_name=source_table_name,
                    fingerprint=table_fingerprint,
                    table_name=table_name,
                    typed_view_name=typed_view_name,
                )

                loaded_tables.append(
                    {
                        "source_name": source_table_name,
                        "fingerprint": table_fingerprint,
                        "table_name": table_name,
                        "typed_view_name": typed_view_name,
                        "row_count": len(rows),
                        "columns": columns,
                        "db_columns": db_columns,
                        "typed_columns": typed_columns,
                        "load_status": load_status,
                    }
                )
            connection.commit()
        finally:
            connection.close()

    return {
        "database_path": str(database_path),
        "tables": loaded_tables,
    }
