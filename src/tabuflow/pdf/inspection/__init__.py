"""PDF inspection package."""

from __future__ import annotations

from ..schemas import PdfInspectionResult
from .workflow import inspect_pdf_file

__all__ = [
    "PdfInspectionResult",
    "inspect_pdf_file",
]
