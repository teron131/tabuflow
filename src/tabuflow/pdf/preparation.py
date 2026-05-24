"""PDF artifact preparation implementation."""

from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Any

import pymupdf

from ..artifacts.naming import normalize_source_filename, normalize_source_stem
from ..tabular.storage import resolve_root_dir
from .common import (
    DEFAULT_DPI,
    DEFAULT_MAX_PREPARE_PAGES,
    DEFAULT_PDF_PREPARE_OUTPUT_DIR,
    MAX_PREPARE_DPI,
    MIN_PREPARE_DPI,
    PDF_ARTIFACT_VERSION,
    PDF_TABLES_DIR_NAME,
    PDF_TABLES_MANIFEST_NAME,
    _pdf_artifact_dir,
    _relative_to_root,
    pdf_source_fingerprint,
)


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
