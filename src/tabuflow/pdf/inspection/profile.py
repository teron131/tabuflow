"""Layout and text-geometry profiling for PDF inspection."""

from __future__ import annotations

from typing import Any

import pymupdf

PROFILE_IMAGE_PAGE_LIMIT = 10
BAND_LABELS = ("top", "middle", "bottom")
COLUMN_LABELS = ("left", "center", "right")
ROW_GEOMETRY_Y_TOLERANCE = 3.5
MAX_ROW_GEOMETRY_ROWS = 90
MAX_ROW_GEOMETRY_WORDS_PER_ROW = 36


def _band_index(
    value: float,
    extent: float,
) -> int:
    """Return a coarse 0-2 band index for a page coordinate."""
    if extent <= 0:
        return 0
    return max(0, min(2, int(value / extent * 3)))


def _short_bands(
    labels: tuple[str, str, str],
    indexes: set[int],
) -> str:
    """Return compact initials for occupied page bands."""
    return "".join(labels[index][0] for index in sorted(indexes)) or "none"


def _text_blocks(blocks: list[Any]) -> list[Any]:
    """Return PyMuPDF text blocks with non-empty text."""
    return [block for block in blocks if (len(block) < 7 or int(block[6]) == 0) and str(block[4]).strip()]


def visual_text_rows(
    page: pymupdf.Page,
    *,
    y_tolerance: float = ROW_GEOMETRY_Y_TOLERANCE,
    max_rows: int = MAX_ROW_GEOMETRY_ROWS,
) -> dict[str, Any]:
    """Return bounded word rows with geometry for table-aware PDF inspection."""
    grouped_rows: list[dict[str, Any]] = []
    for word in sorted(page.get_text("words"), key=lambda item: (round(float(item[1]) / y_tolerance) * y_tolerance, float(item[0]))):
        x0, y0, x1, y1, text, *_rest = word
        if not grouped_rows or abs(float(y0) - float(grouped_rows[-1]["y"])) > y_tolerance:
            grouped_rows.append(
                {
                    "y": float(y0),
                    "bbox": [float(x0), float(y0), float(x1), float(y1)],
                    "words": [],
                }
            )
        row = grouped_rows[-1]
        row["bbox"][0] = min(float(row["bbox"][0]), float(x0))
        row["bbox"][1] = min(float(row["bbox"][1]), float(y0))
        row["bbox"][2] = max(float(row["bbox"][2]), float(x1))
        row["bbox"][3] = max(float(row["bbox"][3]), float(y1))
        if len(row["words"]) < MAX_ROW_GEOMETRY_WORDS_PER_ROW:
            row["words"].append(
                {
                    "x0": round(float(x0), 1),
                    "x1": round(float(x1), 1),
                    "text": str(text),
                }
            )
        else:
            row["truncated_words"] = True

    rows: list[dict[str, Any]] = []
    for row_index, row in enumerate(grouped_rows[:max_rows], start=1):
        words = sorted(row["words"], key=lambda word: float(word["x0"]))
        rows.append(
            {
                "row_id": row_index,
                "y": round(float(row["y"]), 1),
                "bbox": [round(float(value), 1) for value in row["bbox"]],
                "text": " ".join(str(word["text"]) for word in words),
                "words": words,
                "truncated_words": bool(row.get("truncated_words")),
            }
        )

    return {
        "source": "pymupdf_words_grouped_by_visual_y",
        "y_tolerance": y_tolerance,
        "row_count": len(grouped_rows),
        "rows": rows,
        "truncated": len(grouped_rows) > max_rows,
    }


