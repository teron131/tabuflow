"""Top-level PDF extraction workflow."""

from __future__ import annotations

from collections import Counter
import csv
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Any

import pymupdf

from ...artifacts.naming import normalize_source_stem
from ..common import pdf_artifact_workspace
from .coordinate_tables import coordinate_rows
from .detected_tables import pymupdf_table_outputs
from .pages import page_numbers
from .row_streams import ExtractedRows
from .text_values import field_value_rows, line_value_rows
from .transpose import RepeatedLabelColumns, repeated_label_column_transform

FILENAME_FINGERPRINT_CHARS = 4
GENERIC_COLUMN_PATTERN = re.compile(r"^column_[0-9]+$")
PROVENANCE_OUTPUT_COLUMNS = {"page"}
RowPattern = tuple[str, re.Pattern[str]]
SplitValues = tuple[str, ...]
SplitPartKey = tuple[SplitValues, int]


@dataclass(frozen=True)
class PendingPdfTable:
    """Represent one extracted table before its final CSV path is known."""

    pages: list[int]
    page_count: int
    rows: list[dict[str, str]]
    columns: list[str]
    manifest: dict[str, Any]


@dataclass(frozen=True)
class SplitRowGroup:
    """Represent one ordered split-table row group."""

    split_value: str
    split_values: dict[str, str]
    rows: list[dict[str, str]]
    pages: list[int]
    row_metadata: list[dict[str, Any]]
    table_end_reasons: list[str]


def write_csv(
    path: Path,
    rows: list[dict[str, str]],
    columns: list[str],
) -> None:
    """Write one configured CSV."""
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def split_columns_from_config(split_by: str | list[str]) -> list[str]:
    """Return configured split columns from a string or list."""
    if isinstance(split_by, list):
        return [str(column).strip() for column in split_by if str(column).strip()]
    return [column.strip() for column in split_by.split(",") if column.strip()]


def compile_table_end_patterns(patterns: list[dict[str, str]]) -> list[RowPattern]:
    """Return row-field regex patterns that close the current split table."""
    return [(str(item["name"]), re.compile(str(item["pattern"]))) for item in patterns]


def table_end_reason(
    row: dict[str, str],
    patterns: list[RowPattern],
) -> str | None:
    """Return the first matching reason when a row closes the current table."""
    for column, pattern in patterns:
        if pattern.match(str(row.get(column, ""))):
            return f"{column}={pattern.pattern}"
    return None


def split_rows_by_boundaries(
    rows: list[dict[str, str]],
    split_by: str | list[str],
    *,
    source_pages: list[int] | None = None,
    row_metadata: list[dict[str, Any]] | None = None,
    drop_empty: bool = False,
    table_ends: list[dict[str, str]] | None = None,
) -> list[SplitRowGroup]:
    """Return split row groups, starting a new group after configured end rows."""
    split_columns = split_columns_from_config(split_by)
    table_end_patterns = compile_table_end_patterns(table_ends or [])
    groups: dict[SplitPartKey, list[dict[str, str]]] = {}
    group_pages: dict[SplitPartKey, list[int]] = {}
    group_metadata: dict[SplitPartKey, list[dict[str, Any]]] = {}
    table_end_reasons: dict[SplitPartKey, list[str]] = {}
    current_part_by_values: Counter[SplitValues] = Counter()
    for row_index, row in enumerate(rows):
        values = tuple(str(row.get(column, "")) for column in split_columns)
        if drop_empty and not any(values):
            continue
        group_key = (values, current_part_by_values[values])
        groups.setdefault(group_key, []).append(row)
        if source_pages and row_index < len(source_pages):
            group_pages.setdefault(group_key, []).append(source_pages[row_index])
        if row_metadata and row_index < len(row_metadata):
            group_metadata.setdefault(group_key, []).append(row_metadata[row_index])
        if reason := table_end_reason(row, table_end_patterns):
            table_end_reasons.setdefault(group_key, []).append(reason)
            current_part_by_values[values] += 1
    return split_groups_from_parts(
        groups=groups,
        group_pages=group_pages,
        group_metadata=group_metadata,
        table_end_reasons=table_end_reasons,
        split_columns=split_columns,
    )


