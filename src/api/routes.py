"""HTTP routes for the data-agentics workbench API."""

from contextlib import suppress
from datetime import UTC, datetime
import json
from pathlib import Path
import re
import shutil
from typing import Any

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse

from ..config import (
    DEFAULT_AGENT_MODEL,
    MISSING_LLM_CONFIG_MESSAGE,
    REPO_ROOT,
    SKILLS_DIR,
    UPLOADS_DIR,
    has_llm_environment,
)
from ..explainer import MissingExplainerModelError, explain_file
from ..tools import list_skills, load_skills
from ..tools.sql.query import describe_target, list_targets, run_query
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
    SkillResourceSaveRequest,
    SkillSaveRequest,
    SqlRunRequest,
)
from .workspace_data import (
    WorkspaceDataMissingError,
    default_database_path,
    list_prepared_source_summaries,
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


def _public_target(target: dict[str, Any]) -> dict[str, Any]:
    """Remove private source path metadata from a SQL target payload."""
    source_references = _public_source_references(target.get("source_mappings"))
    public_target = {key: value for key, value in target.items() if key not in SOURCE_MAPPING_PRIVATE_KEYS}
    public_target["source_references"] = source_references
    public_target["source_file_names"] = list(dict.fromkeys(reference["name"] for reference in source_references))
    return public_target


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
    if isinstance(result.get("targets"), list):
        result["targets"] = [_public_target(target) for target in result["targets"] if isinstance(target, dict) and target.get("kind") != "typed_content_view"]
    return _public_target(result)


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


def _source_files_payload(database_path: Path) -> list[dict[str, Any]]:
    """Return prepared and uploaded source files for the browser UI."""
    prepared_files = list_prepared_source_summaries(database_path)
    existing_paths = {str(source.get("source_path") or "") for source in prepared_files}
    return [*prepared_files, *list_uploaded_source_summaries(existing_paths=existing_paths)]


def _bootstrap_payload(database_path: Path) -> dict[str, Any]:
    """Build the initial workbench payload with a verified default result."""
    target_payload = _public_sql_payload(list_targets(database_path=database_path))
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
        "source_files": _source_files_payload(database_path),
        "targets": target_payload.get("targets", []),
        "target_summary": target_payload.get("summary", ""),
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
            "target_backend": "pdf",
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
        "target_backend": "image",
        "name": path.name,
        "path": str(path),
        "content_type": content_type or "application/octet-stream",
        "size_bytes": path.stat().st_size,
    }


def _tabular_upload_summary(path: Path) -> dict[str, Any]:
    """Return a lightweight tabular upload summary before prep extracts it."""
    return {
        "status": "uploaded",
        "target_backend": "tabular",
        "name": path.name,
        "path": str(path),
        "format": path.suffix.lower().lstrip("."),
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


def _skill_modified_at(skills_file: Path) -> str:
    """Return one ISO timestamp from the skill file mtime."""
    return datetime.fromtimestamp(skills_file.stat().st_mtime, tz=UTC).isoformat()


def _writable_skill_path(skill_name: str) -> Path:
    """Resolve the workspace skill file path or raise an HTTP error."""
    loaded_payload = load_skills.func(path=str(SKILLS_DIR), skills=skill_name)
    loaded_skills = loaded_payload.get("skills", [])
    if not loaded_skills:
        raise HTTPException(
            status_code=404,
            detail={"status": "error", "message": f"Skill not found: {skill_name}"},
        )

    skills_path = loaded_skills[0].get("skills_path")
    if not isinstance(skills_path, str):
        raise HTTPException(
            status_code=400,
            detail={
                "status": "error",
                "message": f"Skill has no writable path: {skill_name}",
            },
        )
    return Path(skills_path)


def _writable_skill_resource_path(resource_path: str) -> Path:
    """Resolve a skill resource path under the workspace skills directory."""
    path = Path(resource_path).expanduser()
    if not path.is_absolute():
        path = REPO_ROOT / path
    resolved_path = path.resolve()
    skills_root = SKILLS_DIR.resolve()
    if not resolved_path.is_file():
        raise HTTPException(
            status_code=404,
            detail={
                "status": "error",
                "message": f"Skill resource not found: {resource_path}",
            },
        )
    try:
        resolved_path.relative_to(skills_root)
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
        "app": "data-agentics",
        "model": DEFAULT_AGENT_MODEL,
        "llm_configured": has_llm_environment(),
        "database_ready": database_ready,
    }


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
            upload_result = _tabular_upload_summary(saved_path)
        elif suffix in IMAGE_UPLOAD_EXTENSIONS:
            upload_result = _image_upload_summary(saved_path, content_type=file.content_type)
        else:
            upload_result = _pdf_upload_summary(saved_path)
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
    response = {
        "status": "ok",
        "upload": upload_result,
    }
    with suppress(WorkspaceDataMissingError):
        response["bootstrap"] = _bootstrap_payload(default_database_path())
    return response


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


@router.get("/sql/targets")
def sql_targets() -> dict[str, Any]:
    """List queryable targets for the prepared SQLite database."""
    try:
        return _public_sql_payload(list_targets(database_path=resolve_database_path()))
    except WorkspaceDataMissingError as exc:
        raise _workspace_data_error(exc) from exc


@router.get("/sql/targets/{target_name}")
def sql_target(target_name: str) -> dict[str, Any]:
    """Describe one queryable SQLite target."""
    try:
        payload = _public_sql_payload(describe_target(target_name, database_path=resolve_database_path()))
    except WorkspaceDataMissingError as exc:
        raise _workspace_data_error(exc) from exc
    if payload.get("status") == "error" and payload.get("error_type") == "missing_target":
        raise HTTPException(status_code=404, detail=payload)
    return payload


@router.post("/explainer/summary")
def file_explanation(request: FileExplanationRequest) -> dict[str, Any]:
    """Return cached or newly generated non-technical file explanation metadata."""
    try:
        return explain_file(
            path=request.path,
            repo_root=REPO_ROOT,
            force=request.force,
            model=request.model,
        ).to_payload()
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
    payload = list_skills.func(path=str(SKILLS_DIR), max_files=50)
    enriched_skills: list[dict[str, Any]] = []
    diagnostics = list(payload.get("diagnostics", []))
    for skill in payload.get("skills", []):
        skill_name = skill.get("name")
        if not isinstance(skill_name, str):
            enriched_skills.append(skill)
            continue
        loaded_payload = load_skills.func(path=str(SKILLS_DIR), skills=skill_name)
        loaded_skills = loaded_payload.get("skills", [])
        if loaded_skills:
            loaded_skill = loaded_skills[0]
            instructions = loaded_skill.get("instructions", {})
            skills_path = loaded_skill.get("skills_path")
            raw_content = instructions.get("content", "")
            modified_at = None
            if isinstance(skills_path, str):
                skills_file = Path(skills_path)
                raw_content = skills_file.read_text(encoding="utf-8")
                modified_at = _skill_modified_at(skills_file)
            enriched_skills.append(
                {
                    **skill,
                    "skills_path": skills_path,
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


@router.post("/skills/save")
def save_skill(request: SkillSaveRequest) -> dict[str, Any]:
    """Persist skill editor content and return the updated file metadata."""
    skills_file = _writable_skill_path(request.name)
    skills_file.write_text(request.content, encoding="utf-8")
    return {
        "status": "saved",
        "name": request.name,
        "content": request.content,
        "skills_path": str(skills_file),
        "modified_at": _skill_modified_at(skills_file),
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
