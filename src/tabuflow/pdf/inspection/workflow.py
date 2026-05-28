"""Public PDF inspection workflow."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

import pymupdf

from ...artifacts.naming import normalize_source_stem
from ..common import (
    DEFAULT_DPI,
    DEFAULT_INSPECT_PAGE_LIMIT,
    DEFAULT_INSPECT_TEXT_CHARS,
    pdf_artifact_workspace,
)
from ..schemas import dump_pdf_inspection_result
from .overview import visual_sample_batches, write_overview_batches
from .profile import profile_pdf_document, visual_text_rows
from .tables import table_detections, table_region_hints

PROFILE_CACHE_VERSION = 2


@dataclass(frozen=True)
class PdfInspectionCache:
    """Source-owned inspect cache paths and IO helpers."""

    directory: Path
    pdf_stem: str
    source_fingerprint: str
    dpi: int

    @property
    def fingerprint_tag(self) -> str:
        """Return a compact stable fingerprint tag for inspect cache filenames."""
        return self.source_fingerprint[:12]

    @property
    def profile_path(self) -> Path:
        """Return the cached profile path for this source PDF."""
        return self.directory / f"{self.pdf_stem}_{self.fingerprint_tag}_profile_v{PROFILE_CACHE_VERSION}.json"

    @property
    def overview_key(self) -> str:
        """Return the cache key used in overview image filenames."""
        return f"{self.fingerprint_tag}_dpi{self.dpi}"

    def read_profile(self) -> dict[str, Any] | None:
        """Return a cached profile payload when it matches the current cache version."""
        if not self.profile_path.is_file():
            return None
        try:
            payload = json.loads(self.profile_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if payload.get("cache_version") != PROFILE_CACHE_VERSION or not isinstance(payload.get("profile"), dict):
            return None
        return payload["profile"]

    def write_profile(self, profile: dict[str, Any]) -> None:
        """Write a reusable profile payload for repeated inspect calls."""
        self.profile_path.write_text(
            json.dumps(
                {
                    "cache_version": PROFILE_CACHE_VERSION,
                    "profile": profile,
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )


def inspect_pdf_file(
    path: str | Path,
    *,
    page_start: int = 1,
    page_limit: int = DEFAULT_INSPECT_PAGE_LIMIT,
    max_text_chars: int = DEFAULT_INSPECT_TEXT_CHARS,
    root_dir: str | Path | None = None,
    dpi: int = DEFAULT_DPI,
) -> dict[str, Any]:
    """Return PDF profile evidence and optional focused page details."""
    workspace = pdf_artifact_workspace(path, root_dir=root_dir)
    pdf_path = workspace.pdf_path
    if not pdf_path.is_file():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    safe_page_start = max(1, page_start)
    safe_page_limit = max(0, page_limit)
    safe_text_chars = max(0, max_text_chars)
    output_path = workspace.inspect_dir
    output_path.mkdir(parents=True, exist_ok=True)
    pdf_stem = normalize_source_stem(pdf_path.name)
    inspect_cache = PdfInspectionCache(
        directory=output_path,
        pdf_stem=pdf_stem,
        source_fingerprint=workspace.source_fingerprint,
        dpi=dpi,
    )

    pages: list[dict[str, Any]] = []
    page_heights: dict[int, float] = {}
    overview_batches: list[dict[str, Any]] = []
    with pymupdf.open(str(pdf_path)) as document:
        page_count = document.page_count
        profile = inspect_cache.read_profile()
        if profile is None:
            profile = profile_pdf_document(document)
            inspect_cache.write_profile(profile)
        overview_batches = write_overview_batches(
            document,
            output_path,
            pdf_stem=pdf_stem,
            dpi=dpi,
            cache_key=inspect_cache.overview_key,
        )
        if safe_page_limit:
            page_end = min(page_count, safe_page_start + safe_page_limit - 1)
            for page_number in range(safe_page_start, page_end + 1):
                page = document[page_number - 1]
                text = page.get_text("text").strip()
                page_heights[page_number] = round(float(page.rect.height), 1)
                page_payload: dict[str, Any] = {
                    "page_number": page_number,
                    "table_detections": table_detections(page),
                    "row_geometry": visual_text_rows(page),
                    "text": text[:safe_text_chars],
                    "text_truncated": len(text) > safe_text_chars,
                }
                pages.append(page_payload)

    output_profile = {
        "visual_samples": profile["visual_samples"],
        "layout_signatures": profile["layout_signatures"],
    }

    selected_overview_batches = visual_sample_batches(overview_batches, profile["visual_samples"])
    selected_overview_batch_numbers = {int(batch["batch"]) for batch in selected_overview_batches}

    output: dict[str, Any] = {
        "path": str(pdf_path),
        "page_count": page_count,
        "overview_batches": selected_overview_batches,
        "overview_batch_index": [
            {
                "batch": batch_index,
                "pages": batch["pages"],
                "selected": batch_index in selected_overview_batch_numbers,
            }
            for batch_index, batch in enumerate(overview_batches, start=1)
        ],
        "profile": output_profile,
    }
    if pages:
        output.update(
            {
                "page_start": safe_page_start,
                "page_end": pages[-1]["page_number"],
                "table_region_hints": table_region_hints(pages, page_heights=page_heights),
                "pages": pages,
            }
        )
    return dump_pdf_inspection_result(output)
