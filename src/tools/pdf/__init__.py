"""PDF extraction helpers."""

from __future__ import annotations

from typing import Any

from .tools import (
    DEFAULT_DPI,
    DEFAULT_INSPECT_PAGE_LIMIT,
    DEFAULT_INSPECT_TEXT_CHARS,
    DEFAULT_MAX_CONCURRENCY,
    DEFAULT_PAGES_PER_CHUNK,
    extract_pdf_file,
    inspect_pdf_file,
)

__all__ = [
    "DEFAULT_DPI",
    "DEFAULT_INSPECT_PAGE_LIMIT",
    "DEFAULT_INSPECT_TEXT_CHARS",
    "DEFAULT_MAX_CONCURRENCY",
    "DEFAULT_PAGES_PER_CHUNK",
    "PdfTableOcrResult",
    "extract_pdf_file",
    "extract_pdf_tables",
    "inspect_pdf_file",
]

_LAZY_OCR_EXPORTS = {"PdfTableOcrResult", "extract_pdf_tables"}


def __getattr__(name: str) -> Any:
    """Load model-backed PDF OCR exports only when requested."""
    if name not in _LAZY_OCR_EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    from . import llm_ocr_tables

    value = getattr(llm_ocr_tables, name)
    globals()[name] = value
    return value
