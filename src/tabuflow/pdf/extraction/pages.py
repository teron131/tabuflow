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


def document_lines(
    pdf_path: Path,
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    """Read configured pages as cleaned line records."""
    records: list[dict[str, Any]] = []
    with pymupdf.open(str(pdf_path)) as document:
        for page_number in page_numbers(document, config):
            for line in page_lines(document[page_number - 1], page_number, config):
                records.append(
                    {
                        "page": page_number,
                        "text": line,
                    }
                )
    return records
