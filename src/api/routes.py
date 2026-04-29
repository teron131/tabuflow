"""HTTP routes for the data-agentics workbench API."""

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

from ..agents.config import DEFAULT_AGENT_MODEL
from ..tools import list_skills, load_skills
from ..tools.sql.query import describe_target, list_targets, run_query
from .chat import ChatConfigurationError, ChatRuntimeError, has_llm_environment, run_chat
from .constants import DEFAULT_SQL, STAGE_CARDS, SUGGESTED_QUESTIONS
from .schemas import ChatRequest, SkillDraftRequest, SqlRunRequest
from .workspace_data import (
    WorkspaceDataMissingError,
    default_database_path,
    list_prepared_source_summaries,
    resolve_database_path,
)

router = APIRouter(prefix="/api")
PRIVATE_SQL_TOKENS = ("_tabular_", "source_path")


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


def _public_target(target: dict[str, Any]) -> dict[str, Any]:
    """Remove private source path metadata from a SQL target payload."""
    return {
        key: value
        for key, value in target.items()
        if key
        not in {
            "database_path",
            "source_mappings",
            "source_path_preview",
            "source_paths",
            "source_paths_truncated",
        }
    }


def _public_sql_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Remove private local path metadata from SQL tool payloads."""
    result = {key: value for key, value in payload.items() if key != "database_path"}
    if isinstance(result.get("targets"), list):
        result["targets"] = [_public_target(target) for target in result["targets"] if isinstance(target, dict)]
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
    target_payload = _public_sql_payload(list_targets(database_path=database_path))
    return {
        "status": "ok",
        "sample_sql": DEFAULT_SQL,
        "suggested_questions": SUGGESTED_QUESTIONS,
        "stage_cards": STAGE_CARDS,
        "source_files": list_prepared_source_summaries(database_path),
        "targets": target_payload.get("targets", []),
        "target_summary": target_payload.get("summary", ""),
    }


@router.post("/chat")
def chat(request: ChatRequest) -> dict[str, Any]:
    """Run one user chat message through the configured assistant path."""
    try:
        return run_chat(request)
    except ChatConfigurationError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "status": "error",
                "mode": "missing_llm_config",
                "message": str(exc),
                "llm_configured": False,
            },
        ) from exc
    except ChatRuntimeError as exc:
        raise HTTPException(
            status_code=502,
            detail={
                "status": "error",
                "mode": "model_error",
                "message": str(exc),
                "llm_configured": True,
            },
        ) from exc


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


@router.get("/skills")
def skills() -> dict[str, Any]:
    """List workspace skills with full instruction content for the browser UI."""
    payload = list_skills.func(path="skills", max_files=50)
    enriched_skills: list[dict[str, Any]] = []
    diagnostics = list(payload.get("diagnostics", []))
    for skill in payload.get("skills", []):
        skill_name = skill.get("name")
        if not isinstance(skill_name, str):
            enriched_skills.append(skill)
            continue
        loaded_payload = load_skills.func(path="skills", skills=skill_name)
        loaded_skills = loaded_payload.get("skills", [])
        if loaded_skills:
            loaded_skill = loaded_skills[0]
            instructions = loaded_skill.get("instructions", {})
            skills_path = loaded_skill.get("skills_path")
            raw_content = instructions.get("content", "")
            if isinstance(skills_path, str):
                raw_content = Path(skills_path).read_text(encoding="utf-8")
            enriched_skills.append(
                {
                    **skill,
                    "skills_path": skills_path,
                    "instructions": instructions,
                    "content": raw_content,
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


@router.post("/skills/draft")
def draft_skill(request: SkillDraftRequest) -> dict[str, Any]:
    """Accept a non-persistent skill draft from the browser UI."""
    return {
        "status": "drafted",
        "name": request.name,
        "content": request.content,
        "summary": "Draft kept in the browser. Persistence is intentionally disabled in this UI pass.",
    }
