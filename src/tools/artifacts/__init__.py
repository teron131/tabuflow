"""Artifact directory, naming, catalog, and query helpers."""

from __future__ import annotations

from typing import Any

__all__ = [
    "ArtifactNamerFn",
    "CatalogMetadataError",
    "artifacts_from_source",
    "build_sql_artifact_namer",
    "classify_sql_artifact",
    "describe_artifact",
    "describe_sql_artifact",
    "inspect_sql_artifact_schema",
    "list_artifacts",
    "list_sql_artifacts",
    "name_sql_artifact",
    "query_artifacts",
    "resolve_db_path",
    "run_query",
    "save_artifact_view",
    "save_view",
    "suggest_sql_error_repair",
    "suggest_sql_error_repair_from_schema",
]

_EXPORT_MODULES = {
    "ArtifactNamerFn": ".naming",
    "artifacts_from_source": ".catalog",
    "CatalogMetadataError": ".catalog",
    "build_sql_artifact_namer": ".naming",
    "classify_sql_artifact": ".catalog",
    "describe_artifact": ".catalog",
    "describe_sql_artifact": ".catalog",
    "inspect_sql_artifact_schema": ".repair",
    "list_artifacts": ".catalog",
    "list_sql_artifacts": ".catalog",
    "name_sql_artifact": ".naming",
    "query_artifacts": ".database",
    "resolve_db_path": ".database",
    "run_query": ".database",
    "save_artifact_view": ".database",
    "save_view": ".database",
    "suggest_sql_error_repair": ".repair",
    "suggest_sql_error_repair_from_schema": ".repair",
}


def __getattr__(name: str) -> Any:
    """Load artifact helpers lazily so submodules can import each other safely."""
    module_name = _EXPORT_MODULES.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    from importlib import import_module

    module = import_module(module_name, __name__)
    value = getattr(module, name)
    globals()[name] = value
    return value
