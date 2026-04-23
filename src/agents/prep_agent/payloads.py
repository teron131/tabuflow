"""Compact payload helpers for the prep agent."""

from __future__ import annotations

from typing import Any

MAX_EXTRACTED_TARGET_PREVIEW = 8


def _preview_list(
    items: list[Any],
    *,
    max_items: int,
) -> tuple[list[Any], bool]:
    """Return a bounded preview of one list plus truncation state."""
    safe_max_items = max(0, max_items)
    return items[:safe_max_items], len(items) > safe_max_items


def collect_extracted_targets(extraction_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return compact extracted target metadata for downstream packaging."""
    targets: list[dict[str, Any]] = []
    for extraction in extraction_results:
        for table in extraction.get("tables", []):
            targets.append(
                {
                    "source_path": extraction.get("path"),
                    "table_name": table.get("table_name"),
                    "typed_view_name": table.get("typed_view_name"),
                    "row_count": table.get("row_count"),
                }
            )
    return targets


def compact_extracted_targets(targets: list[dict[str, Any]]) -> dict[str, Any]:
    """Return a bounded preview of extracted targets for prompts and logs."""
    preview, truncated = _preview_list(
        targets,
        max_items=MAX_EXTRACTED_TARGET_PREVIEW,
    )
    return {
        "count": len(targets),
        "truncated": truncated,
        "items": preview,
    }
