"""PyMuPDF-detected table extraction."""

from __future__ import annotations

import contextlib
from dataclasses import dataclass, field
import io
from pathlib import Path
from typing import Any

import pymupdf

from .pages import page_numbers
from .table_records import (
    clean_extracted_table,
    extend_rows_merging_first_column_continuations,
    records_from_detected_table,
    records_from_forced_columns,
)

_DETECTED_TABLE_PAYLOAD_KEYS = {
    "mode",
    "source_page",
    "source_table",
    "source_bbox",
    "source_page_height",
    "columns",
    "rows",
    "merge_first_column_continuations",
    "source_pages",
    "source_tables",
    "source_bboxes",
    "last_source_page",
    "last_source_bbox",
    "last_source_page_height",
}


def _float_bbox(value: Any) -> list[float] | None:
    """Return a float bbox list when a payload value contains coordinates."""
    return [float(coordinate) for coordinate in value] if value else None


@dataclass(slots=True)
class DetectedTableOutput:
    """Internal accumulator for one PyMuPDF-detected table output."""

    source_page: int
    source_table: int
    source_bbox: list[float] | None
    source_page_height: float | None
    columns: list[str]
    rows: list[dict[str, str]]
    merge_first_column_continuations: bool | None = None
    mode: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)
    source_pages: list[int] = field(default_factory=list)
    source_tables: list[int] = field(default_factory=list)
    source_bboxes: list[list[float] | None] = field(default_factory=list)
    last_source_page: int | None = None
    last_source_bbox: list[float] | None = None
    last_source_page_height: float | None = None

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> DetectedTableOutput:
        """Return an internal table output from the public dict shape."""
        return cls(
            source_page=int(payload["source_page"]),
            source_table=int(payload["source_table"]),
            source_bbox=_float_bbox(payload.get("source_bbox")),
            source_page_height=float(payload["source_page_height"]) if payload.get("source_page_height") is not None else None,
            columns=[str(column) for column in payload["columns"]],
            rows=list(payload["rows"]),
            merge_first_column_continuations=bool(payload["merge_first_column_continuations"]) if payload.get("merge_first_column_continuations") is not None else None,
            mode=str(payload["mode"]) if payload.get("mode") is not None else None,
            extra={key: value for key, value in payload.items() if key not in _DETECTED_TABLE_PAYLOAD_KEYS},
            source_pages=[int(page) for page in payload.get("source_pages", [])],
            source_tables=[int(table) for table in payload.get("source_tables", [])],
            source_bboxes=[_float_bbox(bbox) for bbox in payload.get("source_bboxes", [])],
            last_source_page=int(payload["last_source_page"]) if payload.get("last_source_page") is not None else None,
            last_source_bbox=_float_bbox(payload.get("last_source_bbox")),
            last_source_page_height=float(payload["last_source_page_height"]) if payload.get("last_source_page_height") is not None else None,
        )

    def __post_init__(self) -> None:
        """Initialize merge provenance from the first detected chunk."""
        if not self.source_pages:
            self.source_pages.append(self.source_page)
        if not self.source_tables:
            self.source_tables.append(self.source_table)
        if not self.source_bboxes:
            self.source_bboxes.append(self.source_bbox)
        self.last_source_page = self.source_page if self.last_source_page is None else self.last_source_page
        self.last_source_bbox = self.source_bbox if self.last_source_bbox is None else self.last_source_bbox
        if self.last_source_page_height is None:
            self.last_source_page_height = self.source_page_height

    def should_merge(
        self,
        current: DetectedTableOutput,
        *,
        merge_tables: str,
    ) -> bool:
        """Return whether another detected table chunk continues this output."""
        if current.columns != self.columns:
            return False
        if merge_tables == "never":
            return False
        if merge_tables == "always":
            return True
        if not self.source_bbox or not current.source_bbox:
            return True
        previous_page = int(self.last_source_page or self.source_page)
        if current.source_page == previous_page:
            previous_bottom = float((self.last_source_bbox or self.source_bbox)[3])
            current_top = float(current.source_bbox[1])
            return 0 <= current_top - previous_bottom <= 18
        if current.source_page != previous_page + 1:
            return False
        return self.touches_page_break(current)

    def touches_page_break(self, current: DetectedTableOutput) -> bool:
        """Return whether adjacent-page table chunks straddle a page break."""
        page_height = float(self.last_source_page_height or self.source_page_height or current.source_page_height or 0)
        if page_height <= 0 or not self.last_source_bbox or not current.source_bbox:
            return False
        previous_bottom = float(self.last_source_bbox[3])
        current_top = float(current.source_bbox[1])
        return previous_bottom >= page_height * 0.75 and current_top <= page_height * 0.25

    def merge(self, current: DetectedTableOutput) -> None:
        """Append another detected table chunk to this output."""
        if current.merge_first_column_continuations:
            extend_rows_merging_first_column_continuations(self.rows, current.rows, current.columns)
        else:
            self.rows.extend(current.rows)
        self.source_pages.append(current.source_page)
        self.source_tables.append(current.source_table)
        self.source_bboxes.append(current.source_bbox)
        self.last_source_page = current.source_page
        self.last_source_bbox = current.source_bbox
        self.last_source_page_height = current.source_page_height

    def to_payload(self) -> dict[str, Any]:
        """Return the public dict payload expected by the extraction workflow."""
        payload = {
            **self.extra,
            "source_page": self.source_page,
            "source_table": self.source_table,
            "columns": self.columns,
            "rows": self.rows,
            "source_pages": self.source_pages,
            "source_tables": self.source_tables,
            "source_bboxes": self.source_bboxes,
            "last_source_page": self.last_source_page,
            "last_source_bbox": self.last_source_bbox,
            "last_source_page_height": self.last_source_page_height,
        }
        if self.mode is not None:
            payload["mode"] = self.mode
        if self.source_bbox is not None:
            payload["source_bbox"] = self.source_bbox
        if self.source_page_height is not None:
            payload["source_page_height"] = self.source_page_height
        if self.merge_first_column_continuations is not None:
            payload["merge_first_column_continuations"] = self.merge_first_column_continuations
        return payload


