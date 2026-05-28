"""HTTP routes for the Tabuflow workbench API."""

import csv
from dataclasses import asdict
from datetime import UTC, datetime
import io
import json
from pathlib import Path
import re
import shutil
import sqlite3
from typing import Any

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse

from tabuflow.artifacts import describe_sql_artifact, list_sql_artifacts, run_query
from tabuflow.tabular import extract_tabular_source, inspect_tabular_file
from tabuflow.tabular.storage import quote_identifier

from ..config import (
    MISSING_LLM_CONFIG_MESSAGE,
    REPO_ROOT,
    SKILLS_DIR,
    UPLOADS_DIR,
    WORKBENCH_SOURCE_ROOT,
    has_llm_environment,
    llm_settings_payload,
    resolve_agent_model,
    update_llm_settings,
)
from ..pipelines.explainer import MissingExplainerModelError, explain_file
from ..tools.skills import create_skill_package, list_skills, load_skill
from .chat import ChatConfigurationError, ChatRuntimeError, run_chat, stream_chat_chunks
from .constants import (
    DEFAULT_SQL,
    IMAGE_UPLOAD_EXTENSIONS,
    STAGE_CARDS,
    SUGGESTED_QUESTIONS,
    TABULAR_UPLOAD_EXTENSIONS,
    UPLOAD_EXTENSIONS,
)
from .schemas import (
    ChatRequest,
    FileExplanationRequest,
    LlmSettings,
    SkillCreateRequest,
    SkillResourceSaveRequest,
    SkillSaveRequest,
    SourcePreviewRequest,
    SqlRunRequest,
)
from .workspace_data import (
    WorkspaceDataMissingError,
    default_database_path,
    list_loaded_source_summaries,
    list_uploaded_source_summaries,
    resolve_database_path,
)

router = APIRouter(prefix="/api")
PRIVATE_SQL_TOKENS = ("_tabular_", "source_path")
PDF_PREVIEW_PAGE_LIMIT = 3
DEFAULT_IMAGE_EXTENSION_BY_TYPE = {
    "image/gif": ".gif",
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}
SOURCE_MAPPING_PRIVATE_KEYS = {
    "database_path",
    "source_mappings",
    "source_path_preview",
    "source_paths",
    "source_paths_truncated",
}
MAX_SOURCE_PREVIEW_COLUMNS = 512


def _workspace_data_error(exc: WorkspaceDataMissingError) -> HTTPException:
    """Return an explicit missing-data HTTP error."""
    return HTTPException(
        status_code=503,
        detail={
            "status": "error",
            "mode": "missing_workspace_data",
            "message": str(exc),
        },
    )


def _chat_configuration_error(message: str = MISSING_LLM_CONFIG_MESSAGE) -> HTTPException:
    """Return the shared model-configuration HTTP error."""
    return HTTPException(
        status_code=503,
        detail={
            "status": "error",
            "mode": "missing_llm_config",
            "message": message,
            "llm_configured": False,
        },
    )


def _chat_runtime_error(message: str) -> HTTPException:
    """Return the shared model runtime HTTP error."""
    return HTTPException(
        status_code=502,
        detail={
            "status": "error",
            "mode": "model_error",
            "message": message,
            "llm_configured": True,
        },
    )


def _public_sql_artifact(artifact: dict[str, Any]) -> dict[str, Any]:
    """Remove private source path metadata from a SQL artifact payload."""
    source_references = _public_source_references(artifact.get("source_mappings"))
    public_artifact = {key: value for key, value in artifact.items() if key not in SOURCE_MAPPING_PRIVATE_KEYS}
    public_artifact["source_references"] = source_references
    public_artifact["source_file_names"] = list(dict.fromkeys(reference["name"] for reference in source_references))
    public_artifact["schema_profile"] = _sql_artifact_schema_profile(public_artifact)
    return public_artifact