def split_groups_from_parts(
    *,
    groups: dict[SplitPartKey, list[dict[str, str]]],
    group_pages: dict[SplitPartKey, list[int]],
    group_metadata: dict[SplitPartKey, list[dict[str, Any]]],
    table_end_reasons: dict[SplitPartKey, list[str]],
    split_columns: list[str],
) -> list[SplitRowGroup]:
    """Return stable split group payloads from keyed row parts."""
    value_counts = Counter(values for values, _part in groups)
    split_groups: list[SplitRowGroup] = []
    for (values, part_index), grouped_rows in groups.items():
        split_value = " / ".join(value for value in values if value) or "unsectioned"
        split_values = dict(zip(split_columns, values, strict=True))
        if value_counts[values] > 1:
            split_value = f"{split_value} / part {part_index + 1}"
            split_values["table_part"] = str(part_index + 1)
        split_groups.append(
            SplitRowGroup(
                split_value=split_value,
                split_values=split_values,
                rows=grouped_rows,
                pages=sorted(set(group_pages.get((values, part_index), []))),
                row_metadata=group_metadata.get((values, part_index), []),
                table_end_reasons=table_end_reasons.get((values, part_index), []),
            )
        )
    return split_groups


def page_tag(
    pages: list[int],
    *,
    page_count: int,
) -> str:
    """Return an explicit page-range tag as pSTARTpEND."""
    if not pages:
        return "p00p00"
    width = len(str(page_count))
    start_page = min(pages)
    end_page = max(pages)
    return f"p{start_page:0{width}d}p{end_page:0{width}d}"


def filename_fingerprint(
    rows: list[dict[str, str]],
    columns: list[str],
) -> str:
    """Return a short stable fingerprint for filename collisions."""
    payload = json.dumps({"columns": columns, "rows": rows}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:FILENAME_FINGERPRINT_CHARS]


def output_path_for_table(
    *,
    output_dir: Path,
    pdf_stem: str,
    pages: list[int],
    page_count: int,
    page_table_index: int,
    rows: list[dict[str, str]],
    columns: list[str],
    used_stems: set[str],
    page_tag_counts: Counter[str],
) -> Path:
    """Return a stable CSV path based on page range and document order."""
    table_page_tag = page_tag(pages, page_count=page_count)
    base_stem = f"{pdf_stem}_{table_page_tag}"
    stem = base_stem
    if page_tag_counts[table_page_tag] > 1 or stem in used_stems:
        stem = f"{base_stem}_t{page_table_index}"
    if stem in used_stems:
        stem = f"{stem}_{filename_fingerprint(rows, columns)}"
    used_stems.add(stem)
    return output_dir / f"{stem}.csv"


def empty_extraction_diagnostics(pdf_path: Path) -> dict[str, Any]:
    """Return text diagnostics when extraction produced no tables."""
    with pymupdf.open(str(pdf_path)) as document:
        text_char_count = sum(len(page.get_text("text")) for page in document)
        warnings = ["no_tables_extracted"]
        if text_char_count == 0:
            warnings.append("no_extractable_text")
        return {
            "page_count": document.page_count,
            "text_char_count": text_char_count,
            "warnings": warnings,
        }


def manifest_table_warnings(tables: list[dict[str, Any]]) -> list[str]:
    """Return deterministic warnings for extracted PDF tables."""
    warnings: list[str] = []
    detected_tables = [table for table in tables if table.get("mode") == "pymupdf_tables"]
    if not detected_tables:
        return warnings
    if any(all(GENERIC_COLUMN_PATTERN.match(str(column)) for column in table.get("columns", [])) for table in detected_tables):
        warnings.append("generic_detected_columns")
    for table in detected_tables:
        detector_diagnostics = table.get("detector_diagnostics", {})
        if isinstance(detector_diagnostics, dict):
            warnings.extend(str(warning) for warning in detector_diagnostics.get("warnings", []) if warning)
    if warnings:
        warnings.insert(0, "low_confidence_detected_tables")
    return list(dict.fromkeys(warnings))


