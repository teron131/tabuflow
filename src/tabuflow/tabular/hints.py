"""Shared tabular structure hints."""

from __future__ import annotations

from typing import Any


def structure_hints(
    *,
    header_candidate_rows: list[dict[str, Any]],
    regions: list[dict[str, Any]],
    sheet_names: list[str] | None = None,
) -> dict[str, Any]:
    """Build compact hints for agents before they inspect raw rows manually."""
    stable_candidates = [candidate for candidate in header_candidate_rows if not candidate.get("has_stronger_header_ahead")]
    header_pool = stable_candidates or header_candidate_rows
    best_header = max(header_pool, key=lambda candidate: (candidate.get("non_empty_cells", 0), -candidate.get("row", 0))) if header_pool else None
    suggested_start_row = best_header["row"] if best_header else None
    return {
        "likely_header_row": suggested_start_row,
        "suggested_data_start_row": suggested_start_row + 1 if suggested_start_row else None,
        "header_candidate_count": len(header_candidate_rows),
        "region_count": len(regions),
        "sheet_names": sheet_names or [],
    }
