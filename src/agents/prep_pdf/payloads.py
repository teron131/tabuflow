"""Compact payload helpers for the prep_pdf stage."""

from __future__ import annotations

from typing import Any

MAX_EXTRACTED_SQL_ARTIFACT_PREVIEW = 8


def _preview_list(
    items: list[Any],
    *,
    max_items: int,
) -> tuple[list[Any], bool]:
    """Return a bounded preview of one list plus truncation state."""
    safe_max_items = max(0, max_items)
    return items[:safe_max_items], len(items) > safe_max_items


def collect_extracted_sql_artifacts(extraction_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return compact extracted SQL artifact metadata for downstream packaging."""
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


def compact_extracted_sql_artifacts(sql_artifacts: list[dict[str, Any]]) -> dict[str, Any]:
    """Return a bounded preview of extracted SQL artifacts for prompts and logs."""
    preview, truncated = _preview_list(
        sql_artifacts,
        max_items=MAX_EXTRACTED_SQL_ARTIFACT_PREVIEW,
    )
    return {
        "count": len(sql_artifacts),
        "truncated": truncated,
        "items": preview,
    }
