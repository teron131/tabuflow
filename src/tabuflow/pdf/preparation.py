"""PDF artifact preparation implementation."""

from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Any

import pymupdf

from .common import (
    DEFAULT_DPI,
    DEFAULT_MAX_PREPARE_PAGES,
    MAX_PREPARE_DPI,
    MIN_PREPARE_DPI,
    PDF_ARTIFACT_VERSION,
    _relative_to_root,
    pdf_artifact_workspace,
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
    workspace = pdf_artifact_workspace(path, root_dir=root_dir, create=False)
    resolved_root_dir = workspace.root_dir
    pdf_path = workspace.pdf_path

    with pymupdf.open(str(pdf_path)) as document:
        page_count = document.page_count
        _validate_prepare_options(
            dpi=dpi,
            page_count=page_count,
            max_pages=max_pages,
        )

        for directory in (workspace.pages_dir, workspace.text_dir, workspace.work_dir, workspace.tables_dir):
            directory.mkdir(parents=True, exist_ok=True)
        if not workspace.source_artifact_path.exists():
            workspace.source_artifact_path.write_bytes(pdf_path.read_bytes())

        pages = _prepare_page_artifacts(
            document=document,
            pages_dir=workspace.pages_dir,
            text_dir=workspace.text_dir,
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
        "normalized_filename": workspace.normalized_filename,
        "source_fingerprint": workspace.source_fingerprint,
        "source_artifact_path": _relative_to_root(workspace.source_artifact_path, resolved_root_dir),
        "artifact_dir": _relative_to_root(workspace.artifact_dir, resolved_root_dir),
        "pages_dir": _relative_to_root(workspace.pages_dir, resolved_root_dir),
        "text_dir": _relative_to_root(workspace.text_dir, resolved_root_dir),
        "work_dir": _relative_to_root(workspace.work_dir, resolved_root_dir),
        "tables_dir": _relative_to_root(workspace.tables_dir, resolved_root_dir),
        "tables_manifest_path": _relative_to_root(workspace.tables_manifest_path, resolved_root_dir),
        "dpi": dpi,
        "max_pages": max_pages,
        "page_count": page_count,
        "prepared_page_count": len(pages),
        "pages": pages,
    }
    manifest_path = workspace.artifact_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    return {
        "path": str(pdf_path),
        "format": "pdf",
        "status": "prepared",
        "artifact_dir": str(workspace.artifact_dir),
        "manifest_path": str(manifest_path),
        "source_artifact_path": str(workspace.source_artifact_path),
        "pages_dir": str(workspace.pages_dir),
        "text_dir": str(workspace.text_dir),
        "work_dir": str(workspace.work_dir),
        "tables_dir": str(workspace.tables_dir),
        "tables_manifest_path": str(workspace.tables_manifest_path),
        "dpi": dpi,
        "max_pages": max_pages,
        "page_count": page_count,
        "prepared_page_count": len(pages),
        "pages": pages,
    }