def extraction_diagnostics(
    *,
    pdf_path: Path,
    tables: list[dict[str, Any]],
) -> dict[str, Any]:
    """Return extraction diagnostics for empty or low-confidence PDF outputs."""
    if not tables:
        return empty_extraction_diagnostics(pdf_path)
    return {"warnings": manifest_table_warnings(tables)}


def pdf_extraction_status(tables: list[dict[str, Any]], diagnostics: dict[str, Any]) -> str:
    """Return the honest extraction status for a PDF table run."""
    if not tables:
        return "empty"
    if "low_confidence_detected_tables" in diagnostics.get("warnings", []):
        return "low_confidence"
    return "ok"


def _configured_rows(
    pdf_path: Path,
    table_config: dict[str, Any],
) -> ExtractedRows:
    """Extract rows for one configured non-detector PDF table mode."""
    mode = str(table_config["mode"])
    if mode == "line_value":
        return line_value_rows(pdf_path, table_config)
    if mode == "field_value":
        return field_value_rows(pdf_path, table_config)
    if mode == "coordinate_table":
        return coordinate_rows(pdf_path, table_config)
    raise ValueError(f"Unsupported PDF config extraction mode: {mode}")


def _configured_columns(
    table_config: dict[str, Any],
    rows: list[dict[str, str]],
) -> list[str]:
    """Return output columns for a configured table extraction."""
    if output_columns := table_config.get("output_columns"):
        columns = [str(column) for column in output_columns]
    else:
        configured_columns = table_config.get("columns", [])
        if configured_columns and isinstance(configured_columns[0], dict):
            columns = [str(column["name"]) for column in configured_columns]
        else:
            columns = [str(column) for column in configured_columns]
    if not columns and rows:
        columns = list(rows[0])
    return [column for column in columns if column not in PROVENANCE_OUTPUT_COLUMNS and not column.startswith("__")]


def _detected_pending_tables(
    *,
    pdf_path: Path,
    table_config: dict[str, Any],
    pdf_page_count: int,
) -> list[PendingPdfTable]:
    """Collect pending outputs for PyMuPDF detected tables."""
    pending_tables: list[PendingPdfTable] = []
    for table_output in pymupdf_table_outputs(pdf_path, table_config):
        table_pages = [int(page) for page in table_output.get("source_pages", [table_output["source_page"]])]
        pending_tables.append(
            PendingPdfTable(
                pages=table_pages,
                page_count=pdf_page_count,
                rows=table_output["rows"],
                columns=table_output["columns"],
                manifest={
                    "mode": table_output["mode"],
                    "row_count": len(table_output["rows"]),
                    "columns": table_output["columns"],
                    "source_page": table_output["source_page"],
                    "source_table": table_output["source_table"],
                    "source_pages": table_output.get("source_pages", [table_output["source_page"]]),
                    "source_tables": table_output.get("source_tables", [table_output["source_table"]]),
                    "source_bboxes": table_output.get("source_bboxes", [table_output.get("source_bbox")]),
                    "source_page_rejected_detection_count": table_output.get("source_page_rejected_detection_count"),
                    "merge_evidence": table_output.get("merge_evidence", []),
                    "detector_diagnostics": table_output.get("detector_diagnostics"),
                },
            )
        )
    return pending_tables


