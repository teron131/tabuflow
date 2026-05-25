"""Detected-table cell cleanup and row-record helpers."""

from __future__ import annotations

import re
from typing import Any


def clean_cell(value: Any) -> str:
    """Return one table cell as single-line text."""
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return re.sub(r"(?<=[A-Za-z0-9])-\s+(?=[A-Za-z0-9])", "-", text)


def clean_extracted_table(
    rows: list[list[Any]],
    header_names: list[Any] | None = None,
) -> tuple[list[list[str]], list[str]]:
    """Normalize detected table rows and headers without drifting column indexes."""
    width = max([len(row) for row in rows] + [len(header_names or [])], default=0)
    cleaned = [[clean_cell(cell) for cell in [*row, *([None] * (width - len(row)))]] for row in rows]
    cleaned_header = [clean_cell(cell) for cell in [*(header_names or []), *([None] * (width - len(header_names or [])))]]
    nonblank_rows = [row for row in cleaned if any(row)]
    if not nonblank_rows:
        return [], []
    keep_indexes = [index for index in range(width) if cleaned_header[index] or any(row[index] for row in nonblank_rows)]
    return [[row[index] for index in keep_indexes] for row in nonblank_rows], [cleaned_header[index] for index in keep_indexes]


def records_from_detected_table(
    rows: list[list[str]],
    header_names: list[Any] | None = None,
) -> tuple[list[str], list[dict[str, str]]]:
    """Return columns and row records for one detected table."""
    header = [clean_cell(value) for value in header_names or []]
    column_indexes = [index for index, value in enumerate(header) if value]
    if len(column_indexes) < 2:
        column_indexes = []
    if column_indexes:
        columns = column_names_from_header([header[index] for index in column_indexes])
        first_row_repeats_header = all(index < len(rows[0]) and index < len(header) and rows[0][index] == header[index] for index in column_indexes)
        data_rows = rows[1:] if first_row_repeats_header else rows
        records = [record_from_header_indexes(row, columns, column_indexes) for row in data_rows]
        return columns, merge_continuation_records(records, columns)

    columns = [f"column_{index}" for index in range(1, len(rows[0]) + 1)]
    return columns, [dict(zip(columns, row, strict=False)) for row in rows]


def records_from_forced_columns(
    rows: list[list[str]],
    columns: list[str],
    min_filled_cells: int = 1,
) -> tuple[list[str], list[dict[str, str]]]:
    """Return records using caller-supplied columns when PDF headers drift."""
    records: list[dict[str, str]] = []
    for row in rows:
        cells = fit_row_to_columns(row, len(columns))
        filled_indexes = [index for index, value in enumerate(cells) if value]
        if not filled_indexes or row_matches_forced_columns(cells, columns):
            continue
        if filled_indexes == [0]:
            if records:
                first_column = columns[0]
                records[-1][first_column] = f"{records[-1][first_column]} {cells[0]}".strip()
            else:
                records.append(dict(zip(columns, cells, strict=True)))
            continue
        if len(filled_indexes) < min_filled_cells:
            continue
        records.append(dict(zip(columns, cells, strict=True)))
    return columns, records


def extend_rows_merging_first_column_continuations(
    existing_rows: list[dict[str, str]],
    new_rows: list[dict[str, str]],
    columns: list[str],
) -> None:
    """Append rows while joining page-leading first-column continuations."""
    first_column = columns[0]
    for row in new_rows:
        filled_columns = [column for column in columns if row.get(column)]
        if existing_rows and filled_columns == [first_column]:
            existing_rows[-1][first_column] = f"{existing_rows[-1][first_column]} {row[first_column]}".strip()
            continue
        existing_rows.append(row)


def fit_row_to_columns(row: list[str], column_count: int) -> list[str]:
    """Fit one detected row to the requested output width."""
    cells = [clean_cell(cell) for cell in row]
    if len(cells) < column_count:
        return [*cells, *([""] * (column_count - len(cells)))]
    if len(cells) == column_count:
        return cells
    return [*cells[: column_count - 1], " ".join(cell for cell in cells[column_count - 1 :] if cell).strip()]


def row_matches_forced_columns(row: list[str], columns: list[str]) -> bool:
    """Return whether a row repeats the forced output header."""
    filled_pairs = [(value, columns[index]) for index, value in enumerate(row) if value]
    return bool(filled_pairs) and all(header_token(value) == header_token(column) for value, column in filled_pairs)


def header_token(value: str) -> str:
    """Return a comparable token for detected and requested headers."""
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def record_from_header_indexes(
    row: list[str],
    columns: list[str],
    column_indexes: list[int],
) -> dict[str, str]:
    """Map a detected row into non-empty header columns, folding spacer cells left."""
    record = dict.fromkeys(columns, "")
    for index, value in enumerate(row):
        if not value:
            continue
        target_column_index = max((pos for pos, header_index in enumerate(column_indexes) if header_index <= index), default=0)
        target_column = columns[target_column_index]
        record[target_column] = f"{record[target_column]} {value}".strip()
    return record


def merge_continuation_records(
    records: list[dict[str, str]],
    columns: list[str],
) -> list[dict[str, str]]:
    """Merge rows that only continue the previous record's trailing cells."""
    merged: list[dict[str, str]] = []
    first_column = columns[0]
    for record in records:
        if not any(record.values()):
            continue
        if merged and not record[first_column]:
            for column in columns[1:]:
                if record[column]:
                    merged[-1][column] = f"{merged[-1][column]} {record[column]}".strip()
            continue
        merged.append(record)
    return merged


def column_names_from_header(header: list[str]) -> list[str]:
    """Return stable CSV column names from detected header cells."""
    columns: list[str] = []
    seen: dict[str, int] = {}
    for index, value in enumerate(header, start=1):
        column = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_") or f"column_{index}"
        seen[column] = seen.get(column, 0) + 1
        if seen[column] > 1:
            column = f"{column}_{seen[column]}"
        columns.append(column)
    return columns
