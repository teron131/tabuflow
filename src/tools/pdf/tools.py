"""LangChain tools for inspecting and extracting PDF table data."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from langchain.tools import tool
import pymupdf

from ..tabular.storage import fingerprint, load_tables_into_sqlite, resolve_root_dir
from .llm_ocr_tables import (
    DEFAULT_DPI,
    DEFAULT_MAX_CONCURRENCY,
    DEFAULT_PAGES_PER_CHUNK,
    extract_pdf_tables,
)

DEFAULT_PDF_INSPECT_OUTPUT_DIR = Path("data/pdf_inspect")
DEFAULT_PDF_EXTRACT_OUTPUT_DIR = Path("data/pdf_ocr")
DEFAULT_INSPECT_PAGE_LIMIT = 2
DEFAULT_INSPECT_TEXT_CHARS = 4_000


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
                image_path = output_path / f"{pdf_path.stem}_page_{page_number}.jpg"
                image_path.write_bytes(page.get_pixmap(dpi=dpi).tobytes("jpeg"))
                page_payload["image_path"] = str(image_path)
            pages.append(page_payload)

    return {
        "path": str(pdf_path),
        "format": "pdf",
        "page_count": page_count,
        "page_start": safe_page_start,
        "page_end": pages[-1]["page_number"] if pages else safe_page_start - 1,
        "pages": pages,
    }


def _normalized_pdf_tables(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Return PDF OCR tables in the shared tabular-loader shape."""
    normalized_tables: list[dict[str, Any]] = []
    for table_index, table in enumerate(payload.get("tables", []), start=1):
        if not isinstance(table, dict):
            continue
        columns = [str(column or "").strip() or f"column_{idx}" for idx, column in enumerate(table.get("columns", []), start=1)]
        rows = table.get("rows", [])
        if not columns or not isinstance(rows, list):
            continue
        width = len(columns)
        normalized_rows = [[str(cell or "") for cell in row[:width]] + [""] * max(0, width - len(row)) for row in rows if isinstance(row, list)]
        if not normalized_rows:
            continue
        table_name = str(table.get("title") or "").strip() or f"table_{table_index:03d}"
        normalized_tables.append(
            {
                "name": table_name,
                "columns": columns,
                "rows": normalized_rows,
            }
        )
    return normalized_tables


def _pdf_tables_fingerprint(tables: list[dict[str, Any]]) -> str:
    """Build a deterministic fingerprint for recovered PDF tables."""
    rows: list[list[str]] = []
    for table in tables:
        rows.append([str(table["name"])])
        rows.append([str(column) for column in table["columns"]])
        rows.extend([str(cell) for cell in row] for row in table["rows"])
    return fingerprint(rows, max_sample_rows=max(len(rows), 1), header_candidates=[])


