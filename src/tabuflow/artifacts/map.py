"""Artifact workspace trace map."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import sqlite3
from typing import Any

from ..workspace_db import artifact_workspace
from .catalog import CatalogMetadataError, database_catalog
from .catalog.metadata import DatabaseCatalog, SqlArtifactInfo, path_match_reason
from .database import error_result
from .relationships import referenced_artifact_names
from .schemas import dump_artifact_map_result


@dataclass(frozen=True)
class ArtifactMapFiles:
    """Managed artifact files that can participate in a trace map."""

    sql_files: list[str]
    output_files: list[str]
    pdf_workspaces: list[str]


def _compact_path(path_text: str, *, workspace_dir: Path) -> str:
    """Return a cwd-relative path when the path lives inside the workspace."""
    path = Path(path_text).expanduser()
    if not path.is_absolute():
        return path_text
    try:
        return str(path.resolve().relative_to(workspace_dir))
    except ValueError:
        return str(path)


def _map_diagnostic(
    *,
    kind: str,
    path: str,
    message: str,
) -> dict[str, str]:
    """Return one non-fatal map diagnostic."""
    return {
        "kind": kind,
        "path": path,
        "message": message,
    }


def _artifact_files() -> ArtifactMapFiles:
    """Return managed SQL, output, and PDF workspace paths."""
    workspace = artifact_workspace()
    sql_files: list[str] = []
    output_files: list[str] = []
    pdf_workspaces: list[str] = []

    if workspace.sql_dir.exists():
        for path in sorted(workspace.sql_dir.rglob("*.sql")):
            relative_path = path.relative_to(workspace.sql_dir)
            if path.is_file() and not any(part.startswith(".") or part == "__pycache__" for part in relative_path.parts):
                sql_files.append(workspace.relative_path(path))

    if workspace.outputs_dir.exists():
        for path in sorted(workspace.outputs_dir.rglob("*")):
            relative_path = path.relative_to(workspace.outputs_dir)
            if path.is_file() and not any(part.startswith(".") or part == "__pycache__" for part in relative_path.parts):
                output_files.append(workspace.relative_path(path))

    if workspace.pdf_dir.exists():
        for path in sorted(workspace.pdf_dir.iterdir()):
            relative_path = path.relative_to(workspace.pdf_dir)
            if path.is_dir() and not any(part.startswith(".") or part == "__pycache__" for part in relative_path.parts):
                pdf_workspaces.append(workspace.relative_path(path))

    return ArtifactMapFiles(
        sql_files=sql_files,
        output_files=output_files,
        pdf_workspaces=pdf_workspaces,
    )


def _sql_references(
    sql_paths: list[str],
    *,
    catalog: DatabaseCatalog | None,
    include_internal: bool,
) -> tuple[
    dict[str, list[str]],
    list[dict[str, str]],
]:
    """Return SQL file paths keyed by referenced table/view name."""
    if catalog is None:
        return {}, []

    workspace = artifact_workspace()
    visible_names = {artifact.name for artifact in catalog.visible_sql_artifacts(include_internal=include_internal)}
    sql_by_table: dict[str, list[str]] = {}
    diagnostics: list[dict[str, str]] = []
    for sql_path in sql_paths:
        try:
            sql = (workspace.workspace_dir / sql_path).read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            diagnostics.append(
                _map_diagnostic(
                    kind="unreadable_sql_file",
                    path=sql_path,
                    message=str(exc),
                )
            )
            continue
        for table_name in referenced_artifact_names(
            sql,
            available_artifact_names=visible_names,
            current_artifact_name="",
        ):
            sql_by_table.setdefault(table_name, []).append(sql_path)
    return sql_by_table, diagnostics


def _results_by_sql(
    sql_paths: list[str],
    output_paths: list[str],
) -> dict[str, list[str]]:
    """Link output files to SQL files by matching managed artifact stems."""
    outputs_by_stem: dict[str, list[str]] = {}
    for output_path in output_paths:
        outputs_by_stem.setdefault(Path(output_path).stem, []).append(output_path)
    return {sql_path: outputs_by_stem.get(Path(sql_path).stem, []) for sql_path in sql_paths}


def _pdf_workspaces_by_source(
    pdf_workspace_paths: list[str],
) -> tuple[
    dict[str, list[str]],
    list[dict[str, str]],
]:
    """Return PDF workspace paths keyed by manifest source path."""
    workspace = artifact_workspace()
    workspaces_by_source: dict[str, list[str]] = {}
    diagnostics: list[dict[str, str]] = []
    for pdf_workspace_path in pdf_workspace_paths:
        manifest_path = workspace.workspace_dir / pdf_workspace_path / "manifest.json"
        if not manifest_path.is_file():
            continue
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            diagnostics.append(
                _map_diagnostic(
                    kind="unreadable_pdf_manifest",
                    path=str(manifest_path.relative_to(workspace.workspace_dir)),
                    message=str(exc),
                )
            )
            continue
        source_path = str(manifest.get("source_path") or "").strip()
        if source_path:
            workspaces_by_source.setdefault(source_path, []).append(pdf_workspace_path)
    return workspaces_by_source, diagnostics


def _preferred_sql_artifacts(catalog_artifacts: list[SqlArtifactInfo]) -> list[tuple[SqlArtifactInfo, SqlArtifactInfo | None]]:
    """Return default map artifacts, preferring typed views over their raw tables."""
    artifacts_by_name = {artifact.name: artifact for artifact in catalog_artifacts}
    typed_raw_names = {artifact.name.removesuffix("_typed") for artifact in catalog_artifacts if artifact.kind == "typed_content_view"}
    preferred: list[tuple[SqlArtifactInfo, SqlArtifactInfo | None]] = []
    for artifact in catalog_artifacts:
        if artifact.kind == "raw_content_table" and artifact.name in typed_raw_names:
            continue
        raw_table = None
        if artifact.kind == "typed_content_view":
            raw_table = artifacts_by_name.get(artifact.name.removesuffix("_typed"))
        preferred.append((artifact, raw_table))
    return preferred


def _source_pdf_workspaces(
    source_path: str,
    *,
    workspace_dir: Path,
    pdf_workspaces_by_source: dict[str, list[str]],
) -> list[str]:
    """Return PDF workspaces that point to one source path."""
    compact_source_path = _compact_path(source_path, workspace_dir=workspace_dir)
    matched: list[str] = []
    for pdf_source_path, workspace_paths in pdf_workspaces_by_source.items():
        if path_match_reason(pdf_source_path, source_path) or path_match_reason(pdf_source_path, compact_source_path):
            matched.extend(workspace_paths)
    return list(dict.fromkeys(matched))


def _sql_nodes(
    table_names: list[str],
    *,
    sql_by_table: dict[str, list[str]],
    results_by_sql: dict[str, list[str]],
) -> list[dict[str, Any]]:
    """Return SQL nodes for one table/raw-table pair."""
    sql_paths: list[str] = []
    for table_name in table_names:
        sql_paths.extend(sql_by_table.get(table_name, []))
    nodes = []
    for sql_path in dict.fromkeys(sql_paths):
        node: dict[str, Any] = {"path": sql_path}
        results = results_by_sql.get(sql_path, [])
        if results:
            node["sql_results"] = results
        nodes.append(node)
    return nodes


def _trace_tree(
    catalog: DatabaseCatalog | None,
    *,
    database_path: str,
    include_internal: bool,
    sql_by_table: dict[str, list[str]],
    results_by_sql: dict[str, list[str]],
    pdf_workspaces_by_source: dict[str, list[str]],
) -> list[dict[str, Any]]:
    """Return input -> table -> SQL -> result trace nodes."""
    if catalog is None:
        return []

    workspace = artifact_workspace()
    trace_by_input: dict[str, dict[str, Any]] = {}
    for artifact, raw_table in _preferred_sql_artifacts(catalog.visible_sql_artifacts(include_internal=include_internal)):
        for mapping in artifact.source_mappings:
            source_path = str(mapping.get("source_path") or "").strip()
            if not source_path:
                continue
            input_path = _compact_path(source_path, workspace_dir=workspace.workspace_dir)
            source_node = trace_by_input.setdefault(
                input_path,
                {
                    "input_file": input_path,
                    "extracted_tables": [],
                },
            )
            pdf_workspaces = _source_pdf_workspaces(
                source_path,
                workspace_dir=workspace.workspace_dir,
                pdf_workspaces_by_source=pdf_workspaces_by_source,
            )
            if pdf_workspaces:
                source_node["pdf_workspaces"] = pdf_workspaces

            table_names = [artifact.name]
            if raw_table is not None:
                table_names.append(raw_table.name)
            source_node["extracted_tables"].append(
                {
                    "sqlite_database": database_path,
                    "table_name": artifact.name,
                    "sql_files": _sql_nodes(
                        table_names,
                        sql_by_table=sql_by_table,
                        results_by_sql=results_by_sql,
                    ),
                }
            )

    return sorted(trace_by_input.values(), key=lambda node: str(node["input_file"]))


def _linked_paths(trace: list[dict[str, Any]]) -> tuple[set[str], set[str], set[str]]:
    """Return linked SQL, output, and PDF workspace paths from the trace."""
    linked_sql: set[str] = set()
    linked_outputs: set[str] = set()
    linked_pdf_workspaces: set[str] = set()
    for source_node in trace:
        linked_pdf_workspaces.update(source_node.get("pdf_workspaces", []))
        for table_node in source_node.get("extracted_tables", []):
            for sql_node in table_node.get("sql_files", []):
                linked_sql.add(sql_node["path"])
                linked_outputs.update(sql_node.get("sql_results", []))
    return linked_sql, linked_outputs, linked_pdf_workspaces


def format_artifact_map(payload: dict[str, Any]) -> str:
    """Return a compact readable input -> table -> SQL -> result trace."""
    lines: list[str] = []
    for source_node in payload.get("artifact_traces") or []:
        lines.append(f"FROM input_file {source_node['input_file']}")
        for pdf_workspace in source_node.get("pdf_workspaces", []):
            lines.append(f"  INSIDE pdf_workspace {pdf_workspace}")
        for table_node in source_node.get("extracted_tables", []):
            table_name = table_node["table_name"]
            sqlite_database = table_node.get("sqlite_database")
            table_ref = f"{sqlite_database}:{table_name}" if sqlite_database else table_name
            lines.append(f"  EXTRACTED table {table_ref}")
            for sql_node in table_node.get("sql_files", []):
                lines.append(f"    APPLIED sql_file {sql_node['path']}")
                for sql_result in sql_node.get("sql_results", []):
                    lines.append(f"      DERIVED result_file {sql_result}")
        lines.append("")

    unlinked_files = payload.get("unlinked_files") or {}
    unlinked_lines: list[str] = []
    for key, label in (
        ("pdf_workspaces", "pdf_workspace"),
        ("sql_files", "sql_file"),
        ("sql_results", "result_file"),
    ):
        unlinked_lines.extend(f"  {label} {path}" for path in unlinked_files.get(key, []))
    if unlinked_lines:
        lines.append("UNLINKED")
        lines.extend(unlinked_lines)

    diagnostics = payload.get("diagnostics") or []
    if diagnostics:
        if lines:
            lines.append("")
        lines.append("DIAGNOSTICS")
        for diagnostic in diagnostics:
            lines.append(f"  {diagnostic.get('kind', 'warning')} {diagnostic.get('path', '')}: {diagnostic.get('message', '')}")

    return "\n".join(lines).rstrip() or "no_artifact_traces"


def map_artifacts(
    *,
    include_internal: bool = False,
) -> dict[str, Any]:
    """Return a compact input -> table -> SQL -> result artifact trace."""
    workspace = artifact_workspace()
    database_path = workspace.tabular_database_path
    try:
        catalog = database_catalog(database_path) if database_path.exists() else None
        artifact_files = _artifact_files()
        sql_by_table, sql_diagnostics = _sql_references(
            artifact_files.sql_files,
            catalog=catalog,
            include_internal=include_internal,
        )
        results_by_sql = _results_by_sql(artifact_files.sql_files, artifact_files.output_files)
        pdf_workspaces_by_source, pdf_diagnostics = _pdf_workspaces_by_source(artifact_files.pdf_workspaces)
        trace = _trace_tree(
            catalog,
            database_path=workspace.relative_path(database_path),
            include_internal=include_internal,
            sql_by_table=sql_by_table,
            results_by_sql=results_by_sql,
            pdf_workspaces_by_source=pdf_workspaces_by_source,
        )
        linked_sql, linked_outputs, linked_pdf_workspaces = _linked_paths(trace)
        return dump_artifact_map_result(
            {
                "status": "ok",
                "database_path": workspace.relative_path(database_path),
                "artifact_traces": trace,
                "unlinked_files": {
                    "sql_files": [path for path in artifact_files.sql_files if path not in linked_sql],
                    "sql_results": [path for path in artifact_files.output_files if path not in linked_outputs],
                    "pdf_workspaces": [path for path in artifact_files.pdf_workspaces if path not in linked_pdf_workspaces],
                },
                "diagnostics": sql_diagnostics + pdf_diagnostics,
            }
        )
    except ValueError as exc:
        return dump_artifact_map_result(
            error_result(
                database_path=database_path,
                error_type="invalid_artifact_map_request",
                message=str(exc),
            )
        )
    except CatalogMetadataError as exc:
        return dump_artifact_map_result(
            error_result(
                database_path=database_path,
                error_type="catalog_metadata_error",
                message=str(exc),
            )
        )
    except (sqlite3.Error, sqlite3.Warning) as exc:
        return dump_artifact_map_result(
            error_result(
                database_path=database_path,
                error_type="sql_execution_error",
                message=str(exc),
            )
        )
