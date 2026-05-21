"""Artifact directory, naming, catalog, and query helpers."""

from __future__ import annotations

from .catalog import (
    DEFAULT_CLI_ARTIFACT_LIST_LIMIT,
    SQL_ARTIFACT_LIST_DETAILS,
    artifacts_from_source,
    describe_sql_artifact,
    list_sql_artifacts,
    suggest_sql_artifacts,
)
from .catalog_metadata import CatalogMetadataError, classify_sql_artifact
from .database import (
    query_artifacts,
    resolve_db_path,
    run_query,
    save_artifact_view,
    save_view,
)
from .naming import ArtifactNamerFn, build_sql_artifact_namer, name_sql_artifact
from .repair import (
    inspect_sql_artifact_schema,
    suggest_sql_error_repair,
    suggest_sql_error_repair_from_schema,
)

__all__ = [
    "DEFAULT_CLI_ARTIFACT_LIST_LIMIT",
    "SQL_ARTIFACT_LIST_DETAILS",
    "ArtifactNamerFn",
    "CatalogMetadataError",
    "artifacts_from_source",
    "build_sql_artifact_namer",
    "classify_sql_artifact",
    "describe_sql_artifact",
    "inspect_sql_artifact_schema",
    "list_sql_artifacts",
    "name_sql_artifact",
    "query_artifacts",
    "resolve_db_path",
    "run_query",
    "save_artifact_view",
    "save_view",
    "suggest_sql_artifacts",
    "suggest_sql_error_repair",
    "suggest_sql_error_repair_from_schema",
]
