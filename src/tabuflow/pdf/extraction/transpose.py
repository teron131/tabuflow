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
class RepeatedLabelTranspose:
    """Represent a repeated-label row stream promoted to columnar rows."""

    rows: list[dict[str, str]]
    columns: list[str]
    evidence: dict[str, Any]


@dataclass(frozen=True)
class RepeatedLabelTransposer:
    """Promote repeated label/value rows into columnar parent rows."""

    rows: list[dict[str, str]]
    columns: list[str]
    label_column: str
    value_column: str
    table_end_patterns: list[RowPattern]
    row_metadata: list[dict[str, Any]]
    mode: str = "auto"
    entity_column: str = "item"
    total_column: str = "total"

    def transpose(self) -> RepeatedLabelTranspose | None:
        """Return columnar rows when repeated-label evidence is strong enough."""
        mode = self.mode if self.mode in TRANSPOSE_MODES else "auto"
        if mode == "never" or len(self.rows) < 3:
            return None

        key_labels, reason = self._key_labels()
        if mode == "auto" and len(key_labels) < MIN_AUTO_KEY_LABELS:
            return None
        if mode == "always" and not key_labels:
            key_labels = self._repeated_labels()
        if not key_labels:
            return None

        key_columns = _key_columns(
            key_labels,
            reserved={
                *self.context_columns,
                self.entity_column,
                self.total_column,
            },
        )
        transposed_rows = self._transposed_rows(key_columns)
        has_parent_row = any(row.get(self.entity_column) and any(row.get(column) for column in key_columns.values()) for row in transposed_rows)
        if mode == "auto" and not has_parent_row:
            return None

        output_columns = [*self.context_columns, self.entity_column, self.total_column, *key_columns.values()]
        evidence = {
            "status": "applied",
            "mode": mode,
            "reason": reason,
            "label_column": self.label_column,
            "value_column": self.value_column,
            "entity_column": self.entity_column,
            "total_column": self.total_column,
            "key_labels": key_labels,
            "row_count_before": len(self.rows),
            "row_count_after": len(transposed_rows),
        }
        return RepeatedLabelTranspose(
            rows=transposed_rows,
            columns=output_columns,
            evidence=evidence,
        )

    @property
    def context_columns(self) -> list[str]:
        """Return content columns carried unchanged through the transpose."""
        content_columns = {self.label_column, self.value_column, "page"}
        return [column for column in self.columns if column not in content_columns and not column.startswith("__")]

    def _key_labels(self) -> tuple[list[str], str]:
        key_labels = self._table_end_child_labels()
        if len(key_labels) >= MIN_AUTO_KEY_LABELS:
            return key_labels, "table_end_child_label_sequence"
        return self._indent_child_labels(), "layout_child_indent"

    def _table_end_child_labels(self) -> list[str]:
        key_labels: list[str] = []
        segment_labels: list[str] = []
        child_labels: list[str] = []
        has_parent = False
        for row in self.rows:
            label = str(row.get(self.label_column, "")).strip()
            if self._matches_table_end(row):
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
            if self._is_context_parent(row, label=label):
                if has_parent:
                    _extend_unique(key_labels, child_labels)
                child_labels = []
                has_parent = True
                continue
            if has_parent:
                child_labels.append(label)
        return key_labels

    def _is_context_parent(
        self,
        row: dict[str, str],
        *,
        label: str,
    ) -> bool:
        return any(label == str(row.get(column, "")).strip() for column in self.context_columns)

    def _indent_child_labels(self) -> list[str]:
        if not self.row_metadata:
            return []
        key_labels: list[str] = []
        child_labels: list[str] = []
        parent_x0: float | None = None
        parent_count = 0
        for row_index, row in enumerate(self.rows):
            label = str(row.get(self.label_column, "")).strip()
            label_x0 = _label_x0(self.row_metadata, row_index)
            if not label or label_x0 is None:
                continue
            if _starts_outdented_footer(label_x0, parent_x0):
                if child_labels:
                    _extend_unique(key_labels, child_labels)
                child_labels = []
                parent_x0 = None
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

    def _repeated_labels(self) -> list[str]:
        counts = Counter(str(row.get(self.label_column, "")).strip() for row in self.rows)
        return [label for label, count in counts.items() if label and count > 1]

    def _transposed_rows(self, key_columns: dict[str, str]) -> list[dict[str, str]]:
        transposed_rows: list[dict[str, str]] = []
        current: dict[str, str] | None = None
        current_parent_x0: float | None = None
        for row_index, row in enumerate(self.rows):
            label = str(row.get(self.label_column, "")).strip()
            value = str(row.get(self.value_column, "")).strip()
            label_x0 = _label_x0(self.row_metadata, row_index)
            if self._matches_table_end(row):
                _append_current(transposed_rows, current)
                transposed_rows.append(self._table_end_row(row, label, value))
                current = None
                current_parent_x0 = None
                continue
            if _starts_outdented_footer(label_x0, current_parent_x0):
                _append_current(transposed_rows, current)
                transposed_rows.append(self._table_end_row(row, label, value))
                current = None
                current_parent_x0 = None
                continue
            if _starts_aligned_parent(label_x0, current_parent_x0):
                _append_current(transposed_rows, current)
                current = self._parent_row(row, label, value)
                current_parent_x0 = label_x0
                continue
            if label in key_columns:
                if current is None:
                    current = self._parent_row(row, "", "")
                current[_unique_key(current, key_columns[label])] = value
                continue
            _append_current(transposed_rows, current)
            current = self._parent_row(row, label, value)
            current_parent_x0 = label_x0

        _append_current(transposed_rows, current)
        return transposed_rows

    def _matches_table_end(self, row: dict[str, str]) -> bool:
        return any(pattern.match(str(row.get(column, ""))) for column, pattern in self.table_end_patterns)

    def _parent_row(
        self,
        row: dict[str, str],
        entity: str,
        total: str,
    ) -> dict[str, str]:
        return {
            **{column: str(row.get(column, "")) for column in self.context_columns},
            self.entity_column: entity,
            self.total_column: total,
        }

    def _table_end_row(
        self,
        row: dict[str, str],
        entity: str,
        total: str,
    ) -> dict[str, str]:
        footer_row = self._parent_row(row, entity, total)
        if entity.lower().startswith("total ") and "account" in self.context_columns:
            footer_row["account"] = ""
        return footer_row