def _sql_artifact_schema_profile(artifact: dict[str, Any]) -> dict[str, Any]:
    """Return a compact source/target schema profile for browser and model use."""
    columns = artifact.get("columns")
    if not isinstance(columns, list):
        columns = artifact.get("column_preview") if isinstance(artifact.get("column_preview"), list) else []
    sample_rows = artifact.get("sample_rows") if isinstance(artifact.get("sample_rows"), list) else []
    source_references = artifact.get("source_references") if isinstance(artifact.get("source_references"), list) else []
    warnings: list[str] = []
    if artifact.get("columns_truncated"):
        warnings.append("Column list is truncated in this summary.")
    if artifact.get("source_paths_truncated"):
        warnings.append("Source lineage is truncated in this summary.")
    if artifact.get("status") == "error":
        warnings.append(str(artifact.get("message") or "Schema profile could not be loaded."))

    return {
        "target_name": str(artifact.get("name") or artifact.get("sql_artifact_name") or ""),
        "target_kind": str(artifact.get("kind") or ""),
        "object_type": str(artifact.get("type") or ""),
        "row_count": artifact.get("row_count"),
        "column_count": artifact.get("column_count"),
        "size_label": artifact.get("size_label"),
        "columns": columns,
        "sample_rows": sample_rows,
        "source_references": source_references,
        "warnings": warnings,
    }


def _public_source_references(source_mappings: Any) -> list[dict[str, str]]:
    """Return browser-safe source lineage without exposing raw absolute paths."""
    if not isinstance(source_mappings, list):
        return []
    references: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for source_mapping in source_mappings:
        if not isinstance(source_mapping, dict):
            continue
        raw_source_path = str(source_mapping.get("source_path") or "").strip()
        if not raw_source_path:
            continue
        public_path = _public_source_path(raw_source_path)
        source_table_name = str(source_mapping.get("source_table_name") or "")
        source_sheet_name = str(source_mapping.get("source_sheet_name") or "")
        key = (public_path, source_sheet_name, source_table_name)
        if key in seen:
            continue
        seen.add(key)
        references.append(
            {
                "name": Path(public_path).name or public_path,
                "path": public_path,
                "format": str(source_mapping.get("source_format") or ""),
                "sheet_name": source_sheet_name,
                "table_name": source_table_name,
            }
        )
    return references


def _public_source_path(source_path: str) -> str:
    """Return a stable source path suitable for the browser UI."""
    path = Path(source_path).expanduser()
    if not path.is_absolute():
        return source_path
    try:
        return str(path.resolve().relative_to(REPO_ROOT))
    except ValueError:
        return path.name or str(path)