def extract_pdf_file(
    path: str | Path,
    *,
    root_dir: str | Path | None = None,
    output_dir: str | Path = DEFAULT_PDF_EXTRACT_OUTPUT_DIR,
    model: str | None = None,
    pages_per_chunk: int = DEFAULT_PAGES_PER_CHUNK,
    max_concurrency: int = DEFAULT_MAX_CONCURRENCY,
    dpi: int = DEFAULT_DPI,
    max_chunks: int | None = None,
    fix_bridges: bool = True,
    fix_overall: bool = True,
    write_markdown: bool = True,
) -> dict[str, Any]:
    """Extract visual PDF tables and load them into the shared SQLite cache."""
    resolved_root_dir = resolve_root_dir(root_dir=root_dir)
    pdf_path = Path(path).expanduser()
    if not pdf_path.is_absolute():
        pdf_path = resolved_root_dir / pdf_path
    pdf_path = pdf_path.resolve()

    ocr_result = extract_pdf_tables(
        pdf_path,
        output_dir=output_dir,
        model=model,
        pages_per_chunk=pages_per_chunk,
        max_concurrency=max_concurrency,
        dpi=dpi,
        max_chunks=max_chunks,
        fix_bridges=fix_bridges,
        fix_overall=fix_overall,
        write_markdown=write_markdown,
    )
    ocr_payload = json.loads(ocr_result.json_path.read_text(encoding="utf-8"))
    recovered_tables = _normalized_pdf_tables(ocr_payload)
    if not recovered_tables:
        return {
            "path": str(pdf_path),
            "format": "pdf_ocr",
            "status": "empty",
            "artifact_backend": "sqlite",
            "database_path": "",
            "ocr_sqlite_path": str(ocr_result.sqlite_path),
            "result_json_path": str(ocr_result.json_path),
            "markdown_path": None if ocr_result.markdown_path is None else str(ocr_result.markdown_path),
            "recovered_table_count": 0,
            "tables": [],
            "message": "PDF OCR completed but did not recover importable tables.",
        }

    recovered = {
        "path": str(pdf_path),
        "format": "pdf_ocr",
        "tables": recovered_tables,
    }
    loaded = load_tables_into_sqlite(
        recovered,
        root_dir=resolved_root_dir,
        fingerprint=_pdf_tables_fingerprint(recovered_tables),
    )

    return {
        "path": str(pdf_path),
        "format": "pdf_ocr",
        "status": "loaded",
        "artifact_backend": "sqlite",
        "database_path": loaded["database_path"],
        "ocr_sqlite_path": str(ocr_result.sqlite_path),
        "result_json_path": str(ocr_result.json_path),
        "markdown_path": None if ocr_result.markdown_path is None else str(ocr_result.markdown_path),
        "recovered_table_count": len(recovered_tables),
        "tables": loaded["tables"],
        "usage": {
            "input_tokens": ocr_result.usage.input_tokens,
            "output_tokens": ocr_result.usage.output_tokens,
            "cost": ocr_result.usage.cost,
        },
    }


def make_pdf_tools(*, root_dir: str | Path | None = None):
    """Create PDF inspect and extraction tools."""
    resolved_root_dir = resolve_root_dir(root_dir=root_dir)

    def source_path(path: str) -> Path:
        resolved_path = Path(path).expanduser()
        return resolved_path if resolved_path.is_absolute() else resolved_root_dir / resolved_path

    @tool(parse_docstring=True)
    def inspect_pdf(
        path: str,
        page_start: int = 1,
        page_limit: int = DEFAULT_INSPECT_PAGE_LIMIT,
        max_text_chars: int = DEFAULT_INSPECT_TEXT_CHARS,
        include_images: bool = False,
    ) -> dict[str, Any]:
        """Inspect a PDF with raw page text and optional rendered page images.

        Args:
            path: Path to the PDF file to inspect.
            page_start: One-based page number where inspection begins.
            page_limit: Maximum number of pages to inspect.
            max_text_chars: Maximum text characters to include per page.
            include_images: Whether to render inspected pages to image artifacts.
        """
        return inspect_pdf_file(
            source_path(path),
            page_start=page_start,
            page_limit=page_limit,
            max_text_chars=max_text_chars,
            include_images=include_images,
        )

    @tool(parse_docstring=True)
    def extract_pdf(
        path: str,
        pages_per_chunk: int = DEFAULT_PAGES_PER_CHUNK,
        max_chunks: int | None = None,
        fix_bridges: bool = True,
        fix_overall: bool = True,
    ) -> dict[str, Any]:
        """Extract visual tables from a PDF into the shared SQLite cache.

        Args:
            path: Path to the PDF file to extract.
            pages_per_chunk: Number of PDF pages rendered into each OCR request.
            max_chunks: Optional cap on rendered chunks for bounded trial runs.
            fix_bridges: Whether to repair tables that cross chunk boundaries.
            fix_overall: Whether to run a final full-document table cleanup.
        """
        return extract_pdf_file(
            path,
            root_dir=resolved_root_dir,
            pages_per_chunk=pages_per_chunk,
            max_chunks=max_chunks,
            fix_bridges=fix_bridges,
            fix_overall=fix_overall,
        )

    return [
        inspect_pdf,
        extract_pdf,
    ]
