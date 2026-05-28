"""Pydantic schemas for public PDF payload contracts."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

PdfExtractionStatus = Literal["ok", "empty", "low_confidence"]
PdfManifestStatus = Literal["ok", "empty"]


class PdfPayload(BaseModel):
    """Base class for strict public PDF payload schemas."""

    model_config = ConfigDict(extra="forbid")


class PdfSourcePayload(PdfPayload):
    """Base payload for responses tied to one source PDF."""

    path: str = Field(description="Resolved source PDF path.")


class PdfWorkspacePayload(PdfSourcePayload):
    """Base payload for responses that own PDF artifact workspace paths."""

    artifact_dir: str = Field(description="Root artifact directory for this source PDF.")
    work_dir: str = Field(description="Working directory for generated PDF artifacts.")


class FlexiblePdfPayload(PdfPayload):
    """Base class for payload sections that preserve source-specific extra evidence."""

    model_config = ConfigDict(extra="allow")


JsonObject = dict[str, Any]


class PdfPageNumberPayload(PdfPayload):
    """Base payload for one-indexed PDF page evidence."""

    page_number: int = Field(ge=1, description="One-indexed PDF page number.")


class PdfOverviewBatch(PdfPayload):
    """Rendered overview image for a batch of PDF pages."""

    batch: int = Field(ge=1, description="One-indexed overview batch number.")
    pages: list[int] = Field(description="One-indexed pages included in this overview image.")
    sample_pages: list[int] = Field(description="Representative visual sample pages included in this batch.")
    image_path: str = Field(description="Rendered overview image path.")


class PdfOverviewBatchIndex(PdfPayload):
    """Index entry for one rendered overview batch."""

    batch: int = Field(ge=1, description="One-indexed overview batch number.")
    pages: list[int] = Field(description="One-indexed pages included in this overview image.")
    selected: bool = Field(description="Whether this batch is included in the compact overview output.")


class PdfInspectionProfile(PdfPayload):
    """Compact document-wide profile returned by PDF inspection."""

    visual_samples: JsonObject = Field(description="Representative page samples selected from the document profile.")
    layout_signatures: list[JsonObject] = Field(description="Coarse layout signatures found across the PDF.")


class PdfTableDetections(FlexiblePdfPayload):
    """Bounded table detector output for one inspected page."""

    detection_count: int = Field(ge=0, description="Number of PyMuPDF table detections before truncation.")
    detections: list[JsonObject] = Field(description="Bounded table detection candidates and interpretation evidence.")
    truncated: bool = Field(description="Whether additional table detections were omitted.")


class PdfRowGeometry(FlexiblePdfPayload):
    """Bounded text-row geometry for one inspected page."""

    row_count: int = Field(ge=0, description="Number of visual text rows before truncation.")
    rows: list[JsonObject] = Field(description="Bounded visual text rows with coordinates and text.")
    truncated: bool = Field(description="Whether additional visual text rows were omitted.")


class PdfInspectionPage(PdfPageNumberPayload):
    """Focused inspection payload for one PDF page."""

    table_detections: PdfTableDetections = Field(description="PyMuPDF table detection evidence for this page.")
    row_geometry: PdfRowGeometry = Field(description="Visual text row geometry for table planning.")
    text: str = Field(description="Bounded raw page text.")
    text_truncated: bool = Field(description="Whether raw page text was truncated.")


class PdfInspectionResult(PdfSourcePayload):
    """Public response returned by PDF inspection."""

    page_count: int = Field(ge=0, description="Total page count in the source PDF.")
    overview_batches: list[PdfOverviewBatch] = Field(description="Selected overview batches for representative visual inspection.")
    overview_batch_index: list[PdfOverviewBatchIndex] = Field(description="All overview batches with selected markers.")
    profile: PdfInspectionProfile = Field(description="Compact document-wide profile.")
    page_start: int | None = Field(default=None, ge=1, description="First focused page included in this result.")
    page_end: int | None = Field(default=None, ge=1, description="Last focused page included in this result.")
    table_region_hints: JsonObject | None = Field(default=None, description="Suggested extraction regions and methods grouped from inspection evidence.")
    pages: list[PdfInspectionPage] | None = Field(default=None, description="Focused page-level inspection payloads.")


class PdfExtractionDiagnostics(FlexiblePdfPayload):
    """Diagnostic details for a PDF extraction run."""

    warnings: list[str] = Field(default_factory=list, description="Machine-readable warnings for empty or low-confidence extraction output.")


class PdfTableFilePayload(PdfPayload):
    """Base payload for one generated PDF table CSV."""

    document_order: int = Field(ge=1, description="One-indexed table order in source document order.")
    name: str = Field(description="Stable table name derived from page range and collision handling.")
    page_tag: str = Field(description="Compact page-range tag used in generated filenames.")
    path: str = Field(description="Generated table CSV path.")


class PdfExtractionTable(PdfTableFilePayload):
    """Manifest entry for one extracted PDF table."""

    model_config = ConfigDict(extra="allow")

    mode: str = Field(description="Extraction mode that produced this table.")
    row_count: int = Field(ge=0, description="Number of extracted data rows.")
    columns: list[str] = Field(description="Output CSV columns.")


class PdfExtractionManifest(PdfWorkspacePayload):
    """Manifest persisted for a PDF table extraction run."""

    status: PdfManifestStatus = Field(description="Compatibility status: empty when no tables were extracted, otherwise ok.")
    extraction_status: PdfExtractionStatus = Field(description="Detailed extraction status for empty or low-confidence outputs.")
    extraction: JsonObject = Field(description="Extraction configuration used for this run.")
    output_dir: str = Field(description="Directory containing generated table CSV files.")
    tables: list[PdfExtractionTable] = Field(description="Generated table manifest entries.")
    diagnostics: PdfExtractionDiagnostics = Field(description="Extraction diagnostics and warning details.")


class PdfExtractionResult(PdfExtractionManifest):
    """Public response returned by PDF extraction."""

    manifest_path: str = Field(description="Path to the persisted PDF extraction manifest.")


def dump_pdf_inspection_result(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate and return a JSON-shaped PDF inspection payload."""
    return PdfInspectionResult.model_validate(payload).model_dump(mode="json", exclude_none=True)


def dump_pdf_extraction_manifest(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate and return a JSON-shaped PDF extraction manifest."""
    return PdfExtractionManifest.model_validate(payload).model_dump(mode="json")


def dump_pdf_extraction_result(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate and return a JSON-shaped PDF extraction response."""
    return PdfExtractionResult.model_validate(payload).model_dump(mode="json")
