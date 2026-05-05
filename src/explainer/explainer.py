"""Model-backed file explanations with durable metadata storage."""

from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
import sqlite3
from typing import Any

from langchain_core.messages import HumanMessage

from ..config import MISSING_LLM_CONFIG_MESSAGE, has_llm_environment, resolve_agent_model
from ..clients.openai import ChatOpenAI

EXPLAINABLE_EXTENSIONS = {".md", ".markdown", ".py", ".sql"}
MAX_EXPLAINER_CHARS = 80_000
METADATA_FILENAME = "file_metadata.sqlite"


class MissingExplainerModelError(RuntimeError):
    """Raised when a summary must be generated but no model is configured."""


@dataclass(frozen=True)
class FileExplanation:
    """Persisted explanation metadata for one file snapshot."""

    path: str
    relative_path: str
    content_hash: str
    summary: str
    model: str
    generated_at: str
    cached: bool

    def to_payload(self) -> dict[str, Any]:
        """Return the API payload shape."""
        return {
            "status": "ok",
            "path": self.path,
            "relative_path": self.relative_path,
            "content_hash": self.content_hash,
            "summary": self.summary,
            "model": self.model,
            "generated_at": self.generated_at,
            "cached": self.cached,
        }


def explain_file(
    *,
    path: str,
    repo_root: Path,
    force: bool = False,
    model: str | None = None,
) -> FileExplanation:
    """Return a cached or newly generated explanation for a supported text file."""
    resolved_path = _resolve_explainable_path(path=path, repo_root=repo_root)
    text = _read_explainable_text(resolved_path)
    content_hash = sha256(text.encode("utf-8")).hexdigest()
    relative_path = str(resolved_path.relative_to(repo_root.resolve()))
    metadata_path = _metadata_database_path(repo_root)
    _ensure_metadata_store(metadata_path)

    if not force:
        cached = _load_cached_explanation(metadata_path, relative_path=relative_path, content_hash=content_hash)
        if cached is not None:
            return FileExplanation(
                path=str(resolved_path),
                relative_path=relative_path,
                content_hash=content_hash,
                summary=cached["summary"],
                model=cached["model"],
                generated_at=cached["generated_at"],
                cached=True,
            )

    resolved_model = resolve_agent_model(model)
    _require_model_environment()
    summary = _generate_summary(path=relative_path, text=text, model=resolved_model)
    generated_at = datetime.now(tz=UTC).isoformat()
    _save_explanation(
        metadata_path,
        relative_path=relative_path,
        content_hash=content_hash,
        summary=summary,
        model=resolved_model,
        generated_at=generated_at,
    )
    return FileExplanation(
        path=str(resolved_path),
        relative_path=relative_path,
        content_hash=content_hash,
        summary=summary,
        model=resolved_model,
        generated_at=generated_at,
        cached=False,
    )


def _resolve_explainable_path(*, path: str, repo_root: Path) -> Path:
    """Resolve and validate one workbench file path."""
    if not path.strip():
        raise ValueError("File path is required.")
    root = repo_root.resolve()
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved_path = candidate.resolve()
    try:
        resolved_path.relative_to(root)
    except ValueError as exc:
        raise ValueError("Explainer files must stay inside the workspace.") from exc
    if not resolved_path.is_file():
        raise FileNotFoundError(f"Explainer file does not exist: {path}")
    if resolved_path.suffix.lower() not in EXPLAINABLE_EXTENSIONS:
        raise ValueError("Only Markdown, Python, and SQL files can be explained.")
    return resolved_path


def _read_explainable_text(path: Path) -> str:
    """Read bounded UTF-8 text from one file."""
    text = path.read_text(encoding="utf-8")
    if len(text) > MAX_EXPLAINER_CHARS:
        raise ValueError(f"File is too large to explain safely: {path.name}")
    return text


def _metadata_database_path(repo_root: Path) -> Path:
    """Return the repo-local metadata database path."""
    path = repo_root / "data" / METADATA_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _require_model_environment() -> None:
    """Fail before generation when the OpenAI-compatible model is unavailable."""
    if not has_llm_environment():
        raise MissingExplainerModelError(MISSING_LLM_CONFIG_MESSAGE)


def _ensure_metadata_store(database_path: Path) -> None:
    """Create the file explanation metadata table if needed."""
    with closing(sqlite3.connect(str(database_path))) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS file_explanations (
                relative_path TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                summary TEXT NOT NULL,
                model TEXT NOT NULL,
                generated_at TEXT NOT NULL,
                PRIMARY KEY (relative_path, content_hash)
            )
            """
        )
        connection.commit()


def _load_cached_explanation(
    database_path: Path,
    *,
    relative_path: str,
    content_hash: str,
) -> dict[str, str] | None:
    """Return cached explanation metadata for the current file snapshot."""
    with closing(sqlite3.connect(str(database_path))) as connection:
        row = connection.execute(
            """
            SELECT summary, model, generated_at
            FROM file_explanations
            WHERE relative_path = ? AND content_hash = ?
            """,
            (relative_path, content_hash),
        ).fetchone()
    if row is None:
        return None
    return {
        "summary": str(row[0]),
        "model": str(row[1]),
        "generated_at": str(row[2]),
    }


def _save_explanation(
    database_path: Path,
    *,
    relative_path: str,
    content_hash: str,
    summary: str,
    model: str,
    generated_at: str,
) -> None:
    """Persist explanation metadata for one file snapshot."""
    with closing(sqlite3.connect(str(database_path))) as connection:
        connection.execute(
            """
            INSERT INTO file_explanations (
                relative_path,
                content_hash,
                summary,
                model,
                generated_at
            )
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(relative_path, content_hash)
            DO UPDATE SET
                summary = excluded.summary,
                model = excluded.model,
                generated_at = excluded.generated_at
            """,
            (relative_path, content_hash, summary, model, generated_at),
        )
        connection.commit()


def _generate_summary(*, path: str, text: str, model: str) -> str:
    """Ask the configured model for a non-technical file explanation."""
    llm = ChatOpenAI(model=model, temperature=0.2)
    prompt = f"""You explain workspace files for non-technical users.

Give useful guidance about what the file helps them understand or do, what data or other files it depends on, what it produces or changes, and what assumptions or risks are visible.

Use plain language and do not invent behavior. Do not use a forced template, fixed heading sequence, or repeated labels like "What it is", "Purpose", "Data it depends on", or "What it produces".

If placeholder tokens are visible, explain that the file is a template and that placeholders need real inspected names, but do not turn every placeholder into a rigid requirements checklist.

Write a concise, natural explanation for a non-technical user. Use paragraphs or bullets only when they make the guidance easier to scan. Include enough detail for the user to know when this file is useful and what to be careful about.

File path: {path}

```text
{text}
```"""
    messages = [HumanMessage(content=prompt)]
    response = llm.invoke(messages)
    content = response.content
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = [str(part.get("text") if isinstance(part, dict) else part).strip() for part in content]
        return "\n".join(part for part in parts if part).strip()
    return str(content).strip()