def _configured_split_pending_table(
    *,
    split_group: SplitRowGroup,
    table_config: dict[str, Any],
    columns: list[str],
    fallback_pages: list[int],
    pdf_page_count: int,
) -> PendingPdfTable:
    """Return one pending table for a configured split row group."""
    transformed = _repeated_label_transform(
        rows=split_group.rows,
        columns=columns,
        table_config=table_config,
        row_metadata=split_group.row_metadata,
    )
    output_rows = transformed.rows if transformed else split_group.rows
    output_columns = transformed.columns if transformed else columns
    return PendingPdfTable(
        pages=split_group.pages or fallback_pages,
        page_count=pdf_page_count,
        rows=output_rows,
        columns=output_columns,
        manifest={
            "mode": table_config["mode"],
            "row_count": len(output_rows),
            "columns": output_columns,
            "split_by": table_config["split_by"],
            "split_value": split_group.split_value,
            "split_values": split_group.split_values,
            "table_end_reasons": split_group.table_end_reasons,
            **({"repeated_label_columns": transformed.evidence} if transformed else {}),
        },
    )


def _repeated_label_transform(
    *,
    rows: list[dict[str, str]],
    columns: list[str],
    table_config: dict[str, Any],
    row_metadata: list[dict[str, Any]] | None = None,
) -> RepeatedLabelColumns | None:
    """Return an automatic repeated-label reshape when the line-value signal is strong."""
    if table_config["mode"] != "line_value":
        return None
    return repeated_label_column_transform(
        rows,
        columns,
        label_column=str(table_config.get("label_column", "label")),
        value_column=str(table_config.get("value_column", "value")),
        table_end_patterns=compile_table_end_patterns(table_config.get("table_ends", [])),
        row_metadata=row_metadata,
        mode=str(table_config.get("transpose_repeated_labels") or "auto"),
        entity_column=str(table_config.get("transpose_entity_column") or "item"),
        total_column=str(table_config.get("transpose_total_column") or "total"),
    )


def _configured_pending_tables(
    *,
    pdf_path: Path,
    table_config: dict[str, Any],
    fallback_pages: list[int],
    pdf_page_count: int,
) -> list[PendingPdfTable]:
    """Collect pending outputs for configured text and coordinate table modes."""
    extracted_rows = _configured_rows(pdf_path, table_config)
    rows = extracted_rows.rows
    columns = _configured_columns(table_config, rows)
    if split_by := table_config.get("split_by"):
        split_groups = split_rows_by_boundaries(
            rows,
            split_by,
            source_pages=extracted_rows.source_pages,
            row_metadata=extracted_rows.row_metadata,
            drop_empty=bool(table_config.get("drop_empty_split")),
            table_ends=table_config.get("table_ends", []),
        )
        return [
            _configured_split_pending_table(
                split_group=split_group,
                table_config=table_config,
                columns=columns,
                fallback_pages=fallback_pages,
                pdf_page_count=pdf_page_count,
            )
            for split_group in split_groups
        ]

    return [
        _configured_unsplit_pending_table(
            rows=rows,
            columns=columns,
            table_config=table_config,
            fallback_pages=fallback_pages,
            source_pages=extracted_rows.source_pages,
            pdf_page_count=pdf_page_count,
            row_metadata=extracted_rows.row_metadata,
        )
    ]


def _configured_unsplit_pending_table(
    *,
    rows: list[dict[str, str]],
    columns: list[str],
    table_config: dict[str, Any],
    fallback_pages: list[int],
    source_pages: list[int],
    pdf_page_count: int,
    row_metadata: list[dict[str, Any]] | None = None,
) -> PendingPdfTable:
    """Return one pending table for configured rows without split groups."""
    transformed = _repeated_label_transform(
        rows=rows,
        columns=columns,
        table_config=table_config,
        row_metadata=row_metadata,
    )
    output_rows = transformed.rows if transformed else rows
    output_columns = transformed.columns if transformed else columns
    return PendingPdfTable(
        pages=sorted(set(source_pages)) or fallback_pages,
        page_count=pdf_page_count,
        rows=output_rows,
        columns=output_columns,
        manifest={
            "mode": table_config["mode"],
            "row_count": len(output_rows),
            "columns": output_columns,
            **({"repeated_label_columns": transformed.evidence} if transformed else {}),
        },
    )