def _drawn_line_profile(page: pymupdf.Page) -> dict[str, Any]:
    """Return counts and coarse location bands for rule-like page drawings."""
    horizontal = 0
    vertical = 0
    other = 0
    horizontal_y_bands: set[int] = set()
    vertical_x_bands: set[int] = set()
    tolerance = 1.5
    for drawing in page.get_drawings():
        for item in drawing.get("items", []):
            kind = item[0]
            if kind == "l" and len(item) >= 3:
                start, end = item[1], item[2]
                dx = abs(float(start.x) - float(end.x))
                dy = abs(float(start.y) - float(end.y))
                if dx <= tolerance and dy > tolerance:
                    vertical += 1
                    vertical_x_bands.add(_band_index((float(start.x) + float(end.x)) / 2, float(page.rect.width)))
                elif dy <= tolerance and dx > tolerance:
                    horizontal += 1
                    horizontal_y_bands.add(_band_index((float(start.y) + float(end.y)) / 2, float(page.rect.height)))
                else:
                    other += 1
            elif kind == "re" and len(item) >= 2:
                rect = item[1]
                horizontal += 2
                vertical += 2
                horizontal_y_bands.update({_band_index(float(rect.y0), float(page.rect.height)), _band_index(float(rect.y1), float(page.rect.height))})
                vertical_x_bands.update({_band_index(float(rect.x0), float(page.rect.width)), _band_index(float(rect.x1), float(page.rect.width))})
            else:
                other += 1
    return {
        "counts": {
            "horizontal": horizontal,
            "vertical": vertical,
            "other": other,
            "total": horizontal + vertical + other,
        },
        "geometry": {
            "horizontal_y_bands": _short_bands(BAND_LABELS, horizontal_y_bands),
            "vertical_x_bands": _short_bands(COLUMN_LABELS, vertical_x_bands),
        },
    }


def _text_geometry(
    blocks: list[Any],
    page: pymupdf.Page,
) -> dict[str, str]:
    """Return coarse location bands for text block centers."""
    x_bands: set[int] = set()
    y_bands: set[int] = set()
    grid_cells: set[str] = set()
    for block in _text_blocks(blocks):
        x0, y0, x1, y1, *_rest = block
        x_index = _band_index((float(x0) + float(x1)) / 2, float(page.rect.width))
        y_index = _band_index((float(y0) + float(y1)) / 2, float(page.rect.height))
        x_bands.add(x_index)
        y_bands.add(y_index)
        grid_cells.add(f"{BAND_LABELS[y_index][0]}{COLUMN_LABELS[x_index][0]}")
    return {
        "x_bands": _short_bands(COLUMN_LABELS, x_bands),
        "y_bands": _short_bands(BAND_LABELS, y_bands),
        "grid": ".".join(sorted(grid_cells)) or "none",
    }


