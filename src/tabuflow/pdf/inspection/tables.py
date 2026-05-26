"""Table-detection hints for PDF inspection."""

from __future__ import annotations

import contextlib
import io
import re
from typing import Any

import pymupdf


def _clean_table_cell(value: Any) -> str:
    """Return compact display text for one PyMuPDF table cell."""
    text = " ".join(str(value or "").split())
    text = re.sub(r"(?<=\w)-\s+(?=\w)", "-", text)
    text = re.sub(r"(?<=\w)\.\s+(?=<\w)", ".", text)
    text = re.sub(r"\(\s+", "(", text)
    text = re.sub(r"\s+\)", ")", text)
    return re.sub(r"\s+([,.;:])", r"\1", text)


def _detected_table_metrics(
    rows: list[list[str]],
    header_names: list[str],
) -> dict[str, Any]:
    """Return compact deterministic metrics for one PyMuPDF table candidate."""
    allowed_short_headers = {"id", "ip", "no", "qty", "key"}
    row_count = len(rows)
    column_count = max((len(row) for row in rows), default=0)
    first_row = rows[0] if rows else []
    header_cells = [_clean_table_cell(name) for name in header_names] or [_clean_table_cell(value) for value in first_row]
    filled_first_row = sum(1 for value in first_row if value)
    short_header_cells = [value for value in header_cells if value and len(value) <= 3 and value.lower() not in allowed_short_headers]
    non_empty_cells = sum(1 for row in rows for value in row if value)
    return {
        "row_count": row_count,
        "column_count": column_count,
        "filled_first_row_cells": filled_first_row,
        "short_header_cell_count": len(short_header_cells),
        "header_cell_count": len(header_cells),
        "non_empty_cell_count": non_empty_cells,
    }


def detected_table_diagnostics(
    rows: list[list[str]],
    header_names: list[str],
) -> dict[str, Any]:
    """Return deterministic diagnostics for one PyMuPDF table candidate."""
    metrics = _detected_table_metrics(rows, header_names)
    warnings: list[str] = []
    if metrics["row_count"] < 2:
        warnings.append("too_few_rows")
    if metrics["column_count"] < 2:
        warnings.append("too_few_columns")
    if metrics["short_header_cell_count"]:
        warnings.append("short_header_fragments")
    if metrics["non_empty_cell_count"] < metrics["row_count"] * max(metrics["column_count"], 1) * 0.25:
        warnings.append("sparse_cells")
    return {
        "warnings": warnings,
        "filled_first_row_cells": metrics["filled_first_row_cells"],
        "non_empty_cell_count": metrics["non_empty_cell_count"],
    }


