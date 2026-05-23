"""Standalone LLM-free PDF inspection and preparation tools."""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import re
import shutil
from typing import Any

import pymupdf

from ..tabular.storage import resolve_root_dir

DEFAULT_PAGES_PER_CHUNK = 3
DEFAULT_DPI = 150
DEFAULT_MAX_CONCURRENCY = 1
DEFAULT_PDF_INSPECT_OUTPUT_DIR = Path("data/pdf_inspect")
DEFAULT_PDF_PREPARE_OUTPUT_DIR = Path("artifacts/pdf")
DEFAULT_PDF_EXTRACT_OUTPUT_DIR = Path("data/pdf_ocr")
DEFAULT_INSPECT_PAGE_LIMIT = 2
DEFAULT_INSPECT_TEXT_CHARS = 4_000
DEFAULT_MAX_PREPARE_PAGES = 300
MIN_PREPARE_DPI = 72
MAX_PREPARE_DPI = 300
PDF_ARTIFACT_VERSION = 1
PDF_SLUG_PATTERN = re.compile(r"[^a-z0-9]+")


def pdf_artifact_slug(path: Path) -> str:
    """Return a stable artifact folder name for one PDF source file."""
    stem = PDF_SLUG_PATTERN.sub("-", path.stem.lower()).strip("-") or "pdf"
    digest = hashlib.sha256(path.read_bytes()).hexdigest()[:12]
    return f"{stem}-{digest}"


def _relative_to_root(path: Path, root_dir: Path) -> str:
    """Return a root-relative path when possible."""
    try:
        return str(path.resolve().relative_to(root_dir))
    except ValueError:
        return str(path.resolve())


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
    """Render every PDF page and return manifest-ready page entries."""
    pages: list[dict[str, Any]] = []
    page_width = max(3, len(str(document.page_count)))
    for page_number in range(1, document.page_count + 1):
        page = document[page_number - 1]
        page_name = f"page_{page_number:0{page_width}d}"
        image_path = pages_dir / f"{page_name}.jpg"
        text_path = text_dir / f"{page_name}.txt"
        text = page.get_text("text").strip()
        image_path.write_bytes(page.get_pixmap(dpi=dpi).tobytes("jpeg"))
        text_path.write_text(f"{text}\n" if text else "", encoding="utf-8")
        pages.append(
            {
                "page_number": page_number,
                "image_path": _relative_to_root(image_path, root_dir),
                "text_path": _relative_to_root(text_path, root_dir),
                "text_char_count": len(text),
            }
        )
    return pages


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
        "status": "ok",
        "route": "deterministic_pdf_inspect",
        "llm_required": False,
        "page_count": page_count,
        "page_start": safe_page_start,
        "page_end": pages[-1]["page_number"] if pages else safe_page_start - 1,
        "image_output_dir": str(output_path) if include_images else None,
        "pages": pages,
    }


def prepare_pdf_file(
    path: str | Path,
    *,
    root_dir: str | Path | None = None,
    output_dir: str | Path = DEFAULT_PDF_PREPARE_OUTPUT_DIR,
    dpi: int = DEFAULT_DPI,
    max_pages: int | None = DEFAULT_MAX_PREPARE_PAGES,
    copy_source: bool = True,
) -> dict[str, Any]:
    """Create a resumable PDF artifact workspace with page images, text, and manifest."""
    resolved_root_dir = resolve_root_dir(root_dir=root_dir)
    pdf_path = Path(path).expanduser()
    if not pdf_path.is_absolute():
        pdf_path = resolved_root_dir / pdf_path
    pdf_path = pdf_path.resolve()
    if not pdf_path.is_file():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    output_path = Path(output_dir)
    if not output_path.is_absolute():
        output_path = resolved_root_dir / output_path
    artifact_dir = output_path / pdf_artifact_slug(pdf_path)
    pages_dir = artifact_dir / "pages"
    text_dir = artifact_dir / "text"
    work_dir = artifact_dir / "work"
    import_dir = artifact_dir / "import"

    with pymupdf.open(str(pdf_path)) as document:
        page_count = document.page_count
        _validate_prepare_options(
            dpi=dpi,
            page_count=page_count,
            max_pages=max_pages,
        )

        for directory in (pages_dir, text_dir, work_dir, import_dir):
            directory.mkdir(parents=True, exist_ok=True)

        source_artifact_path = artifact_dir / "source.pdf"
        if copy_source:
            shutil.copy2(pdf_path, source_artifact_path)

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
        "source_artifact_path": _relative_to_root(source_artifact_path, resolved_root_dir) if copy_source else None,
        "artifact_dir": _relative_to_root(artifact_dir, resolved_root_dir),
        "pages_dir": _relative_to_root(pages_dir, resolved_root_dir),
        "text_dir": _relative_to_root(text_dir, resolved_root_dir),
        "work_dir": _relative_to_root(work_dir, resolved_root_dir),
        "import_dir": _relative_to_root(import_dir, resolved_root_dir),
        "dpi": dpi,
        "max_pages": max_pages,
        "page_count": page_count,
        "prepared_page_count": len(pages),
        "pages": pages,
        "next_steps": [
            "Read pages/*.jpg visually and text/*.txt semantically.",
            "Write recovered tables into work/*.csv or work/*.json.",
            "Import prepared table artifacts into SQLite when ready.",
        ],
    }
    manifest_path = artifact_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    return {
        "path": str(pdf_path),
        "format": "pdf",
        "status": "prepared",
        "route": "deterministic_pdf_prepare",
        "artifact_dir": str(artifact_dir),
        "manifest_path": str(manifest_path),
        "source_artifact_path": str(source_artifact_path) if copy_source else None,
        "pages_dir": str(pages_dir),
        "text_dir": str(text_dir),
        "work_dir": str(work_dir),
        "import_dir": str(import_dir),
        "dpi": dpi,
        "max_pages": max_pages,
        "page_count": page_count,
        "prepared_page_count": len(pages),
        "pages": pages,
    }


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
    """Return the agent-managed PDF extraction boundary without running an LLM."""
    resolved_root_dir = resolve_root_dir(root_dir=root_dir)
    pdf_path = Path(path).expanduser()
    if not pdf_path.is_absolute():
        pdf_path = resolved_root_dir / pdf_path
    pdf_path = pdf_path.resolve()
    return {
        "path": str(pdf_path),
        "format": "pdf",
        "status": "agent_required",
        "route": "agent_managed_pdf_extraction",
        "artifact_backend": "sqlite",
        "database_path": "",
        "tables": [],
        "requested_options": {
            "output_dir": str(output_dir),
            "model": model,
            "pages_per_chunk": pages_per_chunk,
            "max_concurrency": max_concurrency,
            "dpi": dpi,
            "max_chunks": max_chunks,
            "fix_bridges": fix_bridges,
            "fix_overall": fix_overall,
            "write_markdown": write_markdown,
        },
        "message": "PDF table extraction is agent-managed. Use prepare_pdf_file to create page image/text artifacts, write recovered tables into the work directory, then import them through the tabular or artifact tools.",
    }
