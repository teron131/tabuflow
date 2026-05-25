"""Top-level PDF extraction workflow."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

import pymupdf

from ...artifacts.naming import normalize_source_stem
from ..common import pdf_artifact_work_paths
from .coordinate_tables import coordinate_rows
from .detected_tables import pymupdf_table_outputs
from .text_values import field_value_rows, line_value_rows


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


def split_table_name(
    base_name: str,
    split_value: str,
) -> str:
    """Return a stable table name for one split output."""
    split_name = normalize_source_stem(split_value) if split_value else "unsectioned"
    return f"{base_name}_{split_name}" if split_name else base_name


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


def configured_output_columns(table_config: dict[str, Any]) -> list[str]:
    """Return CSV columns from explicit output columns or coordinate specs."""
    if output_columns := table_config.get("output_columns"):
        return [str(column) for column in output_columns]
    columns = table_config.get("columns", [])
    if columns and isinstance(columns[0], dict):
        return [str(column["name"]) for column in columns]
    return [str(column) for column in columns]


def extract_pdf_file(
    path: str | Path,
    *,
    extraction: dict[str, Any],
    root_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Extract PDF tables with configured PyMuPDF-backed presets."""
    if "output_dir" in extraction:
        raise ValueError("PDF extraction options cannot set output_dir. Outputs are written to the root-owned PDF artifact workspace.")

    artifact_paths = pdf_artifact_work_paths(path, root_dir=root_dir)
    pdf_path = artifact_paths["pdf_path"]
    output_dir = artifact_paths["tables_dir"]
    pdf_stem = normalize_source_stem(pdf_path.name)
    manifest_tables: list[dict[str, Any]] = []
    for stale_output in output_dir.glob(f"{pdf_stem}_table_*.csv"):
        stale_output.unlink()

    for table_index, table_config in enumerate(extraction.get("tables", []), start=1):
        if table_config["mode"] == "pymupdf_tables":
            base_name = str(table_config.get("name", "detected_table"))
            for detected_index, table_output in enumerate(pymupdf_table_outputs(pdf_path, table_config), start=table_index):
                table_name = f"{base_name}_{detected_index}"
                output_path = output_dir / f"{pdf_stem}_table_{detected_index}.csv"
                write_csv(output_path, table_output["rows"], table_output["columns"])
                manifest_tables.append(
                    {
                        "name": table_name,
                        "table_number": detected_index,
                        "mode": table_output["mode"],
                        "path": str(output_path),
                        "row_count": len(table_output["rows"]),
                        "columns": table_output["columns"],
                        "source_page": table_output["source_page"],
                        "source_table": table_output["source_table"],
                        "source_pages": table_output.get("source_pages", [table_output["source_page"]]),
                        "source_tables": table_output.get("source_tables", [table_output["source_table"]]),
                        "source_bboxes": table_output.get("source_bboxes", [table_output.get("source_bbox")]),
                    }
                )
            continue
        mode = str(table_config["mode"])
        if mode == "line_value":
            rows = line_value_rows(pdf_path, table_config)
        elif mode == "field_value":
            rows = field_value_rows(pdf_path, table_config)
        elif mode == "coordinate_table":
            rows = coordinate_rows(pdf_path, table_config)
        else:
            raise ValueError(f"Unsupported PDF config extraction mode: {mode}")
        table_number = table_index
        table_name = str(table_config.get("name", f"table_{table_number}"))
        columns = configured_output_columns(table_config)
        if not columns and rows:
            columns = list(rows[0])
        if split_by := table_config.get("split_by"):
            split_groups = split_rows(rows, str(split_by), drop_empty=bool(table_config.get("drop_empty_split")))
            for split_index, (split_value, grouped_rows) in enumerate(split_groups, start=table_number):
                split_name = split_table_name(table_name, split_value)
                output_path = output_dir / f"{pdf_stem}_table_{split_index}.csv"
                write_csv(output_path, grouped_rows, columns)
                manifest_tables.append(
                    {
                        "name": split_name,
                        "table_number": split_index,
                        "mode": table_config["mode"],
                        "path": str(output_path),
                        "row_count": len(grouped_rows),
                        "columns": columns,
                        "split_by": str(split_by),
                        "split_value": split_value,
                    }
                )
            continue
        output_path = output_dir / f"{pdf_stem}_table_{table_number}.csv"
        write_csv(output_path, rows, columns)
        manifest_tables.append(
            {
                "name": table_name,
                "table_number": table_number,
                "mode": table_config["mode"],
                "path": str(output_path),
                "row_count": len(rows),
                "columns": columns,
            }
        )

    manifest_path = artifact_paths["tables_manifest_path"]
    manifest = {
        "status": "ok",
        "path": str(pdf_path),
        "extraction": extraction,
        "artifact_dir": str(artifact_paths["artifact_dir"]),
        "work_dir": str(artifact_paths["work_dir"]),
        "output_dir": str(output_dir),
        "tables": manifest_tables,
        "diagnostics": {"warnings": []} if manifest_tables else empty_extraction_diagnostics(pdf_path),
    }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return {
        **manifest,
        "manifest_path": str(manifest_path),
    }
