"""Public PDF inspection workflow."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pymupdf

from ...artifacts.naming import normalize_source_stem
from ..common import (
    DEFAULT_DPI,
    DEFAULT_INSPECT_PAGE_LIMIT,
    DEFAULT_INSPECT_TEXT_CHARS,
    DEFAULT_PDF_INSPECT_OUTPUT_DIR,
)
from .overview import visual_sample_batches, write_overview_batches
from .profile import profile_pdf_document, visual_text_rows
from .tables import table_detections, table_region_hints


def inspect_pdf_file(
    path: str | Path,
    *,
    page_start: int = 1,
    page_limit: int = DEFAULT_INSPECT_PAGE_LIMIT,
    max_text_chars: int = DEFAULT_INSPECT_TEXT_CHARS,
    output_dir: str | Path = DEFAULT_PDF_INSPECT_OUTPUT_DIR,
    dpi: int = DEFAULT_DPI,
) -> dict[str, Any]:
    """Return PDF profile evidence and optional focused page details."""
    pdf_path = Path(path).expanduser().resolve()
    if not pdf_path.is_file():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    safe_page_start = max(1, page_start)
    safe_page_limit = max(0, page_limit)
    safe_text_chars = max(0, max_text_chars)
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    pages: list[dict[str, Any]] = []
    overview_batches: list[dict[str, Any]] = []
    with pymupdf.open(str(pdf_path)) as document:
        page_count = document.page_count
        profile = profile_pdf_document(document)
        pdf_stem = normalize_source_stem(pdf_path.name)
        overview_batches = write_overview_batches(document, output_path, pdf_stem=pdf_stem, dpi=dpi)
        if safe_page_limit:
            page_end = min(page_count, safe_page_start + safe_page_limit - 1)
            for page_number in range(safe_page_start, page_end + 1):
                page = document[page_number - 1]
                text = page.get_text("text").strip()
                page_payload: dict[str, Any] = {
                    "page_number": page_number,
                    "table_detections": table_detections(page),
                    "row_geometry": visual_text_rows(page),
                    "text": text[:safe_text_chars],
                    "text_char_count": len(text),
                    "text_truncated": len(text) > safe_text_chars,
                }
                pages.append(page_payload)

    output_profile = dict(profile)
    output_profile.pop("pages", None)
    output_profile["pages_omitted"] = page_count

    selected_overview_batches = visual_sample_batches(overview_batches, profile["summary"]["visual_samples"])
    selected_overview_batch_numbers = {int(batch["batch"]) for batch in selected_overview_batches}

    return {
        "path": str(pdf_path),
        "format": "pdf",
        "status": "ok",
        "page_count": page_count,
        "page_start": safe_page_start,
        "page_end": pages[-1]["page_number"] if pages else safe_page_start - 1,
        "overview_batches": selected_overview_batches,
        "overview_batch_index": [
            {
                "batch": batch_index,
                "pages": batch["pages"],
                "selected": batch_index in selected_overview_batch_numbers,
            }
            for batch_index, batch in enumerate(overview_batches, start=1)
        ],
        "overview_batches_omitted": max(0, len(overview_batches) - len(selected_overview_batches)),
        "profile": output_profile,
        "table_region_hints": table_region_hints(pages),
        "pages": pages,
    }
