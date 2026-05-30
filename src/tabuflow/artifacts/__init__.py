"""Artifact directory, naming, catalog, and query helpers."""

from __future__ import annotations

from .catalog import (
    DEFAULT_CLI_ARTIFACT_LIST_LIMIT,
    SQL_ARTIFACT_LIST_DETAILS,
    CatalogMetadataError,
    artifacts_from_source,
    classify_sql_artifact,
    describe_sql_artifact,
    list_sql_artifacts,
    suggest_sql_artifacts,
)
from .database import (
    resolve_db_path,
    run_query,
)
from .map import format_artifact_map, map_artifacts
from .naming import ArtifactNamerFn, build_sql_artifact_namer, name_sql_artifact, normalize_source_filename, normalize_source_stem
from .repair import (
    inspect_sql_artifact_schema,
    suggest_sql_error_repair,
    suggest_sql_error_repair_from_schema,
)
from .views import save_view

__all__ = [
    "DEFAULT_CLI_ARTIFACT_LIST_LIMIT",
    "SQL_ARTIFACT_LIST_DETAILS",
    "ArtifactNamerFn",
    "CatalogMetadataError",
    "artifacts_from_source",
    "build_sql_artifact_namer",
    "classify_sql_artifact",
    "describe_sql_artifact",
    "format_artifact_map",
    "inspect_sql_artifact_schema",
    "list_sql_artifacts",
    "map_artifacts",
    "name_sql_artifact",
    "normalize_source_filename",
    "normalize_source_stem",
    "resolve_db_path",
    "run_query",
    "save_view",
    "suggest_sql_artifacts",
    "suggest_sql_error_repair",
    "suggest_sql_error_repair_from_schema",
]
