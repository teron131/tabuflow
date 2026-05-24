"""Standalone LLM-free PDF inspection and preparation tools."""

from __future__ import annotations

import contextlib
import csv
from datetime import UTC, datetime
import hashlib
import io
import json
from pathlib import Path
import re
from typing import Any

import pymupdf

from ..artifacts.naming import normalize_source_filename, normalize_source_stem
from ..tabular.storage import resolve_root_dir

DEFAULT_DPI = 150
DEFAULT_PDF_INSPECT_OUTPUT_DIR = Path("data/pdf_inspect")
DEFAULT_PDF_PREPARE_OUTPUT_DIR = Path("artifacts/pdf")
DEFAULT_INSPECT_PAGE_LIMIT = 2
DEFAULT_INSPECT_TEXT_CHARS = 4_000
DEFAULT_MAX_PREPARE_PAGES = 300
MIN_PREPARE_DPI = 72
MAX_PREPARE_DPI = 300
PDF_ARTIFACT_VERSION = 1
PDF_TABLES_DIR_NAME = "tables"
PDF_TABLES_MANIFEST_NAME = "tables_manifest.json"


def pdf_source_fingerprint(path: Path) -> str:
    """Return the exact source-file fingerprint used for PDF artifact identity."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _relative_to_root(path: Path, root_dir: Path) -> str:
    """Return a root-relative path when possible."""
    try:
        return str(path.resolve().relative_to(root_dir))
    except ValueError:
        return str(path.resolve())


def _validate_prepare_options(
    *,
    dpi: int,
    page_count: int,
    max_pages: int | None,
) -> None:
    """Validate PDF preparation limits before rendering page artifacts."""
    if not MIN_PREPARE_DPI <= dpi <= MAX_PREPARE_DPI:
        raise ValueError(f"dpi must be between {MIN_PREPARE_DPI} and {MAX_PREPARE_DPI}.")
    if max_pages is not None and max_pages < 1:
        raise ValueError("max_pages must be >= 1 when provided.")
    if max_pages is not None and page_count > max_pages:
        raise ValueError(f"PDF has {page_count} pages, above the max_pages guard of {max_pages}. Pass a higher --max-pages if you want to prepare it.")


def _manifest_source_fingerprint(artifact_dir: Path) -> str | None:
    """Return the source fingerprint recorded by an existing PDF artifact manifest."""
    manifest_path = artifact_dir / "manifest.json"
    if not manifest_path.is_file():
        return None
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    value = manifest.get("source_fingerprint") or manifest.get("fingerprint")
    return str(value) if value else None


def _pdf_artifact_dir(
    *,
    output_path: Path,
    source_stem: str,
    source_fingerprint: str,
) -> Path:
    """Return a normalized artifact directory, reusing identical content when present."""
    index = 1
    while True:
        artifact_stem = source_stem if index == 1 else f"{source_stem}_{index}"
        artifact_dir = output_path / artifact_stem
        if not artifact_dir.exists() or _manifest_source_fingerprint(artifact_dir) == source_fingerprint:
            return artifact_dir
        index += 1


def _prepare_page_artifacts(
    *,
    document: pymupdf.Document,
    pages_dir: Path,
    text_dir: Path,
    root_dir: Path,
    dpi: int,
) -> list[dict[str, Any]]:
    """Render every PDF page image/text pair and return manifest-ready page entries."""
    pages: list[dict[str, Any]] = []
    page_width = max(3, len(str(document.page_count)))
    for page_number in range(1, document.page_count + 1):
        page = document[page_number - 1]
        page_name = f"page_{page_number:0{page_width}d}"
        image_path = pages_dir / f"{page_name}.jpg"
        text_path = text_dir / f"{page_name}.txt"
        text = page.get_text("text")
        image_path.write_bytes(page.get_pixmap(dpi=dpi).tobytes("jpeg"))
        text_path.write_text(text, encoding="utf-8")
        pages.append(
            {
                "page_number": page_number,
                "image_path": _relative_to_root(image_path, root_dir),
                "text_path": _relative_to_root(text_path, root_dir),
                "text_char_count": len(text),
            }
        )
    return pages


def pdf_artifact_work_paths(
    path: str | Path,
    *,
    root_dir: str | Path | None = None,
) -> dict[str, Path]:
    """Return root-owned PDF artifact work paths for a source PDF."""
    resolved_root_dir = resolve_root_dir(root_dir=root_dir)
    pdf_path = Path(path).expanduser()
    if not pdf_path.is_absolute():
        pdf_path = resolved_root_dir / pdf_path
    pdf_path = pdf_path.resolve()
    if not pdf_path.is_file():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    output_path = resolved_root_dir / DEFAULT_PDF_PREPARE_OUTPUT_DIR
    source_fingerprint = pdf_source_fingerprint(pdf_path)
    source_stem = normalize_source_stem(pdf_path.name)
    artifact_dir = output_path / source_stem
    if not artifact_dir.exists() or _manifest_source_fingerprint(artifact_dir) not in {None, source_fingerprint}:
        artifact_dir = _pdf_artifact_dir(
            output_path=output_path,
            source_stem=source_stem,
            source_fingerprint=source_fingerprint,
        )
    work_dir = artifact_dir / "work"
    tables_dir = work_dir / PDF_TABLES_DIR_NAME
    tables_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = artifact_dir / "manifest.json"
    if not manifest_path.exists():
        manifest = {
            "version": PDF_ARTIFACT_VERSION,
            "kind": "pdf_work",
            "status": "prepared",
            "created_at": datetime.now(UTC).isoformat(),
            "source_path": str(pdf_path),
            "source_filename": pdf_path.name,
            "normalized_filename": normalize_source_filename(pdf_path.name),
            "source_fingerprint": source_fingerprint,
            "artifact_dir": _relative_to_root(artifact_dir, resolved_root_dir),
            "work_dir": _relative_to_root(work_dir, resolved_root_dir),
            "tables_dir": _relative_to_root(tables_dir, resolved_root_dir),
            "tables_manifest_path": _relative_to_root(work_dir / PDF_TABLES_MANIFEST_NAME, resolved_root_dir),
        }
        manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return {
        "root_dir": resolved_root_dir,
        "pdf_path": pdf_path,
        "artifact_dir": artifact_dir,
        "work_dir": work_dir,
        "tables_dir": tables_dir,
        "tables_manifest_path": work_dir / PDF_TABLES_MANIFEST_NAME,
    }


def inspect_pdf_file(
    path: str | Path,
    *,
    page_start: int = 1,
    page_limit: int = DEFAULT_INSPECT_PAGE_LIMIT,
    max_text_chars: int = DEFAULT_INSPECT_TEXT_CHARS,
    include_images: bool = False,
    output_dir: str | Path = DEFAULT_PDF_INSPECT_OUTPUT_DIR,
    dpi: int = 96,
) -> dict[str, Any]:
    """Return raw page text and optional rendered page images for a PDF."""
    pdf_path = Path(path).expanduser().resolve()
    if not pdf_path.is_file():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    safe_page_start = max(1, page_start)
    safe_page_limit = max(1, page_limit)
    safe_text_chars = max(0, max_text_chars)
    output_path = Path(output_dir)
    if include_images:
        output_path.mkdir(parents=True, exist_ok=True)

    pages: list[dict[str, Any]] = []
    with pymupdf.open(str(pdf_path)) as document:
        page_count = document.page_count
        page_end = min(page_count, safe_page_start + safe_page_limit - 1)
        for page_number in range(safe_page_start, page_end + 1):
            page = document[page_number - 1]
            text = page.get_text("text").strip()
            page_payload: dict[str, Any] = {
                "page_number": page_number,
                "text": text[:safe_text_chars],
                "text_char_count": len(text),
                "text_truncated": len(text) > safe_text_chars,
            }
            if include_images:
                image_path = output_path / f"{normalize_source_stem(pdf_path.name)}_page_{page_number}.jpg"
                image_path.write_bytes(page.get_pixmap(dpi=dpi).tobytes("jpeg"))
                page_payload["image_path"] = str(image_path)
            pages.append(page_payload)

    return {
        "path": str(pdf_path),
        "format": "pdf",
        "status": "ok",
        "page_count": page_count,
        "page_start": safe_page_start,
        "page_end": pages[-1]["page_number"] if pages else safe_page_start - 1,
        "image_output_dir": str(output_path) if include_images else None,
        "pages": pages,
    }


def prepare_pdf_file(
    path: str | Path,
    *,
    root_dir: str | Path | None = None,
    dpi: int = DEFAULT_DPI,
    max_pages: int | None = DEFAULT_MAX_PREPARE_PAGES,
) -> dict[str, Any]:
    """Create a lean PDF artifact workspace with page images, work files, and manifest."""
    resolved_root_dir = resolve_root_dir(root_dir=root_dir)
    pdf_path = Path(path).expanduser()
    if not pdf_path.is_absolute():
        pdf_path = resolved_root_dir / pdf_path
    pdf_path = pdf_path.resolve()
    if not pdf_path.is_file():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    output_path = resolved_root_dir / DEFAULT_PDF_PREPARE_OUTPUT_DIR
    source_fingerprint = pdf_source_fingerprint(pdf_path)
    normalized_filename = normalize_source_filename(pdf_path.name)
    normalized_stem = normalize_source_stem(pdf_path.name)
    artifact_dir = _pdf_artifact_dir(
        output_path=output_path,
        source_stem=normalized_stem,
        source_fingerprint=source_fingerprint,
    )
    pages_dir = artifact_dir / "pages"
    text_dir = artifact_dir / "text"
    work_dir = artifact_dir / "work"

    with pymupdf.open(str(pdf_path)) as document:
        page_count = document.page_count
        _validate_prepare_options(
            dpi=dpi,
            page_count=page_count,
            max_pages=max_pages,
        )

        for directory in (pages_dir, text_dir, work_dir):
            directory.mkdir(parents=True, exist_ok=True)
        tables_dir = work_dir / PDF_TABLES_DIR_NAME
        tables_dir.mkdir(parents=True, exist_ok=True)
        tables_manifest_path = work_dir / PDF_TABLES_MANIFEST_NAME

        source_artifact_path = artifact_dir / normalized_filename
        if not source_artifact_path.exists():
            source_artifact_path.write_bytes(pdf_path.read_bytes())

        pages = _prepare_page_artifacts(
            document=document,
            pages_dir=pages_dir,
            text_dir=text_dir,
            root_dir=resolved_root_dir,
            dpi=dpi,
        )

    manifest = {
        "version": PDF_ARTIFACT_VERSION,
        "kind": "pdf_prepare",
        "status": "prepared",
        "created_at": datetime.now(UTC).isoformat(),
        "source_path": str(pdf_path),
        "source_filename": pdf_path.name,
        "normalized_filename": normalized_filename,
        "source_fingerprint": source_fingerprint,
        "source_artifact_path": _relative_to_root(source_artifact_path, resolved_root_dir),
        "artifact_dir": _relative_to_root(artifact_dir, resolved_root_dir),
        "pages_dir": _relative_to_root(pages_dir, resolved_root_dir),
        "text_dir": _relative_to_root(text_dir, resolved_root_dir),
        "work_dir": _relative_to_root(work_dir, resolved_root_dir),
        "tables_dir": _relative_to_root(tables_dir, resolved_root_dir),
        "tables_manifest_path": _relative_to_root(tables_manifest_path, resolved_root_dir),
        "dpi": dpi,
        "max_pages": max_pages,
        "page_count": page_count,
        "prepared_page_count": len(pages),
        "pages": pages,
    }
    manifest_path = artifact_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    return {
        "path": str(pdf_path),
        "format": "pdf",
        "status": "prepared",
        "artifact_dir": str(artifact_dir),
        "manifest_path": str(manifest_path),
        "source_artifact_path": str(source_artifact_path),
        "pages_dir": str(pages_dir),
        "text_dir": str(text_dir),
        "work_dir": str(work_dir),
        "tables_dir": str(tables_dir),
        "tables_manifest_path": str(tables_manifest_path),
        "dpi": dpi,
        "max_pages": max_pages,
        "page_count": page_count,
        "prepared_page_count": len(pages),
        "pages": pages,
    }


def _page_numbers(
    document: pymupdf.Document,
    config: dict[str, Any],
) -> list[int]:
    """Return configured 1-based page numbers."""
    if pages := config.get("pages"):
        page_values = pages if isinstance(pages, list) else [pages]
        return [int(page) for page in page_values]

    start = int(config.get("page_start", 1))
    end = int(config.get("page_end", document.page_count))
    return list(range(max(1, start), min(document.page_count, end) + 1))


def _page_lines(
    page: pymupdf.Page,
    page_number: int,
    config: dict[str, Any],
) -> list[str]:
    """Return cleaned non-empty page text lines."""
    lines: list[str] = []
    skip_lines = set(config.get("skip_lines", []))
    skip_prefixes = list(config.get("skip_prefixes", []))
    for line in [part.strip() for part in page.get_text("text").splitlines() if part.strip()]:
        if bool(config.get("skip_page_numbers", True)) and line == str(page_number):
            continue
        if any(line.startswith(prefix) for prefix in config.get("stop_prefixes", [])):
            break
        if line in skip_lines or any(line.startswith(prefix) for prefix in skip_prefixes):
            continue
        lines.append(line)
    return lines


def _document_lines(
    pdf_path: Path,
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    """Read configured pages as cleaned line records."""
    records: list[dict[str, Any]] = []
    with pymupdf.open(str(pdf_path)) as document:
        for page_number in _page_numbers(document, config):
            for line in _page_lines(document[page_number - 1], page_number, config):
                records.append(
                    {
                        "page": page_number,
                        "text": line,
                    }
                )
    return records


def _compiled_contexts(config: dict[str, Any], key: str) -> list[tuple[str, re.Pattern[str]]]:
    """Return configured line-context patterns."""
    return [(str(item["name"]), re.compile(str(item["pattern"]))) for item in config.get(key, [])]


def _context_match_value(match: re.Match[str]) -> str:
    """Return the carried value for a context regex match."""
    if "value" in match.groupdict():
        return str(match.group("value"))
    if match.groups():
        return str(next((group for group in match.groups() if group is not None), match.group(0)))
    return str(match.group(0))


def _update_line_context(
    text: str,
    context: dict[str, str],
    contexts: list[tuple[str, re.Pattern[str]]],
    clear_contexts: list[tuple[str, re.Pattern[str]]],
) -> None:
    """Update carried context values from one cleaned text line."""
    for name, pattern in clear_contexts:
        if pattern.match(text):
            context[name] = ""
    for name, pattern in contexts:
        if match := pattern.match(text):
            context[name] = _context_match_value(match)


def _line_value_rows(
    pdf_path: Path,
    config: dict[str, Any],
) -> list[dict[str, str]]:
    """Extract adjacent label/value text-line pairs."""
    value_pattern = re.compile(str(config["value_pattern"]))
    label_column = str(config.get("label_column", "label"))
    value_column = str(config.get("value_column", "value"))
    contexts = _compiled_contexts(config, "contexts")
    clear_contexts = _compiled_contexts(config, "clear_contexts")
    context = {name: "" for name, _pattern in contexts}
    records = _document_lines(pdf_path, config)
    rows: list[dict[str, str]] = []
    line_index = 0
    while line_index < len(records) - 1:
        label_record = records[line_index]
        value_record = records[line_index + 1]
        label = str(label_record["text"])
        value = str(value_record["text"])
        _update_line_context(label, context, contexts, clear_contexts)
        if value_pattern.match(value):
            row = dict(context)
            row.update(
                {
                    label_column: label,
                    value_column: value,
                }
            )
            if config.get("include_page"):
                row["page"] = str(label_record["page"])
            rows.append(row)
            line_index += 2
            continue
        line_index += 1
    return rows


def _field_value_rows(
    pdf_path: Path,
    config: dict[str, Any],
) -> list[dict[str, str]]:
    """Extract configured field names and following value lines."""
    field_column = str(config.get("field_column", "field"))
    value_column = str(config.get("value_column", "value"))
    field_labels = dict(config["fields"])
    collect_until_next_field = bool(config.get("collect_until_next_field"))
    field_names = set(field_labels)
    contexts = _compiled_contexts(config, "contexts")
    clear_contexts = _compiled_contexts(config, "clear_contexts")
    context = {name: "" for name, _pattern in contexts}
    records = _document_lines(pdf_path, config)
    rows: list[dict[str, str]] = []
    for index, record in enumerate(records[:-1]):
        line = str(record["text"])
        _update_line_context(line, context, contexts, clear_contexts)
        if line not in field_labels:
            continue
        values = [str(records[index + 1]["text"])]
        if collect_until_next_field:
            for next_record in records[index + 2 :]:
                next_line = str(next_record["text"])
                if next_line in field_names:
                    break
                values.append(next_line)
        row = dict(context)
        row.update(
            {
                field_column: str(field_labels[line]),
                value_column: " ".join(values),
            }
        )
        if config.get("include_page"):
            row["page"] = str(record["page"])
        rows.append(row)
    return rows


def _visual_lines(
    page: pymupdf.Page,
    *,
    y_tolerance: float,
) -> list[tuple[float, list[tuple[float, str]]]]:
    """Group PyMuPDF word records into visual rows."""
    rows: list[tuple[float, list[tuple[float, str]]]] = []
    for word in sorted(page.get_text("words"), key=lambda item: (round(float(item[1]) / y_tolerance) * y_tolerance, float(item[0]))):
        x0, y0, _x1, _y1, text, *_rest = word
        if not rows or abs(rows[-1][0] - float(y0)) > y_tolerance:
            rows.append((float(y0), []))
        rows[-1][1].append((float(x0), str(text)))
    return rows


def _column_value(
    parts: list[tuple[float, str]],
    column: dict[str, Any],
) -> str:
    """Return the joined words that fall inside one configured x-band."""
    x_min = float(column["x_min"])
    x_max = float(column["x_max"])
    return _clean_cell(" ".join(text for x, text in parts if x_min <= x < x_max))


def _coordinate_rows(
    pdf_path: Path,
    config: dict[str, Any],
) -> list[dict[str, str]]:
    """Extract visual rows from configured x-column bands."""
    columns = list(config["columns"])
    y_tolerance = float(config.get("y_tolerance", 4))
    y_min = float(config.get("y_min", 0))
    y_max = float(config.get("y_max", 10_000))
    required_columns = [str(column) for column in config.get("required_columns", [])]
    continuation_column = str(config["continuation_column"]) if config.get("continuation_column") else None
    if continuation_column:
        anchor_columns = [str(column) for column in config.get("anchor_columns", [])]
        if not anchor_columns:
            anchor_columns = [column for column in required_columns if column != continuation_column]
        if anchor_columns:
            return _coordinate_anchor_rows(
                pdf_path,
                config,
                columns=columns,
                y_tolerance=y_tolerance,
                y_min=y_min,
                y_max=y_max,
                required_columns=required_columns,
                anchor_columns=anchor_columns,
                continuation_column=continuation_column,
            )

    rows: list[dict[str, str]] = []

    with pymupdf.open(str(pdf_path)) as document:
        for page_number in _page_numbers(document, config):
            page = document[page_number - 1]
            for y, parts in _visual_lines(page, y_tolerance=y_tolerance):
                if not y_min <= y <= y_max:
                    continue
                row = {str(column["name"]): _column_value(parts, column) for column in columns}
                if config.get("include_page"):
                    row["page"] = str(page_number)
                if _row_matches_skip_filters(row, config):
                    continue
                if required_columns and not all(row.get(column_name) for column_name in required_columns):
                    continue
                if any(row.get(str(column["name"])) for column in columns):
                    rows.append(row)
    return rows


def _coordinate_anchor_rows(
    pdf_path: Path,
    config: dict[str, Any],
    *,
    columns: list[dict[str, Any]],
    y_tolerance: float,
    y_min: float,
    y_max: float,
    required_columns: list[str],
    anchor_columns: list[str],
    continuation_column: str,
) -> list[dict[str, str]]:
    """Extract rows whose stable columns anchor nearby wrapped text."""
    anchor_y_slop = float(config.get("anchor_y_slop", y_tolerance * 2))
    rows: list[dict[str, str]] = []

    with pymupdf.open(str(pdf_path)) as document:
        for page_number in _page_numbers(document, config):
            page_lines = []
            for y, parts in _visual_lines(document[page_number - 1], y_tolerance=y_tolerance):
                if not y_min <= y <= y_max:
                    continue
                row = {str(column["name"]): _column_value(parts, column) for column in columns}
                page_lines.append(
                    {
                        "y": y,
                        "row": row,
                    }
                )

            anchors = [line for line in page_lines if all(line["row"].get(column) for column in anchor_columns)]
            for anchor_index, anchor in enumerate(anchors):
                has_next_anchor = anchor_index < len(anchors) - 1
                next_anchor_y = anchors[anchor_index + 1]["y"] if has_next_anchor else y_max
                band_start = max(y_min, anchor["y"] - anchor_y_slop)
                band_end = min(y_max, next_anchor_y - anchor_y_slop if has_next_anchor else y_max)
                wrapped_values = [line["row"][continuation_column] for line in page_lines if band_start <= line["y"] < band_end and line["row"].get(continuation_column)]
                row = dict(anchor["row"])
                row[continuation_column] = _clean_cell(" ".join(wrapped_values))
                if config.get("include_page"):
                    row["page"] = str(page_number)
                if _row_matches_skip_filters(row, config):
                    continue
                if required_columns and not all(row.get(column) for column in required_columns):
                    continue
                rows.append(row)
    return rows


def _row_matches_skip_filters(
    row: dict[str, str],
    config: dict[str, Any],
) -> bool:
    """Return whether a visual row should be dropped by text cleanup filters."""
    skip_lines = set(config.get("skip_lines", []))
    skip_prefixes = list(config.get("skip_prefixes", []))
    return any(value in skip_lines or any(value.startswith(prefix) for prefix in skip_prefixes) for value in row.values())


def _pymupdf_table_outputs(
    pdf_path: Path,
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    """Extract each PyMuPDF-detected table as a separate output."""
    outputs: list[dict[str, Any]] = []
    merge_tables = str(config.get("merge_tables", "auto"))
    min_rows = int(config.get("min_rows", 1))
    forced_columns = [str(column) for column in config.get("output_columns", [])]
    min_filled_cells = int(config.get("min_filled_cells", 1))
    find_tables_kwargs = _find_tables_kwargs(config)
    with pymupdf.open(str(pdf_path)) as document:
        for page_number in _page_numbers(document, config):
            page = document[page_number - 1]
            with contextlib.redirect_stdout(io.StringIO()):
                tables = page.find_tables(**find_tables_kwargs)
            for source_table_number, table in enumerate(tables.tables, start=1):
                extracted_rows, header_names = _clean_extracted_table(table.extract(), table.header.names)
                if len(extracted_rows) < min_rows:
                    continue
                if forced_columns:
                    columns, rows = _records_from_forced_columns(extracted_rows, forced_columns, min_filled_cells)
                else:
                    columns, rows = _records_from_detected_table(extracted_rows, header_names)
                if config.get("require_header") and _generic_column_names(columns):
                    continue
                if not rows:
                    continue
                outputs.append(
                    {
                        "mode": "pymupdf_tables",
                        "source_page": page_number,
                        "source_table": source_table_number,
                        "source_bbox": list(table.bbox),
                        "source_page_height": float(page.rect.height),
                        "columns": columns,
                        "rows": rows,
                        "merge_first_column_continuations": bool(forced_columns),
                    }
                )
    return _merge_consecutive_table_outputs(outputs, merge_tables=merge_tables)


def _generic_column_names(columns: list[str]) -> bool:
    """Return whether every column is a fallback column_N name."""
    return all(re.fullmatch(r"column_\d+", column) for column in columns)


def _find_tables_kwargs(config: dict[str, Any]) -> dict[str, Any]:
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


def _merge_consecutive_table_outputs(
    outputs: list[dict[str, Any]],
    *,
    merge_tables: str = "auto",
) -> list[dict[str, Any]]:
    """Merge adjacent detected tables that repeat the same schema."""
    if merge_tables not in {"auto", "always", "never"}:
        raise ValueError(f"Unsupported detected-table merge policy: {merge_tables}")
    merged_outputs: list[dict[str, Any]] = []
    for output in outputs:
        if merged_outputs and _should_merge_table_outputs(merged_outputs[-1], output, merge_tables=merge_tables):
            if output.get("merge_first_column_continuations"):
                _extend_rows_merging_first_column_continuations(merged_outputs[-1]["rows"], output["rows"], output["columns"])
            else:
                merged_outputs[-1]["rows"].extend(output["rows"])
            merged_outputs[-1]["source_pages"].append(output["source_page"])
            merged_outputs[-1]["source_tables"].append(output["source_table"])
            merged_outputs[-1]["source_bboxes"].append(output.get("source_bbox"))
            merged_outputs[-1]["last_source_page"] = output["source_page"]
            merged_outputs[-1]["last_source_bbox"] = output.get("source_bbox")
            merged_outputs[-1]["last_source_page_height"] = output.get("source_page_height")
            continue
        merged_outputs.append(
            {
                **output,
                "source_pages": [output["source_page"]],
                "source_tables": [output["source_table"]],
                "source_bboxes": [output.get("source_bbox")],
                "last_source_page": output["source_page"],
                "last_source_bbox": output.get("source_bbox"),
                "last_source_page_height": output.get("source_page_height"),
            }
        )
    return merged_outputs


def _should_merge_table_outputs(
    previous: dict[str, Any],
    current: dict[str, Any],
    *,
    merge_tables: str,
) -> bool:
    """Return whether two detected table chunks look like one continued table."""
    if current["columns"] != previous["columns"]:
        return False
    if merge_tables == "never":
        return False
    if merge_tables == "always":
        return True
    if not previous.get("source_bbox") or not current.get("source_bbox"):
        return True
    previous_page = int(previous.get("last_source_page", previous["source_page"]))
    previous_bbox = previous.get("last_source_bbox", previous["source_bbox"])
    previous_page_height = previous.get("last_source_page_height", previous.get("source_page_height"))
    previous_chunk = {
        **previous,
        "source_page": previous_page,
        "source_bbox": previous_bbox,
        "source_page_height": previous_page_height,
    }
    if current["source_page"] == previous_page:
        return _same_page_tables_touch(previous_chunk, current)
    if current["source_page"] != previous_page + 1:
        return False
    return _page_break_tables_touch(previous_chunk, current)


def _same_page_tables_touch(
    previous: dict[str, Any],
    current: dict[str, Any],
) -> bool:
    """Return whether same-page tables are close enough to be one chunk."""
    previous_bottom = float(previous["source_bbox"][3])
    current_top = float(current["source_bbox"][1])
    return 0 <= current_top - previous_bottom <= 18


def _page_break_tables_touch(
    previous: dict[str, Any],
    current: dict[str, Any],
) -> bool:
    """Return whether adjacent-page tables straddle a page break."""
    page_height = float(previous.get("source_page_height") or current.get("source_page_height") or 0)
    if page_height <= 0:
        return False
    previous_bottom = float(previous["source_bbox"][3])
    current_top = float(current["source_bbox"][1])
    return previous_bottom >= page_height * 0.75 and current_top <= page_height * 0.25


def _clean_cell(value: Any) -> str:
    """Return one table cell as single-line text."""
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return re.sub(r"(?<=[A-Za-z0-9])-\s+(?=[A-Za-z0-9])", "-", text)


def _clean_extracted_table(
    rows: list[list[Any]],
    header_names: list[Any] | None = None,
) -> tuple[list[list[str]], list[str]]:
    """Normalize detected table rows and headers without drifting column indexes."""
    width = max([len(row) for row in rows] + [len(header_names or [])], default=0)
    cleaned = [[_clean_cell(cell) for cell in [*row, *([None] * (width - len(row)))]] for row in rows]
    cleaned_header = [_clean_cell(cell) for cell in [*(header_names or []), *([None] * (width - len(header_names or [])))]]
    nonblank_rows = [row for row in cleaned if any(row)]
    if not nonblank_rows:
        return [], []
    keep_indexes = [index for index in range(width) if cleaned_header[index] or any(row[index] for row in nonblank_rows)]
    return [[row[index] for index in keep_indexes] for row in nonblank_rows], [cleaned_header[index] for index in keep_indexes]


def _records_from_detected_table(
    rows: list[list[str]],
    header_names: list[Any] | None = None,
) -> tuple[list[str], list[dict[str, str]]]:
    """Return columns and row records for one detected table."""
    header = [_clean_cell(value) for value in header_names or []]
    column_indexes = [index for index, value in enumerate(header) if value]
    if len(column_indexes) < 2:
        column_indexes = []
    if column_indexes:
        columns = _column_names_from_header([header[index] for index in column_indexes])
        data_rows = rows[1:] if _row_matches_header(rows[0], header, column_indexes) else rows
        records = [_record_from_header_indexes(row, columns, column_indexes) for row in data_rows]
        return columns, _merge_continuation_records(records, columns)

    columns = [f"column_{index}" for index in range(1, len(rows[0]) + 1)]
    return columns, [dict(zip(columns, row, strict=False)) for row in rows]


def _records_from_forced_columns(
    rows: list[list[str]],
    columns: list[str],
    min_filled_cells: int = 1,
) -> tuple[list[str], list[dict[str, str]]]:
    """Return records using caller-supplied columns when PDF headers drift."""
    records: list[dict[str, str]] = []
    for row in rows:
        cells = _fit_row_to_columns(row, len(columns))
        filled_indexes = [index for index, value in enumerate(cells) if value]
        if not filled_indexes or _row_matches_forced_columns(cells, columns):
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


def _extend_rows_merging_first_column_continuations(
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


def _fit_row_to_columns(row: list[str], column_count: int) -> list[str]:
    """Fit one detected row to the requested output width."""
    cells = [_clean_cell(cell) for cell in row]
    if len(cells) < column_count:
        return [*cells, *([""] * (column_count - len(cells)))]
    if len(cells) == column_count:
        return cells
    return [*cells[: column_count - 1], " ".join(cell for cell in cells[column_count - 1 :] if cell).strip()]


def _row_matches_forced_columns(row: list[str], columns: list[str]) -> bool:
    """Return whether a row repeats the forced output header."""
    filled_pairs = [(value, columns[index]) for index, value in enumerate(row) if value]
    return bool(filled_pairs) and all(_header_token(value) == _header_token(column) for value, column in filled_pairs)


def _header_token(value: str) -> str:
    """Return a comparable token for detected and requested headers."""
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def _row_matches_header(
    row: list[str],
    header: list[str],
    column_indexes: list[int],
) -> bool:
    """Return whether the first extracted row repeats the PyMuPDF header."""
    return all(index < len(row) and index < len(header) and row[index] == header[index] for index in column_indexes)


def _record_from_header_indexes(
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


def _merge_continuation_records(
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


def _column_names_from_header(header: list[str]) -> list[str]:
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


def _table_rows(pdf_path: Path, table_config: dict[str, Any]) -> list[dict[str, str]]:
    """Extract one configured table."""
    mode = str(table_config["mode"])
    if mode == "line_value":
        return _line_value_rows(pdf_path, table_config)
    if mode == "field_value":
        return _field_value_rows(pdf_path, table_config)
    if mode == "coordinate_table":
        return _coordinate_rows(pdf_path, table_config)
    raise ValueError(f"Unsupported PDF config extraction mode: {mode}")


def _write_csv(
    path: Path,
    rows: list[dict[str, str]],
    columns: list[str],
) -> None:
    """Write one configured CSV."""
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _split_rows(
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


def _split_table_name(
    base_name: str,
    split_value: str,
) -> str:
    """Return a stable table name for one split output."""
    split_name = normalize_source_stem(split_value) if split_value else "unsectioned"
    return f"{base_name}_{split_name}" if split_name else base_name


def _empty_extraction_diagnostics(pdf_path: Path) -> dict[str, Any]:
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
            for detected_index, table_output in enumerate(_pymupdf_table_outputs(pdf_path, table_config), start=int(table_config.get("number", table_index))):
                table_name = f"{base_name}_{detected_index}"
                output_path = output_dir / f"{pdf_stem}_table_{detected_index}.csv"
                _write_csv(output_path, table_output["rows"], table_output["columns"])
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
        rows = _table_rows(pdf_path, table_config)
        table_number = int(table_config.get("number", table_index))
        table_name = str(table_config.get("name", f"table_{table_number}"))
        columns = _configured_output_columns(table_config)
        if not columns and rows:
            columns = list(rows[0])
        if split_by := table_config.get("split_by"):
            split_groups = _split_rows(rows, str(split_by), drop_empty=bool(table_config.get("drop_empty_split")))
            for split_index, (split_value, split_rows) in enumerate(split_groups, start=table_number):
                split_name = _split_table_name(table_name, split_value)
                output_path = output_dir / f"{pdf_stem}_table_{split_index}.csv"
                _write_csv(output_path, split_rows, columns)
                manifest_tables.append(
                    {
                        "name": split_name,
                        "table_number": split_index,
                        "mode": table_config["mode"],
                        "path": str(output_path),
                        "row_count": len(split_rows),
                        "columns": columns,
                        "split_by": str(split_by),
                        "split_value": split_value,
                    }
                )
            continue
        output_path = output_dir / f"{pdf_stem}_table_{table_number}.csv"
        _write_csv(output_path, rows, columns)
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
        "diagnostics": {"warnings": []} if manifest_tables else _empty_extraction_diagnostics(pdf_path),
    }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return {
        **manifest,
        "manifest_path": str(manifest_path),
    }


def _configured_output_columns(table_config: dict[str, Any]) -> list[str]:
    """Return CSV columns from explicit output columns or coordinate specs."""
    if output_columns := table_config.get("output_columns"):
        return [str(column) for column in output_columns]
    columns = table_config.get("columns", [])
    if columns and isinstance(columns[0], dict):
        return [str(column["name"]) for column in columns]
    return [str(column) for column in columns]