def transpose_repeated_label_rows(
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
) -> RepeatedLabelTranspose | None:
    """Promote deterministic repeated label/value rows into one row per parent item."""
    return RepeatedLabelTransposer(
        rows=rows,
        columns=columns,
        label_column=label_column,
        value_column=value_column,
        table_end_patterns=table_end_patterns,
        row_metadata=row_metadata or [],
        mode=mode,
        entity_column=entity_column,
        total_column=total_column,
    ).transpose()


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


def _extend_unique(
    target: list[str],
    values: list[str],
) -> None:
    seen = set(target)
    for value in values:
        if value and value not in seen:
            target.append(value)
            seen.add(value)


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


def _starts_aligned_parent(
    label_x0: float | None,
    current_parent_x0: float | None,
) -> bool:
    if label_x0 is None or current_parent_x0 is None:
        return False
    return label_x0 <= current_parent_x0 + INDENT_PARENT_TOLERANCE


def _starts_outdented_footer(
    label_x0: float | None,
    current_parent_x0: float | None,
) -> bool:
    if label_x0 is None or current_parent_x0 is None:
        return False
    return label_x0 < current_parent_x0 - INDENT_PARENT_TOLERANCE


def _append_current(
    output_rows: list[dict[str, str]],
    current: dict[str, str] | None,
) -> None:
    if current is None:
        return
    output_rows.append(current)


def _unique_key(row: dict[str, str], column: str) -> str:
    if column not in row:
        return column
    suffix = 2
    while f"{column}_{suffix}" in row:
        suffix += 1
    return f"{column}_{suffix}"
