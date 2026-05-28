"""Transpose repeated label/value PDF row streams into columnar tables."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import re
from typing import Any

COLUMN_UNSAFE_PATTERN = re.compile(r"[^a-z0-9]+")
INDENT_PARENT_TOLERANCE = 3.0
MIN_CHILD_INDENT_DELTA = 4.0
MIN_AUTO_KEY_LABELS = 2
MIN_INDENT_PARENT_GROUPS = 1
TRANSPOSE_MODES = {"auto", "always", "never"}
RowPattern = tuple[str, re.Pattern[str]]


@dataclass(frozen=True)
class RepeatedLabelColumns:
    """Represent a repeated-label row stream promoted to columnar rows."""

    rows: list[dict[str, str]]
    columns: list[str]
    evidence: dict[str, Any]


def repeated_label_column_transform(
    rows: list[dict[str, str]],
    columns: list[str],
    *,
    label_column: str,
    value_column: str,
    table_end_patterns: list[RowPattern],
    row_metadata: list[dict[str, Any]] | None = None,
    mode: str = "auto",
    entity_column: str = "item",
    total_column: str = "total",
) -> RepeatedLabelColumns | None:
    """Promote deterministic repeated label/value rows into one row per parent item."""
    mode = mode if mode in TRANSPOSE_MODES else "auto"
    if mode == "never" or len(rows) < 3:
        return None

    context_columns = [column for column in columns if column not in {label_column, value_column, "page"} and not column.startswith("__")]
    key_labels = _table_end_child_labels(
        rows,
        label_column=label_column,
        table_end_patterns=table_end_patterns,
        context_columns=context_columns,
    )
    reason = "table_end_child_label_sequence"
    if len(key_labels) < MIN_AUTO_KEY_LABELS:
        key_labels = _indent_child_labels(
            rows,
            label_column=label_column,
            row_metadata=row_metadata,
        )
        reason = "layout_child_indent"
    if mode == "auto" and len(key_labels) < MIN_AUTO_KEY_LABELS:
        return None
    if mode == "always" and not key_labels:
        key_labels = _repeated_labels(rows, label_column=label_column)
    if not key_labels:
        return None

    key_columns = _key_columns(key_labels, reserved={*context_columns, entity_column, total_column})
    transformed_rows = _transformed_rows(
        rows,
        context_columns=context_columns,
        label_column=label_column,
        value_column=value_column,
        table_end_patterns=table_end_patterns,
        row_metadata=row_metadata,
        key_columns=key_columns,
        entity_column=entity_column,
        total_column=total_column,
    )
    has_parent_row = any(row.get(entity_column) and any(row.get(column) for column in key_columns.values()) for row in transformed_rows)
    if mode == "auto" and not has_parent_row:
        return None

    output_columns = [*context_columns, entity_column, total_column, *key_columns.values()]
    evidence = {
        "status": "applied",
        "mode": mode,
        "reason": reason,
        "label_column": label_column,
        "value_column": value_column,
        "entity_column": entity_column,
        "total_column": total_column,
        "key_labels": key_labels,
        "row_count_before": len(rows),
        "row_count_after": len(transformed_rows),
    }
    return RepeatedLabelColumns(rows=transformed_rows, columns=output_columns, evidence=evidence)


def _table_end_child_labels(
    rows: list[dict[str, str]],
    *,
    label_column: str,
    table_end_patterns: list[RowPattern],
    context_columns: list[str],
) -> list[str]:
    key_labels: list[str] = []
    segment_labels: list[str] = []
    child_labels: list[str] = []
    has_parent = False
    for row in rows:
        label = str(row.get(label_column, "")).strip()
        if _matches_table_end(row, table_end_patterns):
            if has_parent:
                _extend_unique(key_labels, child_labels)
            else:
                _extend_unique(key_labels, segment_labels[1:])
            segment_labels = []
            child_labels = []
            has_parent = False
            continue
        if not label:
            continue
        segment_labels.append(label)
        if _is_context_parent(row, label=label, context_columns=context_columns):
            if has_parent:
                _extend_unique(key_labels, child_labels)
            child_labels = []
            has_parent = True
            continue
        if has_parent:
            child_labels.append(label)
    return key_labels


def _is_context_parent(
    row: dict[str, str],
    *,
    label: str,
    context_columns: list[str],
) -> bool:
    return any(label == str(row.get(column, "")).strip() for column in context_columns)


def _indent_child_labels(
    rows: list[dict[str, str]],
    *,
    label_column: str,
    row_metadata: list[dict[str, Any]] | None,
) -> list[str]:
    if not row_metadata:
        return []
    key_labels: list[str] = []
    child_labels: list[str] = []
    parent_x0: float | None = None
    parent_count = 0
    for row_index, row in enumerate(rows):
        label = str(row.get(label_column, "")).strip()
        label_x0 = _label_x0(row_metadata, row_index)
        if not label or label_x0 is None:
            continue
        if parent_x0 is None or label_x0 <= parent_x0 + INDENT_PARENT_TOLERANCE:
            if child_labels:
                _extend_unique(key_labels, child_labels)
            child_labels = []
            parent_x0 = label_x0
            parent_count += 1
            continue
        if label_x0 >= parent_x0 + MIN_CHILD_INDENT_DELTA:
            child_labels.append(label)
    if child_labels:
        _extend_unique(key_labels, child_labels)
    if parent_count < MIN_INDENT_PARENT_GROUPS:
        return []
    return key_labels


def _label_x0(
    row_metadata: list[dict[str, Any]],
    row_index: int,
) -> float | None:
    if row_index >= len(row_metadata):
        return None
    value = row_metadata[row_index].get("label_x0")
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _extend_unique(target: list[str], values: list[str]) -> None:
    seen = set(target)
    for value in values:
        if value and value not in seen:
            target.append(value)
            seen.add(value)


def _repeated_labels(
    rows: list[dict[str, str]],
    *,
    label_column: str,
) -> list[str]:
    counts = Counter(str(row.get(label_column, "")).strip() for row in rows)
    return [label for label, count in counts.items() if label and count > 1]


def _key_columns(
    labels: list[str],
    *,
    reserved: set[str],
) -> dict[str, str]:
    used = set(reserved)
    key_columns: dict[str, str] = {}
    for label in labels:
        base = COLUMN_UNSAFE_PATTERN.sub("_", label.lower()).strip("_") or "value"
        column = base
        suffix = 2
        while column in used:
            column = f"{base}_{suffix}"
            suffix += 1
        key_columns[label] = column
        used.add(column)
    return key_columns


def _transformed_rows(
    rows: list[dict[str, str]],
    *,
    context_columns: list[str],
    label_column: str,
    value_column: str,
    table_end_patterns: list[RowPattern],
    row_metadata: list[dict[str, Any]] | None,
    key_columns: dict[str, str],
    entity_column: str,
    total_column: str,
) -> list[dict[str, str]]:
    transformed_rows: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    current_parent_x0: float | None = None
    for row_index, row in enumerate(rows):
        label = str(row.get(label_column, "")).strip()
        value = str(row.get(value_column, "")).strip()
        label_x0 = _label_x0(row_metadata or [], row_index)
        if _matches_table_end(row, table_end_patterns):
            _append_current(transformed_rows, current)
            transformed_rows.append(
                _table_end_row(
                    row,
                    context_columns,
                    entity_column,
                    total_column,
                    label,
                    value,
                )
            )
            current = None
            current_parent_x0 = None
            continue
        if _starts_indented_parent(label_x0, current_parent_x0):
            _append_current(transformed_rows, current)
            current = _parent_row(row, context_columns, entity_column, total_column, label, value)
            current_parent_x0 = label_x0
            continue
        if label in key_columns:
            if current is None:
                current = _parent_row(row, context_columns, entity_column, total_column, "", "")
            current[_unique_key(current, key_columns[label])] = value
            continue
        _append_current(transformed_rows, current)
        current = _parent_row(row, context_columns, entity_column, total_column, label, value)
        current_parent_x0 = label_x0

    _append_current(transformed_rows, current)
    return transformed_rows


def _starts_indented_parent(
    label_x0: float | None,
    current_parent_x0: float | None,
) -> bool:
    if label_x0 is None or current_parent_x0 is None:
        return False
    return label_x0 <= current_parent_x0 + INDENT_PARENT_TOLERANCE


def _matches_table_end(row: dict[str, str], patterns: list[RowPattern]) -> bool:
    return any(pattern.match(str(row.get(column, ""))) for column, pattern in patterns)


def _parent_row(
    row: dict[str, str],
    context_columns: list[str],
    entity_column: str,
    total_column: str,
    entity: str,
    total: str,
) -> dict[str, str]:
    return {
        **{column: str(row.get(column, "")) for column in context_columns},
        entity_column: entity,
        total_column: total,
    }


def _table_end_row(
    row: dict[str, str],
    context_columns: list[str],
    entity_column: str,
    total_column: str,
    entity: str,
    total: str,
) -> dict[str, str]:
    footer_row = _parent_row(row, context_columns, entity_column, total_column, entity, total)
    if entity.lower().startswith("total ") and "account" in context_columns:
        footer_row["account"] = ""
    return footer_row


def _append_current(
    transformed_rows: list[dict[str, str]],
    current: dict[str, str] | None,
) -> None:
    if current is None:
        return
    transformed_rows.append(current)


def _unique_key(row: dict[str, str], column: str) -> str:
    if column not in row:
        return column
    suffix = 2
    while f"{column}_{suffix}" in row:
        suffix += 1
    return f"{column}_{suffix}"
