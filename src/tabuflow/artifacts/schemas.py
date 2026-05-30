"""Pydantic schemas for public artifact payloads."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

JsonObject = dict[str, Any]


class ArtifactCatalogPayload(BaseModel):
    """Base class for artifact catalog result payloads."""

    model_config = ConfigDict(extra="allow")

    status: Literal["ok", "error"] = Field(description="Catalog operation status.")
    database_path: str | None = Field(default=None, description="SQLite database path when known.")


class SqlArtifactListResult(ArtifactCatalogPayload):
    """Public response returned by artifact listing."""

    sql_artifact_count: int | None = Field(default=None, ge=0, description="Number of listed SQL artifacts.")
    sql_artifact_total_count: int | None = Field(default=None, ge=0, description="Total matching SQL artifact count before truncation.")
    sql_artifacts_truncated: bool | None = Field(default=None, description="Whether listed artifacts were truncated.")
    detail: str | None = Field(default=None, description="Listing detail level.")
    sql_artifacts: list[JsonObject] | None = Field(default=None, description="Listed SQL artifact payloads.")
    summary: str | None = Field(default=None, description="Compact listing summary.")


class SqlArtifactDescriptionResult(ArtifactCatalogPayload):
    """Public response returned by artifact description."""

    name: str | None = Field(default=None, description="SQL artifact name.")
    type: str | None = Field(default=None, description="SQLite artifact type.")
    kind: str | None = Field(default=None, description="Classified artifact kind.")
    row_count: int | None = Field(default=None, ge=0, description="Artifact row count when known.")
    column_count: int | None = Field(default=None, ge=0, description="Artifact column count.")
    columns: list[JsonObject] | None = Field(default=None, description="Artifact column metadata.")
    sample_rows: list[JsonObject] | None = Field(default=None, description="Bounded sample rows.")
    summary: str | None = Field(default=None, description="Compact artifact summary.")


class SqlArtifactSuggestionResult(ArtifactCatalogPayload):
    """Public response returned by artifact suggestions."""

    question: str | None = Field(default=None, description="Original natural-language question.")
    tokens: list[str] | None = Field(default=None, description="Search tokens used for suggestions.")
    suggestion_count: int | None = Field(default=None, ge=0, description="Number of returned suggestions.")
    suggestions: list[JsonObject] | None = Field(default=None, description="Suggested SQL artifact payloads.")
    summary: str | None = Field(default=None, description="Compact suggestion summary.")


class SourceArtifactLookupResult(ArtifactCatalogPayload):
    """Public response returned by source-to-artifact lookup."""

    source_path: str | None = Field(default=None, description="Requested source path.")
    source_format: str | None = Field(default=None, description="Requested source format.")
    artifact_count: int | None = Field(default=None, ge=0, description="Number of artifacts matched to the source.")
    preferred_artifact: JsonObject | None = Field(default=None, description="Preferred artifact for the source when available.")
    artifacts: list[JsonObject] | None = Field(default=None, description="Matched source artifact payloads.")
    artifacts_truncated: bool | None = Field(default=None, description="Whether matched artifacts were truncated.")
    summary: str | None = Field(default=None, description="Compact source artifact summary.")


class ArtifactMapResult(BaseModel):
    """Public response returned by artifact workspace mapping."""

    model_config = ConfigDict(extra="allow")

    status: Literal["ok", "error"] | None = Field(default=None, description="Catalog operation status.")
    database_path: str | None = Field(default=None, description="SQLite database path when known.")
    artifact_traces: list[JsonObject] | None = Field(default=None, description="Input file to table to SQL file to SQL result trace.")
    unlinked_files: JsonObject | None = Field(default=None, description="Managed artifact file paths not linked into the trace.")


def dump_sql_artifact_list_result(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate and return a JSON-shaped artifact list payload."""
    return SqlArtifactListResult.model_validate(payload).model_dump(mode="json", exclude_none=True)


def dump_sql_artifact_description_result(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate and return a JSON-shaped artifact description payload."""
    return SqlArtifactDescriptionResult.model_validate(payload).model_dump(mode="json", exclude_none=True)


def dump_sql_artifact_suggestion_result(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate and return a JSON-shaped artifact suggestion payload."""
    return SqlArtifactSuggestionResult.model_validate(payload).model_dump(mode="json", exclude_none=True)


def dump_source_artifact_lookup_result(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate and return a JSON-shaped source artifact lookup payload."""
    return SourceArtifactLookupResult.model_validate(payload).model_dump(mode="json", exclude_none=True)


def dump_artifact_map_result(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate and return a JSON-shaped artifact map payload."""
    return ArtifactMapResult.model_validate(payload).model_dump(mode="json", exclude_none=True)