def detected_table_quality(
    rows: list[list[str]],
    header_names: list[str],
) -> dict[str, Any]:
    """Return compact inspection quality for one PyMuPDF table candidate."""
    metrics = _detected_table_metrics(rows, header_names)
    plausible = (
        metrics["row_count"] >= 2
        and metrics["column_count"] >= 2
        and metrics["filled_first_row_cells"] >= 2
        and metrics["short_header_cell_count"] < max(2, metrics["header_cell_count"] // 2)
    )
    return {
        "quality": "plausible" if plausible else "suspicious",
        **detected_table_diagnostics(rows, header_names),
    }


def _drop_empty_table_columns(
    rows: list[list[str]],
    header_names: list[str],
) -> tuple[list[list[str]], list[str], list[int]]:
    """Remove columns that only exist as PyMuPDF spacer cells."""
    column_count = max([len(header_names), *(len(row) for row in rows)], default=0)
    keep_indexes: list[int] = []
    dropped_indexes: list[int] = []
    for column_index in range(column_count):
        header_value = header_names[column_index] if column_index < len(header_names) else ""
        column_values = [row[column_index] for row in rows if column_index < len(row)]
        if header_value or any(column_values):
            keep_indexes.append(column_index)
        else:
            dropped_indexes.append(column_index)

    compact_header_names = [header_names[column_index] if column_index < len(header_names) else "" for column_index in keep_indexes]
    compact_rows = [[row[column_index] if column_index < len(row) else "" for column_index in keep_indexes] for row in rows]
    return compact_rows, compact_header_names, dropped_indexes


def _merge_unnamed_table_columns(
    rows: list[list[str]],
    header_names: list[str],
) -> tuple[list[list[str]], list[str], list[int]]:
    """Fold unnamed spacer/header columns into the previous named column."""
    if not header_names:
        return rows, header_names, []

    named_indexes = [column_index for column_index, header in enumerate(header_names) if header]
    if not named_indexes:
        return rows, header_names, []

    merged_indexes: list[int] = []
    output_header_names = [header_names[column_index] for column_index in named_indexes]
    output_rows: list[list[str]] = []
    target_by_named_index = {column_index: target_index for target_index, column_index in enumerate(named_indexes)}
    target_by_index: dict[int, int] = {}
    current_target = 0
    for column_index, header in enumerate(header_names):
        if header:
            current_target = target_by_named_index[column_index]
        else:
            merged_indexes.append(column_index)
        target_by_index[column_index] = current_target

    for row in rows:
        output_row = ["" for _header in output_header_names]
        for column_index, value in enumerate(row):
            if column_index not in target_by_index:
                continue
            target_index = target_by_index[column_index]
            output_row[target_index] = " ".join(part for part in (output_row[target_index], value) if part).strip()
        output_rows.append(output_row)
    return output_rows, output_header_names, merged_indexes


def _normalized_cell_text(value: str) -> str:
    """Return a coarse comparable form for duplicate wrapped-cell fragments."""
    return "".join(character.lower() for character in value if character.isalnum())


def _join_cell_fragment(
    existing: str,
    fragment: str,
) -> str:
    """Join wrapped cell text while avoiding duplicate and hyphen-split fragments."""
    if not fragment:
        return existing
    if not existing:
        return fragment
    existing_norm = _normalized_cell_text(existing)
    fragment_norm = _normalized_cell_text(fragment)
    if fragment_norm and (fragment_norm in existing_norm or existing_norm in fragment_norm):
        return existing
    if existing.endswith("-"):
        return f"{existing}{fragment.lstrip()}"
    return f"{existing} {fragment}"


def _merge_continuation_rows(rows: list[list[str]]) -> tuple[list[list[str]], int]:
    """Attach blank-first-cell continuation rows to the previous logical row."""
    merged_rows: list[list[str]] = []
    merged_count = 0
    for row in rows:
        normalized_row = [_clean_table_cell(value) for value in row]
        if not any(normalized_row):
            continue
        is_continuation = bool(merged_rows) and not normalized_row[0] and any(normalized_row[1:])
        if not is_continuation:
            merged_rows.append(normalized_row)
            continue
        previous_row = merged_rows[-1]
        if len(previous_row) < len(normalized_row):
            previous_row.extend("" for _index in range(len(normalized_row) - len(previous_row)))
        for column_index, value in enumerate(normalized_row[1:], start=1):
            previous_row[column_index] = _join_cell_fragment(previous_row[column_index], value)
        merged_count += 1
    return merged_rows, merged_count


def _repair_code_identifier(value: str) -> str:
    """Repair PyMuPDF spaces around underscores inside code identifiers."""
    tokens = [token for token in value.split() if token != "_"]
    if not tokens:
        return value
    return "_".join(token.strip("_") for token in tokens if token.strip("_"))


def _repair_code_like_value(value: str) -> str:
    """Repair obvious code/path values whose underscores were split into spaces."""
    if "$" not in value and "/" not in value:
        return value
    path_parts = value.split("/")
    repaired_parts = []
    for part in path_parts:
        tokens = [token for token in part.split() if token != "_"]
        repaired_parts.append("_".join(tokens) if len(tokens) > 1 else (tokens[0] if tokens else ""))
    return "/".join(repaired_parts)


def _repair_key_value_rows(
    rows: list[list[str]],
    header_names: list[str],
) -> list[list[str]]:
    """Repair code identifiers in Key/Default/Description table candidates."""
    if [header.lower() for header in header_names[:3]] != ["key", "default", "description"]:
        return rows

    repaired_rows: list[list[str]] = []
    for row_index, row in enumerate(rows):
        if row_index == 0:
            repaired_rows.append(row)
            continue
        repaired_row = list(row)
        if repaired_row:
            repaired_row[0] = _repair_code_identifier(repaired_row[0])
        if len(repaired_row) > 1:
            repaired_row[1] = _repair_code_like_value(repaired_row[1])
        repaired_rows.append(repaired_row)
    return repaired_rows


def _non_empty_rows(rows: list[list[str]]) -> list[list[str]]:
    """Return rows that contain at least one non-empty cell."""
    return [row for row in rows if any(row)]


def _candidate_headers_match_first_row(
    rows: list[list[str]],
    header_names: list[str],
) -> bool:
    """Return whether a candidate's first extracted row repeats the headers."""
    return bool(rows and header_names and rows[0][: len(header_names)] == header_names)


def candidate_interpretation(
    rows: list[list[str]],
    header_names: list[str],
    quality: dict[str, Any],
) -> dict[str, Any]:
    """Interpret one detection as a possible structured table region."""
    non_empty_rows = _non_empty_rows(rows)
    column_count = max((len(row) for row in rows), default=0)
    useful_data_rows = rows[1:] if _candidate_headers_match_first_row(rows, header_names) else rows

    if not non_empty_rows or column_count <= 1:
        return {
            "kind": "false_positive",
            "suggested_method": "ignore",
            "usable": False,
            "columns": header_names,
            "rows": [],
            "diagnostics": ["too_few_populated_rows_or_columns"],
        }

    if header_names and all(header_names) and quality.get("quality") == "plausible":
        return {
            "kind": "grid_table",
            "suggested_method": "detected_table",
            "usable": True,
            "columns": header_names,
            "rows": _non_empty_rows(useful_data_rows),
            "diagnostics": ["plausible_headers", "plausible_cell_density"],
        }

    if column_count == 2 and all(len(row) >= 2 for row in useful_data_rows):
        field_value_rows = [row[:2] for row in useful_data_rows if row[0] and row[1]]
        if field_value_rows:
            return {
                "kind": "field_value_table",
                "suggested_method": "field_value",
                "usable": True,
                "columns": ["Field", "Value"],
                "rows": field_value_rows,
                "diagnostics": ["two_column_label_value_shape"],
            }

    return {
        "kind": "uncertain",
        "suggested_method": "inspect_more",
        "usable": False,
        "columns": header_names,
        "rows": _non_empty_rows(useful_data_rows),
        "diagnostics": ["needs_visual_or_geometry_verification"],
    }


def _bounded_interpretation(
    interpretation: dict[str, Any],
    *,
    max_rows: int,
) -> dict[str, Any]:
    """Return an interpretation with a bounded row preview."""
    rows = interpretation.get("rows", [])
    if not isinstance(rows, list) or len(rows) <= max_rows:
        return {**interpretation, "rows_truncated": False}
    return {
        **interpretation,
        "rows": rows[:max_rows],
        "rows_truncated": True,
    }


def _candidate_plan_item(
    page_payload: dict[str, Any],
    candidate: dict[str, Any],
) -> dict[str, Any]:
    """Return compact candidate identity and rows for extraction plan hints."""
    interpretation = candidate["interpretation"]
    return {
        "page": page_payload["page_number"],
        "table_id": candidate["table_id"],
        "bbox": candidate["bbox"],
        "page_height": candidate["page_height"],
        "columns": interpretation["columns"],
        "rows": interpretation["rows"],
    }


def _field_value_groups(pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group adjacent field/value fragments that look like one logical spec."""
    groups: list[dict[str, Any]] = []
    current_group: dict[str, Any] | None = None
    for page_payload in pages:
        for candidate in page_payload.get("table_detections", {}).get("detections", []):
            interpretation = candidate["interpretation"]
            if not interpretation["usable"] or interpretation["kind"] != "field_value_table":
                continue
            rows = interpretation["rows"]
            has_best_for = any(row and row[0].lower() == "best for" for row in rows)
            page_number = int(page_payload["page_number"])
            should_start_group = current_group is None or has_best_for or page_number > int(current_group["pages"][-1]) + 1
            if should_start_group:
                current_group = {
                    "kind": "field_value_table",
                    "suggested_method": "field_value",
                    "pages": [],
                    "columns": ["Field", "Value"],
                    "source_detections": [],
                    "rows": [],
                }
                groups.append(current_group)
            if page_number not in current_group["pages"]:
                current_group["pages"].append(page_number)
            current_group["source_detections"].append(_candidate_plan_item(page_payload, candidate))
            current_group["rows"].extend(rows)
    return groups


def _grid_groups(pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group adjacent grid candidates that visibly continue across a page break."""
    groups: list[dict[str, Any]] = []
    for page_payload in pages:
        for candidate in page_payload.get("table_detections", {}).get("detections", []):
            interpretation = candidate["interpretation"]
            if not interpretation["usable"] or interpretation["kind"] != "grid_table":
                continue
            page_number = int(page_payload["page_number"])
            columns = interpretation["columns"]
            bbox = candidate["bbox"]
            page_height = float(candidate["page_height"])
            previous_group = groups[-1] if groups else None
            should_continue = False
            if previous_group and previous_group["columns"] == columns and page_number == int(previous_group["pages"][-1]) + 1:
                previous_bbox = previous_group["source_detections"][-1]["bbox"]
                previous_page_height = float(previous_group["source_detections"][-1]["page_height"])
                should_continue = float(previous_bbox[3]) >= previous_page_height * 0.75 and float(bbox[1]) <= page_height * 0.25
            if not should_continue:
                previous_group = {
                    "kind": "grid_table",
                    "suggested_method": "detected_table",
                    "pages": [],
                    "columns": columns,
                    "source_detections": [],
                    "rows": [],
                }
                groups.append(previous_group)
            if page_number not in previous_group["pages"]:
                previous_group["pages"].append(page_number)
            previous_group["source_detections"].append(_candidate_plan_item(page_payload, candidate))
            previous_group["rows"].extend(interpretation["rows"])
    return groups


def table_region_hints(pages: list[dict[str, Any]]) -> dict[str, Any]:
    """Return script-like candidate groups for an agent to assemble final tables."""
    return {
        "field_value_groups": _field_value_groups(pages),
        "grid_groups": _grid_groups(pages),
    }


def table_detections(
    page: pymupdf.Page,
    *,
    max_tables: int = 8,
    max_rows: int = 30,
) -> dict[str, Any]:
    """Return bounded PyMuPDF table detections for inspected pages."""
    with contextlib.redirect_stdout(io.StringIO()):
        tables = page.find_tables()
    detections: list[dict[str, Any]] = []
    for table_index, table in enumerate(tables.tables[:max_tables], start=1):
        raw_rows = [[_clean_table_cell(cell) for cell in row] for row in table.extract()]
        raw_header_names = [_clean_table_cell(name) for name in table.header.names]
        rows, header_names, dropped_indexes = _drop_empty_table_columns(raw_rows, raw_header_names)
        rows, header_names, merged_indexes = _merge_unnamed_table_columns(rows, header_names)
        rows, merged_continuation_row_count = _merge_continuation_rows(rows)
        rows = _repair_key_value_rows(rows, header_names)
        quality = detected_table_quality(rows, header_names)
        interpretation = _bounded_interpretation(candidate_interpretation(rows, header_names, quality), max_rows=max_rows)
        detection = {
            "table_id": table_index,
            "bbox": [round(float(value), 1) for value in table.bbox],
            "page_height": round(float(page.rect.height), 1),
            "page_width": round(float(page.rect.width), 1),
            "row_count": len(rows),
            "column_count": max((len(row) for row in rows), default=0),
            "quality": quality,
            "interpretation": interpretation,
        }
        repair_counts = {
            "dropped_spacer_columns": len(dropped_indexes),
            "merged_unnamed_columns": len(merged_indexes),
            "merged_continuation_rows": merged_continuation_row_count,
        }
        if any(repair_counts.values()):
            detection["repair_counts"] = repair_counts
        detections.append(detection)
    return {
        "source": "pymupdf_find_tables_default",
        "detection_count": len(tables.tables),
        "detections": detections,
        "truncated": len(tables.tables) > max_tables,
    }
