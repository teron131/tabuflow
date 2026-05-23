"""Shared payload helpers for prepared data stages."""

from __future__ import annotations

from typing import Any


def collect_extracted_sql_artifacts(extraction_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return SQL artifact metadata collected from prep-stage extraction results."""
    sql_artifacts: list[dict[str, Any]] = []
    for extraction in extraction_results:
        for table in extraction.get("tables", []):
            sql_artifacts.append(
                {
                    "source_path": extraction.get("path"),
                    "table_name": table.get("table_name"),
                    "typed_view_name": table.get("typed_view_name"),
                    "row_count": table.get("row_count"),
                    "columns": table.get("columns") or [],
                    "db_columns": table.get("db_columns") or [],
                    "typed_columns": table.get("typed_columns") or [],
                }
            )
    return sql_artifacts
