"""PDF page and text-line helpers for extraction."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pymupdf


def page_numbers(
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


def page_lines(
    page: pymupdf.Page,
    page_number: int,
    config: dict[str, Any],
) -> list[str]:
    """Return cleaned non-empty page text lines."""
    return [record["text"] for record in page_line_records(page, page_number, config)]


def page_line_records(
    page: pymupdf.Page,
    page_number: int,
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    """Return cleaned page text lines with their visual line position."""
    records: list[dict[str, Any]] = []
    skip_lines = set(config.get("skip_lines", []))
    skip_prefixes = list(config.get("skip_prefixes", []))
    line_groups: dict[tuple[int, int], list[tuple[float, float, str]]] = {}
    for word in page.get_text("words"):
        x0, y0, _x1, _y1, text, block_number, line_number, *_rest = word
        line_groups.setdefault((int(block_number), int(line_number)), []).append((float(x0), float(y0), str(text)))
    for words in sorted(line_groups.values(), key=lambda group: (min(word[1] for word in group), min(word[0] for word in group))):
        ordered_words = sorted(words, key=lambda word: word[0])
        line = " ".join(word[2] for word in ordered_words).strip()
        if not line:
            continue
        if line == str(page_number):
            continue
        if any(line.startswith(prefix) for prefix in config.get("stop_prefixes", [])):
            break
        if line in skip_lines or any(line.startswith(prefix) for prefix in skip_prefixes):
            continue
        records.append(
            {
                "page": page_number,
                "text": line,
                "x0": min(word[0] for word in ordered_words),
                "y0": min(word[1] for word in ordered_words),
            }
        )
    return records


def document_lines(
    pdf_path: Path,
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    """Read configured pages as cleaned line records."""
    records: list[dict[str, Any]] = []
    with pymupdf.open(str(pdf_path)) as document:
        for page_number in page_numbers(document, config):
            records.extend(page_line_records(document[page_number - 1], page_number, config))
    return records
