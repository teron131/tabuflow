"""Shared PDF artifact paths and identity helpers."""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path

from ..artifacts.naming import normalize_source_filename, normalize_source_stem
from ..workspace_db import resolve_root_dir

DEFAULT_DPI = 150
DEFAULT_PDF_INSPECT_OUTPUT_DIR = Path("data/pdf_inspect")
DEFAULT_PDF_PREPARE_OUTPUT_DIR = Path("artifacts/pdf")
DEFAULT_INSPECT_PAGE_LIMIT = 2
DEFAULT_INSPECT_TEXT_CHARS = 4_000
DEFAULT_MAX_PREPARE_PAGES = 300
MIN_PREPARE_DPI = 72
MAX_PREPARE_DPI = 300
PDF_ARTIFACT_VERSION = 1
PDF_TABLES_DIR_NAME = "tables"
PDF_TABLES_MANIFEST_NAME = "tables_manifest.json"


def pdf_source_fingerprint(path: Path) -> str:
    """Return the exact source-file fingerprint used for PDF artifact identity."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _relative_to_root(path: Path, root_dir: Path) -> str:
    """Return a root-relative path when possible."""
    try:
        return str(path.resolve().relative_to(root_dir))
    except ValueError:
        return str(path.resolve())


def _manifest_source_fingerprint(artifact_dir: Path) -> str | None:
    """Return the source fingerprint recorded by an existing PDF artifact manifest."""
    manifest_path = artifact_dir / "manifest.json"
    if not manifest_path.is_file():
        return None
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    value = manifest.get("source_fingerprint") or manifest.get("fingerprint")
    return str(value) if value else None


def _pdf_artifact_dir(
    *,
    output_path: Path,
    source_stem: str,
    source_fingerprint: str,
) -> Path:
    """Return a normalized artifact directory, reusing identical content when present."""
    index = 1
    while True:
        artifact_stem = source_stem if index == 1 else f"{source_stem}_{index}"
        artifact_dir = output_path / artifact_stem
        if not artifact_dir.exists() or _manifest_source_fingerprint(artifact_dir) == source_fingerprint:
            return artifact_dir
        index += 1


def pdf_artifact_work_paths(
    path: str | Path,
    *,
    root_dir: str | Path | None = None,
) -> dict[str, Path]:
    """Return root-owned PDF artifact work paths for a source PDF."""
    resolved_root_dir = resolve_root_dir(root_dir=root_dir)
    pdf_path = Path(path).expanduser()
    if not pdf_path.is_absolute():
        pdf_path = resolved_root_dir / pdf_path
    pdf_path = pdf_path.resolve()
    if not pdf_path.is_file():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    output_path = resolved_root_dir / DEFAULT_PDF_PREPARE_OUTPUT_DIR
    source_fingerprint = pdf_source_fingerprint(pdf_path)
    source_stem = normalize_source_stem(pdf_path.name)
    artifact_dir = output_path / source_stem
    if not artifact_dir.exists() or _manifest_source_fingerprint(artifact_dir) not in {None, source_fingerprint}:
        artifact_dir = _pdf_artifact_dir(
            output_path=output_path,
            source_stem=source_stem,
            source_fingerprint=source_fingerprint,
        )
    work_dir = artifact_dir / "work"
    tables_dir = work_dir / PDF_TABLES_DIR_NAME
    tables_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = artifact_dir / "manifest.json"
    if not manifest_path.exists():
        manifest = {
            "version": PDF_ARTIFACT_VERSION,
            "kind": "pdf_work",
            "status": "prepared",
            "created_at": datetime.now(UTC).isoformat(),
            "source_path": str(pdf_path),
            "source_filename": pdf_path.name,
            "normalized_filename": normalize_source_filename(pdf_path.name),
            "source_fingerprint": source_fingerprint,
            "artifact_dir": _relative_to_root(artifact_dir, resolved_root_dir),
            "work_dir": _relative_to_root(work_dir, resolved_root_dir),
            "tables_dir": _relative_to_root(tables_dir, resolved_root_dir),
            "tables_manifest_path": _relative_to_root(work_dir / PDF_TABLES_MANIFEST_NAME, resolved_root_dir),
        }
        manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return {
        "root_dir": resolved_root_dir,
        "pdf_path": pdf_path,
        "artifact_dir": artifact_dir,
        "work_dir": work_dir,
        "tables_dir": tables_dir,
        "tables_manifest_path": work_dir / PDF_TABLES_MANIFEST_NAME,
    }
