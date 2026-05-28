"""Pydantic schemas for public tabular tool payloads."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

JsonObject = dict[str, Any]


class TabularPayload(BaseModel):
    """Base class for public tabular payload schemas."""

    model_config = ConfigDict(extra="allow")


class TabularSourcePayload(TabularPayload):
    """Base payload tied to one tabular source file."""

    path: str = Field(description="Source tabular file path.")
    format: str = Field(description="Tabular source format.")


class TabularGridPayload(TabularPayload):
    """Base payload for one sheet-like tabular grid."""

    grid_name: str = Field(description="Stable grid name; empty for CSV or unnamed single-grid sources.")
    sheet_name: str | None = Field(default=None, description="Workbook sheet name when available.")


class TabularSourceGridPayload(
    TabularGridPayload,
    TabularSourcePayload,
):
    """Base payload tied to a source file and one sheet-like grid."""


class TabularProfileGrid(TabularGridPayload):
    """Profile payload for one tabular grid."""


class TabularProfileSource(TabularSourcePayload):
    """Profile payload for a tabular source and its grids."""

    grid_count: int = Field(ge=0, description="Number of profiled grids.")
    grid_names: list[str] = Field(description="Profiled grid names in source order.")
    grids: list[TabularProfileGrid] = Field(description="Profile payloads for each grid.")


class TabularInspectionResult(TabularSourceGridPayload):
    """Public response returned by tabular inspection."""

    preview_row_count: int = Field(ge=0, description="Number of preview rows returned.")
    preview_column_count: int = Field(ge=0, description="Number of preview columns returned.")
    start_row: int = Field(ge=1, description="One-indexed start row for the returned window.")
    end_row: int = Field(ge=0, description="One-indexed end row for the returned window.")
    start_col: int = Field(ge=1, description="One-indexed start column for the returned window.")
    end_col: int = Field(ge=0, description="One-indexed end column for the returned window.")
    structure_hints: JsonObject = Field(description="Deterministic structure hints for extraction planning.")
    header_candidates: list[JsonObject] = Field(description="Detected header candidate rows.")
    regions: list[JsonObject] = Field(description="Detected structural regions.")
    rows: list[list[str]] = Field(description="Bounded raw grid rows.")
    formula_count: int = Field(ge=0, description="Number of formula cells in the returned window.")
    formulas: list[JsonObject] = Field(description="Formula metadata in the returned window.")


class TabularExtractionGrid(TabularGridPayload):
    """Extraction payload for one tabular grid."""

    status: Literal["loaded", "empty"] = Field(description="Extraction load status.")
    artifact_backend: str = Field(description="Artifact storage backend.")
    database_path: str = Field(description="SQLite database path when tables were loaded.")
    recovered_table_count: int = Field(ge=0, description="Number of recovered importable tables.")
    excluded_row_hints: list[JsonObject] = Field(description="Rows excluded from table import but relevant for review.")
    tables: list[JsonObject] = Field(description="Loaded table metadata.")
    formula_count: int = Field(ge=0, description="Number of formula cells found in the grid.")
    formulas: list[JsonObject] = Field(description="Formula metadata and table references.")


class TabularExtractionSource(TabularSourcePayload):
    """Extraction payload for a tabular source and its grids."""

    status: Literal["loaded", "empty"] = Field(description="Source-level extraction status.")
    artifact_backend: str = Field(description="Artifact storage backend.")
    database_path: str = Field(description="SQLite database path when any grid loaded tables.")
    grid_count: int = Field(ge=0, description="Number of extracted grids.")
    grid_names: list[str] = Field(description="Extracted grid names in source order.")
    recovered_table_count: int = Field(ge=0, description="Total recovered importable table count.")
    formula_count: int = Field(ge=0, description="Total formula count across extracted grids.")
    grids: list[TabularExtractionGrid] = Field(description="Extraction payloads for each grid.")


def tabular_grid_name(payload: dict[str, Any]) -> str:
    """Return the stable grid name for one tabular payload."""
    return str(payload.get("sheet_name") or payload.get("grid_name") or "")


def dump_tabular_profile_grid(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate and return one JSON-shaped profile grid payload."""
    grid_payload = {
        **payload,
        "grid_name": tabular_grid_name(payload),
        "sheet_name": payload.get("sheet_name"),
    }
    return TabularProfileGrid.model_validate(grid_payload).model_dump(mode="json")


def dump_tabular_profile_source(
    path: str | Path,
    *,
    format_name: str,
    grids: list[dict[str, Any]],
) -> dict[str, Any]:
    """Validate and return one JSON-shaped profile source payload."""
    grid_payloads = [dump_tabular_profile_grid(grid) for grid in grids]
    payload = {
        "path": str(path),
        "format": format_name,
        "grid_count": len(grid_payloads),
        "grid_names": [tabular_grid_name(grid) for grid in grid_payloads],
        "grids": grid_payloads,
    }
    return TabularProfileSource.model_validate(payload).model_dump(mode="json")


def dump_tabular_extraction_grid(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate and return one JSON-shaped extraction grid payload."""
    grid_payload = {
        **payload,
        "grid_name": tabular_grid_name(payload),
        "sheet_name": payload.get("sheet_name"),
    }
    return TabularExtractionGrid.model_validate(grid_payload).model_dump(mode="json")


def dump_tabular_extraction_source(
    path: str | Path,
    *,
    format_name: str,
    grids: list[dict[str, Any]],
) -> dict[str, Any]:
    """Validate and return one JSON-shaped extraction source payload."""
    grid_payloads = [dump_tabular_extraction_grid(grid) for grid in grids]
    recovered_table_count = sum(int(grid.get("recovered_table_count", 0)) for grid in grid_payloads)
    formula_count = sum(int(grid.get("formula_count", 0)) for grid in grid_payloads)
    payload = {
        "path": str(path),
        "format": format_name,
        "status": "loaded" if recovered_table_count else "empty",
        "artifact_backend": "sqlite",
        "database_path": next((str(grid.get("database_path") or "") for grid in grid_payloads if grid.get("database_path")), ""),
        "grid_count": len(grid_payloads),
        "grid_names": [tabular_grid_name(grid) for grid in grid_payloads],
        "recovered_table_count": recovered_table_count,
        "formula_count": formula_count,
        "grids": grid_payloads,
    }
    return TabularExtractionSource.model_validate(payload).model_dump(mode="json")


def dump_tabular_inspection_result(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate and return a JSON-shaped tabular inspection payload."""
    normalized = {
        **payload,
        "grid_name": tabular_grid_name(payload),
        "sheet_name": payload.get("sheet_name"),
    }
    return TabularInspectionResult.model_validate(normalized).model_dump(mode="json")


def dump_tabular_profile_result(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate and return a JSON-shaped tabular profile payload."""
    if "grids" in payload:
        return TabularProfileSource.model_validate(payload).model_dump(mode="json")
    return dump_tabular_profile_grid(payload)


def dump_tabular_extraction_result(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate and return a JSON-shaped tabular extraction payload."""
    if "grids" in payload:
        return TabularExtractionSource.model_validate(payload).model_dump(mode="json")
    return dump_tabular_extraction_grid(payload)