def _public_sql_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Remove private local path metadata from SQL tool payloads."""
    result = {key: value for key, value in payload.items() if key != "database_path"}
    if "sql_artifact_count" in result:
        result["sql_artifact_count"] = result.pop("sql_artifact_count")
    if isinstance(result.get("sql_artifacts"), list):
        public_artifacts = [
            _public_sql_artifact(sql_artifact)
            for sql_artifact in result.pop("sql_artifacts")
            if isinstance(sql_artifact, dict) and sql_artifact.get("kind") != "typed_content_view"
        ]
        result["sql_artifacts"] = public_artifacts
        result["sql_artifact_count"] = len(public_artifacts)
        result["summary"] = f"Listed {len(public_artifacts)} SQL artifact(s)."
    elif isinstance(result.get("summary"), str):
        result["summary"] = str(result["summary"]).replace("sql_artifact", "SQL artifact").replace("SQL Artifact", "SQL artifact")
    return _public_sql_artifact(result)


def _reject_private_sql(sql: str) -> None:
    """Reject SQL that asks for private local catalog metadata."""
    lowered_sql = sql.lower()
    if any(token in lowered_sql for token in PRIVATE_SQL_TOKENS):
        raise HTTPException(
            status_code=400,
            detail={
                "status": "error",
                "mode": "private_catalog_blocked",
                "message": "Private source catalog metadata is not exposed through the demo API.",
            },
        )


def _safe_upload_name(filename: str, *, content_type: str | None = None) -> str:
    """Return a stable local filename for a browser upload."""
    clean_name = re.sub(r"[^A-Za-z0-9._ -]+", "-", Path(filename).name).strip(" .")
    if clean_name:
        return clean_name
    extension = DEFAULT_IMAGE_EXTENSION_BY_TYPE.get(content_type or "", "")
    return f"upload{extension}"


def _safe_download_name(name: str) -> str:
    """Return a stable CSV filename for a SQL view export."""
    clean_name = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip(".-")
    return f"{clean_name or 'view'}.csv"


def _csv_buffer_value(buffer: io.StringIO) -> str:
    """Return buffered CSV text and reset the buffer for streaming."""
    value = buffer.getvalue()
    buffer.seek(0)
    buffer.truncate(0)
    return value


def _stream_view_csv(database_path: Path, view_name: str):
    """Yield one SQLite view as CSV chunks."""
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer)
    connection_url = f"file:{database_path}?mode=ro"
    with sqlite3.connect(connection_url, uri=True) as connection:
        cursor = connection.execute(f"SELECT * FROM {quote_identifier(view_name)}")
        columns = [column[0] for column in cursor.description or []]
        writer.writerow(columns)
        yield _csv_buffer_value(buffer)
        while rows := cursor.fetchmany(1000):
            writer.writerows(rows)
            yield _csv_buffer_value(buffer)


def _source_files_payload(
    database_path: Path,
    *,
    sql_artifacts: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Return loaded and uploaded source files for the browser UI."""
    loaded_files = list_loaded_source_summaries(database_path)
    existing_paths = {str(source.get("source_path") or "") for source in loaded_files}
    files = [*loaded_files, *list_uploaded_source_summaries(existing_paths=existing_paths)]
    targets_by_source = _target_profiles_by_source(sql_artifacts or [])
    for source_file in files:
        source_keys = [
            key
            for key in {
                str(source_file.get("source_path") or "").strip(),
                str(source_file.get("name") or "").strip(),
            }
            if key
        ]
        targets_by_name: dict[str, dict[str, Any]] = {}
        for source_key in source_keys:
            for target in targets_by_source.get(source_key, []):
                targets_by_name[target["name"]] = target
        source_file["targets"] = list(targets_by_name.values())
        source_file["target_count"] = len(source_file["targets"])
    return files