def _font_profile(page: pymupdf.Page) -> dict[str, Any]:
    """Return compact span/font signals without preserving full text."""
    sizes: list[float] = []
    for block in page.get_text("dict").get("blocks", []):
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                sizes.append(round(float(span.get("size", 0)), 1))
    if not sizes:
        return {
            "span_count": 0,
            "max_size": 0,
            "median_size": 0,
            "large_span_count": 0,
        }
    sorted_sizes = sorted(sizes)
    median_size = sorted_sizes[len(sorted_sizes) // 2]
    return {
        "span_count": len(sizes),
        "max_size": max(sizes),
        "median_size": median_size,
        "large_span_count": sum(1 for size in sizes if size >= median_size * 1.6 and size >= 16),
    }


def _density_bucket(
    value: int,
    *,
    sparse: int,
    dense: int,
) -> str:
    """Bucket a page-level count for layout signatures."""
    if value == 0:
        return "none"
    if value < sparse:
        return "sparse"
    if value >= dense:
        return "dense"
    return "medium"


def _page_layout_signature(page_profile: dict[str, Any]) -> str:
    """Return a coarse layout signature for representative-page selection."""
    has_large_text = "yes" if int(page_profile["font_profile"]["large_span_count"]) else "no"
    return "|".join(
        [
            f"text:{_density_bucket(int(page_profile['word_count']), sparse=40, dense=250)}",
            f"lines:{_density_bucket(int(page_profile['drawn_lines']['total']), sparse=8, dense=40)}",
            f"text_x:{page_profile['text_geometry']['x_bands']}",
            f"text_y:{page_profile['text_geometry']['y_bands']}",
            f"line_h:{page_profile['line_geometry']['horizontal_y_bands']}",
            f"line_v:{page_profile['line_geometry']['vertical_x_bands']}",
            f"large_text:{has_large_text}",
        ]
    )


def _page_hints(page_profile: dict[str, Any]) -> list[str]:
    """Return compact route-selection hints for one page."""
    hints: list[str] = []
    if page_profile["text_char_count"] == 0:
        hints.append("no_extractable_text")
    drawn_lines = page_profile["drawn_lines"]
    if drawn_lines["horizontal"] >= 3 and drawn_lines["vertical"] >= 3:
        hints.append("visible_rule_grid")
    if page_profile["font_profile"]["large_span_count"]:
        hints.append("large_text_regions")
    return hints


def _layout_sample_pages(
    page_profiles: list[dict[str, Any]],
    *,
    page_count: int,
) -> dict[str, Any]:
    """Choose representative pages for selective visual inspection."""
    pages: list[int] = [1]
    seen_signatures: set[str] = set()
    reasons: dict[int, list[str]] = {1: ["first_page"]}
    selected_signature_count = 0
    for page_profile in page_profiles:
        signature = str(page_profile["layout_signature"])
        if signature in seen_signatures:
            continue
        seen_signatures.add(signature)
        page_number = int(page_profile["page_number"])
        if page_number in pages:
            reasons.setdefault(page_number, []).append(f"layout_signature:{signature}")
            selected_signature_count += 1
        elif len(pages) < PROFILE_IMAGE_PAGE_LIMIT - 1:
            pages.append(page_number)
            reasons.setdefault(page_number, []).append(f"layout_signature:{signature}")
            selected_signature_count += 1
    if page_count not in pages:
        pages.append(page_count)
    reasons.setdefault(page_count, []).append("last_page")
    selected_pages = pages[:PROFILE_IMAGE_PAGE_LIMIT]
    if len(pages) > PROFILE_IMAGE_PAGE_LIMIT:
        selected_pages = [*pages[: PROFILE_IMAGE_PAGE_LIMIT - 1], page_count]
    selected_pages = sorted(selected_pages)
    return {
        "pages": selected_pages,
        "method": "first page, first page per layout signature up to the profile sample cap, last page",
        "reasons": {str(page_number): reasons.get(page_number, []) for page_number in selected_pages},
        "sample_cap": PROFILE_IMAGE_PAGE_LIMIT,
        "omitted_layout_signature_count": max(0, len(seen_signatures) - selected_signature_count),
    }


def profile_pdf_document(document: pymupdf.Document) -> dict[str, Any]:
    """Return a cheap document-wide PDF layout profile for route selection."""
    page_profiles: list[dict[str, Any]] = []
    for page_number in range(1, document.page_count + 1):
        page = document[page_number - 1]
        text = page.get_text("text").strip()
        words = page.get_text("words")
        blocks = page.get_text("blocks")
        drawn_lines = _drawn_line_profile(page)
        page_profile: dict[str, Any] = {
            "page_number": page_number,
            "width": round(float(page.rect.width), 2),
            "height": round(float(page.rect.height), 2),
            "text_char_count": len(text),
            "word_count": len(words),
            "text_block_count": len(_text_blocks(blocks)),
            "drawn_lines": drawn_lines["counts"],
            "text_geometry": _text_geometry(blocks, page),
            "line_geometry": drawn_lines["geometry"],
            "font_profile": _font_profile(page),
        }
        page_profile["layout_signature"] = _page_layout_signature(page_profile)
        page_profile["hints"] = _page_hints(page_profile)
        page_profiles.append(page_profile)

    signature_counts: dict[str, int] = {}
    for page_profile in page_profiles:
        signature = str(page_profile["layout_signature"])
        signature_counts[signature] = signature_counts.get(signature, 0) + 1

    return {
        "summary": {
            "page_count": document.page_count,
            "text_char_count": sum(int(page["text_char_count"]) for page in page_profiles),
            "word_count": sum(int(page["word_count"]) for page in page_profiles),
            "drawn_line_count": sum(int(page["drawn_lines"]["total"]) for page in page_profiles),
            "layout_signature_count": len(signature_counts),
            "visual_samples": _layout_sample_pages(page_profiles, page_count=document.page_count),
        },
        "layout_signatures": [
            {
                "signature": signature,
                "page_count": count,
                "sample_pages": [int(page["page_number"]) for page in page_profiles if page["layout_signature"] == signature][:5],
            }
            for signature, count in sorted(signature_counts.items(), key=lambda item: (-item[1], item[0]))
        ],
        "pages": page_profiles,
    }