def _collect_pending_tables(
    *,
    pdf_path: Path,
    table_configs: list[dict[str, Any]],
) -> list[PendingPdfTable]:
    """Extract all configured tables before assigning final CSV paths."""
    with pymupdf.open(str(pdf_path)) as document:
        pdf_page_count = document.page_count
        fallback_pages_by_config = [page_numbers(document, table_config) for table_config in table_configs]

    pending_tables: list[PendingPdfTable] = []
    for table_config, fallback_pages in zip(table_configs, fallback_pages_by_config, strict=True):
        if table_config["mode"] == "pymupdf_tables":
            pending_tables.extend(
                _detected_pending_tables(
                    pdf_path=pdf_path,
                    table_config=table_config,
                    pdf_page_count=pdf_page_count,
                )
            )
            continue
        pending_tables.extend(
            _configured_pending_tables(
                pdf_path=pdf_path,
                table_config=table_config,
                fallback_pages=fallback_pages,
                pdf_page_count=pdf_page_count,
            )
        )
    return pending_tables


def _write_pending_tables(
    *,
    pending_tables: list[PendingPdfTable],
    output_dir: Path,
    pdf_stem: str,
) -> list[dict[str, Any]]:
    """Write pending table CSVs and return their manifest entries."""
    used_output_stems: set[str] = set()
    page_tag_counts = Counter(page_tag(table.pages, page_count=table.page_count) for table in pending_tables)
    page_tag_indexes: Counter[str] = Counter()
    manifest_tables: list[dict[str, Any]] = []
    for document_order, table in enumerate(pending_tables, start=1):
        table_page_tag = page_tag(table.pages, page_count=table.page_count)
        page_tag_indexes[table_page_tag] += 1
        output_path = output_path_for_table(
            output_dir=output_dir,
            pdf_stem=pdf_stem,
            pages=table.pages,
            page_count=table.page_count,
            page_table_index=page_tag_indexes[table_page_tag],
            rows=table.rows,
            columns=table.columns,
            used_stems=used_output_stems,
            page_tag_counts=page_tag_counts,
        )
        write_csv(output_path, table.rows, table.columns)
        manifest_tables.append(
            {
                **table.manifest,
                "document_order": document_order,
                "name": output_path.stem.removeprefix(f"{pdf_stem}_"),
                "page_tag": table_page_tag,
                "path": str(output_path),
            }
        )
    return manifest_tables


def extract_pdf_file(
    path: str | Path,
    *,
    extraction: dict[str, Any],
    root_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Extract PDF tables with configured PyMuPDF-backed presets."""
    if "output_dir" in extraction:
        raise ValueError("PDF extraction options cannot set output_dir. Outputs are written to the root-owned PDF artifact workspace.")

    workspace = pdf_artifact_workspace(path, root_dir=root_dir)
    pdf_path = workspace.pdf_path
    output_dir = workspace.tables_dir
    pdf_stem = normalize_source_stem(pdf_path.name)
    for stale_output in output_dir.glob(f"{pdf_stem}_*.csv"):
        stale_output.unlink()

    table_configs = list(extraction.get("tables", []))
    pending_tables = _collect_pending_tables(pdf_path=pdf_path, table_configs=table_configs)
    manifest_tables = _write_pending_tables(
        pending_tables=pending_tables,
        output_dir=output_dir,
        pdf_stem=pdf_stem,
    )

    manifest_path = workspace.tables_manifest_path
    diagnostics = extraction_diagnostics(pdf_path=pdf_path, tables=manifest_tables)
    extraction_status = pdf_extraction_status(manifest_tables, diagnostics)
    manifest = {
        "status": "empty" if extraction_status == "empty" else "ok",
        "extraction_status": extraction_status,
        "path": str(pdf_path),
        "extraction": extraction,
        "artifact_dir": str(workspace.artifact_dir),
        "work_dir": str(workspace.work_dir),
        "output_dir": str(output_dir),
        "tables": manifest_tables,
        "diagnostics": diagnostics,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return {
        **manifest,
        "manifest_path": str(manifest_path),
    }