def _target_profiles_by_source(sql_artifacts: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Index SQL target profiles by browser-safe source path and file name."""
    targets: dict[str, list[dict[str, Any]]] = {}
    for artifact in sql_artifacts:
        if not isinstance(artifact, dict):
            continue
        target_name = str(artifact.get("name") or "").strip()
        if not target_name:
            continue
        target = {
            "name": target_name,
            "kind": str(artifact.get("kind") or ""),
            "type": str(artifact.get("type") or ""),
            "row_count": artifact.get("row_count"),
            "column_count": artifact.get("column_count"),
            "size_label": artifact.get("size_label"),
            "columns": artifact.get("column_preview") if isinstance(artifact.get("column_preview"), list) else [],
            "summary": str(artifact.get("summary") or ""),
        }
        for source_reference in artifact.get("source_references") or []:
            if not isinstance(source_reference, dict):
                continue
            for key in (source_reference.get("path"), source_reference.get("name")):
                source_key = str(key or "").strip()
                if not source_key:
                    continue
                targets.setdefault(source_key, []).append(target)
    return targets


def _resolve_source_preview_path(source_path: str) -> Path:
    """Resolve a browser-safe source path under the configured workbench root."""
    path = Path(source_path).expanduser()
    if not path.is_absolute():
        path = WORKBENCH_SOURCE_ROOT / path
    resolved_path = path.resolve()
    try:
        resolved_path.relative_to(WORKBENCH_SOURCE_ROOT.resolve())
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "status": "error",
                "mode": "source_outside_workspace",
                "message": "Only workspace source files can be previewed.",
            },
        ) from exc
    if not resolved_path.is_file():
        raise HTTPException(
            status_code=404,
            detail={
                "status": "error",
                "mode": "source_not_found",
                "message": f"Source file not found: {source_path}",
            },
        )
    if resolved_path.suffix.lower() not in TABULAR_UPLOAD_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail={
                "status": "error",
                "mode": "unsupported_source_preview",
                "message": "Raw previews are available for CSV and XLSX sources.",
            },
        )
    return resolved_path


def _spreadsheet_column_label(index: int) -> str:
    """Return the spreadsheet-style label for a zero-based column index."""
    label = ""
    column_number = index + 1
    while column_number:
        column_number, remainder = divmod(column_number - 1, 26)
        label = f"{chr(65 + remainder)}{label}"
    return label


def _source_preview_payload(preview: dict[str, Any], *, max_rows: int) -> dict[str, Any]:
    """Convert a raw tabular preview into the grid result shape used by the UI."""
    raw_rows = preview.get("rows")
    preview_rows = raw_rows if isinstance(raw_rows, list) else []
    truncated = len(preview_rows) > max_rows
    rows = preview_rows[:max_rows]
    column_count = min(
        MAX_SOURCE_PREVIEW_COLUMNS,
        max((len(row) for row in rows if isinstance(row, list)), default=0),
    )
    columns = [_spreadsheet_column_label(index) for index in range(column_count)]
    result_rows: list[dict[str, str]] = []
    for row in rows:
        if not isinstance(row, list):
            continue
        result_rows.append({column: str(row[index]) if index < len(row) else "" for index, column in enumerate(columns)})

    start_row = int(preview.get("start_row") or 1)
    end_row = start_row + len(result_rows) - 1
    full_row_count = preview.get("row_count")
    return {
        "status": "ok",
        "summary": f"Raw {str(preview.get('format') or 'tabular').upper()} preview rows {start_row}-{end_row}.",
        "columns": columns,
        "rows": result_rows,
        "row_count": full_row_count if isinstance(full_row_count, int) else len(result_rows),
        "truncated": truncated or (isinstance(full_row_count, int) and end_row < full_row_count),
    }


def _bootstrap_payload(database_path: Path) -> dict[str, Any]:
    """Build the initial workbench payload with a verified default result."""
    sql_artifact_payload = _public_sql_payload(list_sql_artifacts(database_path=database_path))
    sql_artifacts = sql_artifact_payload.get("sql_artifacts")
    public_sql_artifacts = sql_artifacts if isinstance(sql_artifacts, list) else []
    initial_result = _public_sql_payload(
        run_query(
            DEFAULT_SQL,
            database_path=str(database_path),
            max_rows=100,
        )
    )
    return {
        "status": "ok",
        "sample_sql": DEFAULT_SQL,
        "suggested_questions": SUGGESTED_QUESTIONS,
        "stage_cards": STAGE_CARDS,
        "source_files": _source_files_payload(database_path, sql_artifacts=public_sql_artifacts),
        "sql_artifacts": public_sql_artifacts,
        "sql_artifact_summary": sql_artifact_payload.get("summary", ""),
        "initial_result": initial_result,
    }


def _pdf_upload_summary(path: Path) -> dict[str, Any]:
    """Return a lightweight PDF upload summary without model-backed OCR."""
    import fitz

    with fitz.open(path) as document:
        preview_pages = min(document.page_count, PDF_PREVIEW_PAGE_LIMIT)
        preview_text = "\n".join(document.load_page(index).get_text("text").strip() for index in range(preview_pages)).strip()
        return {
            "status": "uploaded",
            "artifact_backend": "pdf",
            "path": str(path),
            "page_count": document.page_count,
            "preview_text": preview_text[:2000],
        }


def _image_upload_summary(
    path: Path,
    *,
    content_type: str | None,
) -> dict[str, Any]:
    """Return a lightweight image upload summary for pasted or attached screenshots."""
    return {
        "status": "uploaded",
        "artifact_backend": "image",
        "name": path.name,
        "path": str(path),
        "content_type": content_type or "application/octet-stream",
        "size_bytes": path.stat().st_size,
    }


def _store_upload(file: UploadFile) -> Path:
    """Persist an uploaded file into the local demo upload folder."""
    filename = _safe_upload_name(file.filename or "", content_type=file.content_type)
    suffix = Path(filename).suffix.lower()
    if suffix not in UPLOAD_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail={
                "status": "error",
                "mode": "unsupported_upload_type",
                "message": "Uploads are limited to CSV, XLSX, PDF, or image files.",
            },
        )

    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    destination = UPLOADS_DIR / filename
    if destination.exists():
        destination = UPLOADS_DIR / f"{destination.stem}-{datetime.now(tz=UTC).strftime('%Y%m%d%H%M%S')}{destination.suffix}"
    with destination.open("wb") as output:
        shutil.copyfileobj(file.file, output)
    return destination


def _skill_modified_at(skill_file: Path) -> str:
    """Return one ISO timestamp from the skill file mtime."""
    return datetime.fromtimestamp(skill_file.stat().st_mtime, tz=UTC).isoformat()


def _writable_skill_path(skill_name: str) -> Path:
    """Resolve the workspace skill file path or raise an HTTP error."""
    loaded_payload = load_skill(path=str(SKILLS_DIR), skill=skill_name)
    loaded_skills = loaded_payload.get("skills", [])
    if not loaded_skills:
        raise HTTPException(
            status_code=404,
            detail={"status": "error", "message": f"Skill not found: {skill_name}"},
        )

    instructions = loaded_skills[0].get("instructions", {})
    instruction_path = instructions.get("path") if isinstance(instructions, dict) else None
    if not isinstance(instruction_path, str):
        raise HTTPException(
            status_code=400,
            detail={
                "status": "error",
                "message": f"Skill has no writable path: {skill_name}",
            },
        )
    return Path(instruction_path)


def _writable_skill_resource_path(resource_path: str) -> Path:
    """Resolve a skill resource path under the workspace skills directory."""
    path = Path(resource_path).expanduser()
    if not path.is_absolute():
        path = REPO_ROOT / path
    resolved_path = path.resolve()
    skill_root = SKILLS_DIR.resolve()
    if not resolved_path.is_file():
        raise HTTPException(
            status_code=404,
            detail={
                "status": "error",
                "message": f"Skill resource not found: {resource_path}",
            },
        )
    try:
        resolved_path.relative_to(skill_root)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "status": "error",
                "message": "Only files under the workspace skills directory can be saved here.",
            },
        ) from exc
    return resolved_path


@router.get("/health")
def health() -> dict[str, Any]:
    """Return basic API and local model configuration status."""
    try:
        default_database_path()
        database_ready = True
    except WorkspaceDataMissingError:
        database_ready = False
    return {
        "status": "ok",
        "app": "tabuflow",
        "model": resolve_agent_model(),
        "llm_configured": has_llm_environment(),
        "database_ready": database_ready,
    }


@router.get("/settings/llm", response_model=LlmSettings)
def llm_settings() -> LlmSettings:
    """Return editable OpenAI-compatible LLM settings for the browser UI."""
    return LlmSettings.model_validate(llm_settings_payload())


@router.post("/settings/llm", response_model=LlmSettings)
def save_llm_settings(request: LlmSettings) -> LlmSettings:
    """Persist editable OpenAI-compatible LLM settings for the current app."""
    return LlmSettings.model_validate(update_llm_settings(request.model_dump()))


@router.get("/bootstrap")
def bootstrap() -> dict[str, Any]:
    """Return initial workbench data for the browser UI."""
    try:
        database_path = default_database_path()
    except WorkspaceDataMissingError as exc:
        raise _workspace_data_error(exc) from exc
    return _bootstrap_payload(database_path)


@router.post("/files/upload")
def upload_file(file: UploadFile = File(...)) -> dict[str, Any]:
    """Upload one CSV, XLSX, PDF, or image file into the local demo workspace."""
    saved_path = _store_upload(file)
    suffix = saved_path.suffix.lower()
    try:
        if suffix in TABULAR_UPLOAD_EXTENSIONS:
            upload_result = extract_tabular_source(saved_path, root_dir=REPO_ROOT)
        elif suffix in IMAGE_UPLOAD_EXTENSIONS:
            upload_result = _image_upload_summary(saved_path, content_type=file.content_type)
        else:
            upload_result = _pdf_upload_summary(saved_path)
        database_path = default_database_path()
    except Exception as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "status": "error",
                "mode": "upload_processing_failed",
                "message": str(exc),
                "path": str(saved_path),
            },
        ) from exc
    return {
        "status": "ok",
        "upload": upload_result,
        "bootstrap": _bootstrap_payload(database_path),
    }


@router.post("/files/preview")
def preview_source_file(request: SourcePreviewRequest) -> dict[str, Any]:
    """Return a bounded raw grid preview for one workspace source file."""
    source_path = _resolve_source_preview_path(request.path)
    try:
        preview = inspect_tabular_file(
            source_path,
            start_row=request.start_row,
            limit=request.max_rows + 1,
            sheet=request.sheet,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "status": "error",
                "mode": "source_preview_failed",
                "message": str(exc),
                "path": request.path,
            },
        ) from exc
    return _source_preview_payload(preview, max_rows=request.max_rows)


@router.post("/chat")
def chat(request: ChatRequest) -> dict[str, Any]:
    """Run one user chat message through the configured assistant path."""
    try:
        return run_chat(request)
    except ChatConfigurationError as exc:
        raise _chat_configuration_error(str(exc)) from exc
    except ChatRuntimeError as exc:
        raise _chat_runtime_error(str(exc)) from exc


@router.post("/chat/stream")
def chat_stream(request: ChatRequest) -> StreamingResponse:
    """Stream one chat turn as AI SDK UI-message chunks."""
    if not has_llm_environment():
        raise _chat_configuration_error()

    def chunk_lines():
        for chunk in stream_chat_chunks(request):
            yield f"{json.dumps(chunk)}\n"

    return StreamingResponse(chunk_lines(), media_type="application/x-ndjson")


@router.post("/sql/run")
def run_sql(request: SqlRunRequest) -> dict[str, Any]:
    """Execute one bounded read-only SQL statement."""
    _reject_private_sql(request.sql)
    try:
        return _public_sql_payload(
            run_query(
                request.sql,
                database_path=resolve_database_path(),
                max_rows=request.max_rows,
            )
        )
    except WorkspaceDataMissingError as exc:
        raise _workspace_data_error(exc) from exc


@router.get("/sql/sql-artifacts")
def sql_artifacts() -> dict[str, Any]:
    """List queryable SQL artifacts for the prepared SQLite database."""
    try:
        return _public_sql_payload(list_sql_artifacts(database_path=resolve_database_path()))
    except WorkspaceDataMissingError as exc:
        raise _workspace_data_error(exc) from exc


@router.get("/sql/sql-artifacts/{sql_artifact_name}/download")
def download_sql_artifact_view(sql_artifact_name: str) -> StreamingResponse:
    """Download one saved SQLite view as a CSV file."""
    try:
        database_path = default_database_path()
        artifact = describe_sql_artifact(sql_artifact_name, database_path=database_path)
    except WorkspaceDataMissingError as exc:
        raise _workspace_data_error(exc) from exc
    if artifact.get("status") == "error" and artifact.get("error_type") == "missing_sql_artifact":
        raise HTTPException(status_code=404, detail=artifact)
    if artifact.get("status") == "error":
        raise HTTPException(status_code=400, detail=artifact)
    if artifact.get("type") != "view" or artifact.get("kind") == "typed_content_view":
        raise HTTPException(
            status_code=400,
            detail={
                "status": "error",
                "mode": "not_downloadable_view",
                "message": "Only saved SQLite views can be downloaded as CSV.",
            },
        )

    filename = _safe_download_name(sql_artifact_name)
    return StreamingResponse(
        _stream_view_csv(database_path, sql_artifact_name),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/sql/sql-artifacts/{sql_artifact_name}")
def sql_artifact(sql_artifact_name: str) -> dict[str, Any]:
    """Describe one queryable SQL artifact."""
    try:
        payload = _public_sql_payload(describe_sql_artifact(sql_artifact_name, database_path=resolve_database_path()))
    except WorkspaceDataMissingError as exc:
        raise _workspace_data_error(exc) from exc
    if payload.get("status") == "error" and payload.get("error_type") == "missing_sql_artifact":
        raise HTTPException(status_code=404, detail=payload)
    return payload


@router.post("/explainer/summary")
def file_explanation(request: FileExplanationRequest) -> dict[str, Any]:
    """Return cached or newly generated non-technical file explanation metadata."""
    try:
        explanation = explain_file(
            path=request.path,
            repo_root=REPO_ROOT,
            force=request.force,
            model=request.model,
        )
        return {"status": "ok", **asdict(explanation)}
    except MissingExplainerModelError as exc:
        raise _chat_configuration_error(str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail={"status": "error", "message": str(exc)}) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"status": "error", "message": str(exc)}) from exc
    except Exception as exc:
        raise _chat_runtime_error(str(exc)) from exc


@router.get("/skills")
def skills() -> dict[str, Any]:
    """List workspace skills with full instruction content for the browser UI."""
    payload = list_skills(path=str(SKILLS_DIR), max_files=50)
    enriched_skills: list[dict[str, Any]] = []
    diagnostics = list(payload.get("diagnostics", []))
    for skill in payload.get("skills", []):
        skill_name = skill.get("name")
        if not isinstance(skill_name, str):
            enriched_skills.append(skill)
            continue
        loaded_payload = load_skill(path=str(SKILLS_DIR), skill=skill_name)
        loaded_skills = loaded_payload.get("skills", [])
        if loaded_skills:
            loaded_skill = loaded_skills[0]
            instructions = loaded_skill.get("instructions", {})
            raw_content = instructions.get("content", "")
            modified_at = None
            instruction_path = instructions.get("path") if isinstance(instructions, dict) else None
            if isinstance(instruction_path, str):
                skill_file = Path(instruction_path)
                raw_content = skill_file.read_text(encoding="utf-8")
                modified_at = _skill_modified_at(skill_file)
            enriched_skills.append(
                {
                    **skill,
                    "modified_at": modified_at,
                    "instructions": instructions,
                    "content": raw_content,
                    "examples": loaded_skill.get("examples", []),
                    "references": loaded_skill.get("references", []),
                    "scripts": loaded_skill.get("scripts", []),
                }
            )
        else:
            enriched_skills.append(skill)
        diagnostics.extend(loaded_payload.get("diagnostics", []))

    result = {**payload, "skills": enriched_skills}
    if diagnostics:
        result["diagnostics"] = diagnostics
    return result


@router.post("/skills/create")
def create_skill(request: SkillCreateRequest) -> dict[str, Any]:
    """Create a deterministic workspace skill package frame."""
    result = create_skill_package(
        path=str(SKILLS_DIR),
        name=request.name,
        description=request.description,
        reference_files=request.reference_files,
        script_files=request.script_files,
    )
    if result.get("status") == "created":
        return result
    status_code = 409 if result.get("error_type") == "skill_exists" else 400
    raise HTTPException(status_code=status_code, detail=result)


@router.post("/skills/save")
def save_skill(request: SkillSaveRequest) -> dict[str, Any]:
    """Persist skill editor content and return the updated file metadata."""
    skill_file = _writable_skill_path(request.name)
    skill_file.write_text(request.content, encoding="utf-8")
    return {
        "status": "saved",
        "name": request.name,
        "content": request.content,
        "modified_at": _skill_modified_at(skill_file),
        "summary": "Skill saved.",
    }


@router.post("/skills/resource/save")
def save_skill_resource(request: SkillResourceSaveRequest) -> dict[str, Any]:
    """Persist an editable skill resource file and return updated metadata."""
    resource_path = _writable_skill_resource_path(request.path)
    resource_path.write_text(request.content, encoding="utf-8")
    return {
        "status": "saved",
        "path": str(resource_path),
        "relative_path": str(resource_path.relative_to(REPO_ROOT)),
        "content": request.content,
        "modified_at": _skill_modified_at(resource_path),
        "summary": "Skill resource saved.",
    }