def pymupdf_table_outputs(
    pdf_path: Path,
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    """Extract each PyMuPDF-detected table as a separate output."""
    outputs: list[DetectedTableOutput] = []
    merge_tables = str(config.get("merge_tables", "auto"))
    min_rows = int(config.get("min_rows", 1))
    forced_columns = [str(column) for column in config.get("output_columns", [])]
    min_filled_cells = int(config.get("min_filled_cells", 1))
    table_detection_options = find_tables_kwargs(config)
    with pymupdf.open(str(pdf_path)) as document:
        for page_number in page_numbers(document, config):
            page = document[page_number - 1]
            with contextlib.redirect_stdout(io.StringIO()):
                tables = page.find_tables(**table_detection_options)
            for source_table_number, table in enumerate(tables.tables, start=1):
                extracted_rows, header_names = clean_extracted_table(table.extract(), table.header.names)
                if len(extracted_rows) < min_rows:
                    continue
                if forced_columns:
                    columns, rows = records_from_forced_columns(extracted_rows, forced_columns, min_filled_cells)
                else:
                    columns, rows = records_from_detected_table(extracted_rows, header_names)
                if config.get("require_header") and all(column.startswith("column_") and column[7:].isdigit() for column in columns):
                    continue
                if not rows:
                    continue
                outputs.append(
                    DetectedTableOutput(
                        source_page=page_number,
                        source_table=source_table_number,
                        source_bbox=[float(value) for value in table.bbox],
                        source_page_height=float(page.rect.height),
                        columns=columns,
                        rows=rows,
                        merge_first_column_continuations=bool(forced_columns),
                        mode="pymupdf_tables",
                    )
                )
    return _merge_detected_table_outputs(outputs, merge_tables=merge_tables)


def find_tables_kwargs(config: dict[str, Any]) -> dict[str, Any]:
    """Return PyMuPDF table-detection options from a detected-table config."""
    kwargs: dict[str, Any] = {}
    for key in ("vertical_strategy", "horizontal_strategy"):
        if value := config.get(key):
            kwargs[key] = str(value).replace("-", "_")
    if clip := config.get("clip"):
        if len(clip) != 4:
            raise ValueError("PDF table clip must contain exactly four values: X0,Y0,X1,Y1.")
        kwargs["clip"] = pymupdf.Rect(*(float(value) for value in clip))
    return kwargs


def _merge_detected_table_outputs(
    outputs: list[DetectedTableOutput],
    *,
    merge_tables: str = "auto",
) -> list[dict[str, Any]]:
    """Merge adjacent internal detected-table outputs that repeat the same schema."""
    if merge_tables not in {"auto", "always", "never"}:
        raise ValueError(f"Unsupported detected-table merge policy: {merge_tables}")
    merged_outputs: list[DetectedTableOutput] = []
    for output in outputs:
        if merged_outputs and merged_outputs[-1].should_merge(output, merge_tables=merge_tables):
            merged_outputs[-1].merge(output)
            continue
        merged_outputs.append(output)
    return [output.to_payload() for output in merged_outputs]


def merge_consecutive_table_outputs(
    outputs: list[dict[str, Any]],
    *,
    merge_tables: str = "auto",
) -> list[dict[str, Any]]:
    """Merge adjacent detected tables that repeat the same schema."""
    return _merge_detected_table_outputs(
        [DetectedTableOutput.from_payload(output) for output in outputs],
        merge_tables=merge_tables,
    )
