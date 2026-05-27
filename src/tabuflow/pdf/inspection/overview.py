"""Rendered overview images for PDF inspection."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pymupdf

OVERVIEW_THUMB_WIDTH = 224
OVERVIEW_THUMB_HEIGHT = 312
OVERVIEW_LABEL_HEIGHT = 20
OVERVIEW_MARGIN = 14
OVERVIEW_BATCH_COLUMNS = 2
OVERVIEW_BATCH_ROWS = 2
OVERVIEW_BATCH_PAGE_COUNT = OVERVIEW_BATCH_COLUMNS * OVERVIEW_BATCH_ROWS
OVERVIEW_MAX_DPI = 300


def _overview_page_tag(
    *,
    start_page: int,
    end_page: int,
    page_count: int,
) -> str:
    """Return a compact page-range tag for an overview batch."""
    width = max(2, len(str(page_count)))
    if start_page == end_page:
        return f"p{start_page:0{width}d}"
    return f"p{start_page:0{width}d}p{end_page:0{width}d}"


def _write_overview_image(
    document: pymupdf.Document,
    image_path: Path,
    *,
    page_start: int,
    page_end: int,
    dpi: int,
) -> None:
    """Write a 2x2 page contact sheet for visual document overview."""
    cell_width = OVERVIEW_THUMB_WIDTH + OVERVIEW_MARGIN
    cell_height = OVERVIEW_THUMB_HEIGHT + OVERVIEW_LABEL_HEIGHT + OVERVIEW_MARGIN
    overview = pymupdf.open()
    try:
        sheet = overview.new_page(
            width=OVERVIEW_BATCH_COLUMNS * cell_width + OVERVIEW_MARGIN,
            height=OVERVIEW_BATCH_ROWS * cell_height + OVERVIEW_MARGIN,
        )
        for batch_index, page_number in enumerate(range(page_start, page_end + 1)):
            column = batch_index % OVERVIEW_BATCH_COLUMNS
            row = batch_index // OVERVIEW_BATCH_COLUMNS
            x = OVERVIEW_MARGIN + column * cell_width
            y = OVERVIEW_MARGIN + row * cell_height
            sheet.insert_text((x, y + 12), f"Page {page_number}", fontsize=10)
            page_rect = pymupdf.Rect(
                x,
                y + OVERVIEW_LABEL_HEIGHT,
                x + OVERVIEW_THUMB_WIDTH,
                y + OVERVIEW_LABEL_HEIGHT + OVERVIEW_THUMB_HEIGHT,
            )
            sheet.show_pdf_page(page_rect, document, page_number - 1, keep_proportion=True)
        sheet.get_pixmap(dpi=min(dpi, OVERVIEW_MAX_DPI), alpha=False).save(image_path)
    finally:
        overview.close()


def write_overview_batches(
    document: pymupdf.Document,
    output_path: Path,
    *,
    pdf_stem: str,
    dpi: int,
    cache_key: str | None = None,
) -> list[dict[str, Any]]:
    """Write default 2x2 page overview batches for selective visual inspection."""
    batches: list[dict[str, Any]] = []
    output_stem = f"{pdf_stem}_{cache_key}" if cache_key else pdf_stem
    for page_start in range(1, document.page_count + 1, OVERVIEW_BATCH_PAGE_COUNT):
        page_end = min(document.page_count, page_start + OVERVIEW_BATCH_PAGE_COUNT - 1)
        page_tag = _overview_page_tag(start_page=page_start, end_page=page_end, page_count=document.page_count)
        image_path = output_path / f"{output_stem}_overview_{page_tag}.jpg"
        if not image_path.is_file():
            _write_overview_image(document, image_path, page_start=page_start, page_end=page_end, dpi=dpi)
        batches.append(
            {
                "pages": list(range(page_start, page_end + 1)),
                "image_path": str(image_path),
            }
        )
    return batches


def visual_sample_batches(
    overview_batches: list[dict[str, Any]],
    visual_samples: dict[str, Any],
) -> list[dict[str, Any]]:
    """Return overview batches containing representative visual sample pages."""
    sample_page_numbers = {int(page_number) for page_number in visual_samples.get("pages", [])}
    batches: list[dict[str, Any]] = []
    for batch_index, batch in enumerate(overview_batches, start=1):
        pages = [int(page_number) for page_number in batch["pages"]]
        sample_pages = [page_number for page_number in pages if page_number in sample_page_numbers]
        if sample_pages:
            batches.append(
                {
                    "batch": batch_index,
                    "pages": pages,
                    "sample_pages": sample_pages,
                    "image_path": batch["image_path"],
                }
            )
    return batches
