"""Top-level PDF extraction workflow."""

from __future__ import annotations

from collections import Counter
import csv
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any

import pymupdf

from ...artifacts.naming import normalize_source_stem
from ..common import pdf_artifact_workspace
from .coordinate_tables import coordinate_rows
from .detected_tables import pymupdf_table_outputs
from .pages import page_numbers
from .text_values import field_value_rows, line_value_rows

FILENAME_FINGERPRINT_CHARS = 4


@dataclass(frozen=True)
class PendingPdfTable:
    """Represent one extracted table before its final CSV path is known."""

    pages: list[int]
    page_count: int
    descriptor: str
    rows: list[dict[str, str]]
    columns: list[str]
    manifest: dict[str, Any]


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


def split_rows(
    rows: list[dict[str, str]],
    split_by: str,
    *,
    drop_empty: bool = False,
) -> list[tuple[str, list[dict[str, str]]]]:
    """Return rows grouped by a configured output column while preserving order."""
    groups: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        value = str(row.get(split_by, ""))
        if drop_empty and not value:
            continue
        groups.setdefault(value, []).append(row)
    return list(groups.items())


def page_tag(
    pages: list[int],
    *,
    page_count: int,
) -> str:
    """Return a compact page-range tag such as p01 or p01p88."""
    if not pages:
        return "p00"
    width = len(str(page_count))
    start_page = min(pages)
    end_page = max(pages)
    start = f"p{start_page:0{width}d}"
    return start if start_page == end_page else f"{start}p{end_page:0{width}d}"


def row_pages(
    rows: list[dict[str, str]],
    fallback_pages: list[int],
) -> list[int]:
    """Return source pages from row metadata, falling back to the configured pages."""
    pages = sorted({int(row["page"]) for row in rows if str(row.get("page", "")).isdigit()})
    return pages or fallback_pages


def output_descriptor(
    *,
    split_value: str = "",
    table_name: str = "",
    columns: list[str],
) -> str:
    """Return a fallback descriptor for page-range filename collisions."""
    candidates = [split_value, " ".join(column for column in columns if not column.startswith("column_")), table_name]
    for candidate in candidates:
        if not candidate.strip():
            continue
        descriptor = normalize_source_stem(candidate)[:40].strip("_")
        if descriptor:
            return descriptor
    return "table"


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
    descriptor: str,
    rows: list[dict[str, str]],
    columns: list[str],
    used_stems: set[str],
    use_descriptor: bool,
) -> Path:
    """Return a stable CSV path, using descriptor and hash only on collisions."""
    base_stem = f"{pdf_stem}_{page_tag(pages, page_count=page_count)}"
    stem = base_stem
    if use_descriptor or stem in used_stems:
        stem = f"{base_stem}_{descriptor}"
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


def _configured_rows(
    pdf_path: Path,
    table_config: dict[str, Any],
) -> list[dict[str, str]]:
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
    return columns


def _detected_pending_tables(
    *,
    pdf_path: Path,
    table_config: dict[str, Any],
    pdf_page_count: int,
) -> list[PendingPdfTable]:
    """Collect pending outputs for PyMuPDF detected tables."""
    base_name = str(table_config.get("name", "detected_table"))
    pending_tables: list[PendingPdfTable] = []
    for table_output in pymupdf_table_outputs(pdf_path, table_config):
        table_pages = [int(page) for page in table_output.get("source_pages", [table_output["source_page"]])]
        pending_tables.append(
            PendingPdfTable(
                pages=table_pages,
                page_count=pdf_page_count,
                descriptor=output_descriptor(table_name=base_name, columns=table_output["columns"]),
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
                },
            )
        )
    return pending_tables


def _configured_pending_tables(
    *,
    pdf_path: Path,
    table_config: dict[str, Any],
    fallback_pages: list[int],
    pdf_page_count: int,
) -> list[PendingPdfTable]:
    """Collect pending outputs for configured text and coordinate table modes."""
    rows = _configured_rows(pdf_path, table_config)
    columns = _configured_columns(table_config, rows)
    table_name = str(table_config.get("name") or "table")
    if split_by := table_config.get("split_by"):
        pending_tables: list[PendingPdfTable] = []
        split_groups = split_rows(rows, str(split_by), drop_empty=bool(table_config.get("drop_empty_split")))
        for split_value, grouped_rows in split_groups:
            split_slug = normalize_source_stem(split_value) if split_value else "unsectioned"
            split_table_name = f"{table_name}_{split_slug}" if split_slug else table_name
            pending_tables.append(
                PendingPdfTable(
                    pages=row_pages(grouped_rows, fallback_pages),
                    page_count=pdf_page_count,
                    descriptor=output_descriptor(split_value=split_value, table_name=split_table_name, columns=columns),
                    rows=grouped_rows,
                    columns=columns,
                    manifest={
                        "mode": table_config["mode"],
                        "row_count": len(grouped_rows),
                        "columns": columns,
                        "split_by": str(split_by),
                        "split_value": split_value,
                    },
                )
            )
        return pending_tables

    return [
        PendingPdfTable(
            pages=row_pages(rows, fallback_pages),
            page_count=pdf_page_count,
            descriptor=output_descriptor(table_name=table_name, columns=columns),
            rows=rows,
            columns=columns,
            manifest={
                "mode": table_config["mode"],
                "row_count": len(rows),
                "columns": columns,
            },
        )
    ]


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
    manifest_tables: list[dict[str, Any]] = []
    for table in pending_tables:
        table_page_tag = page_tag(table.pages, page_count=table.page_count)
        output_path = output_path_for_table(
            output_dir=output_dir,
            pdf_stem=pdf_stem,
            pages=table.pages,
            page_count=table.page_count,
            descriptor=table.descriptor,
            rows=table.rows,
            columns=table.columns,
            used_stems=used_output_stems,
            use_descriptor=page_tag_counts[table_page_tag] > 1,
        )
        write_csv(output_path, table.rows, table.columns)
        manifest_tables.append(
            {
                **table.manifest,
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
    manifest = {
        "status": "ok",
        "path": str(pdf_path),
        "extraction": extraction,
        "artifact_dir": str(workspace.artifact_dir),
        "work_dir": str(workspace.work_dir),
        "output_dir": str(output_dir),
        "tables": manifest_tables,
        "diagnostics": {"warnings": []} if manifest_tables else empty_extraction_diagnostics(pdf_path),
    }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return {
        **manifest,
        "manifest_path": str(manifest_path),
    }
