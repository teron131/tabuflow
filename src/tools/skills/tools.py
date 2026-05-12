"""Workspace skills tools."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import math
import os
from pathlib import Path
import re
from typing import Any

from langchain.tools import tool
from langchain_core.embeddings import Embeddings
import yaml

from ...clients.openai import OpenAIEmbeddings

SKILL_FILENAME = "SKILL.md"
DEFAULT_MAX_FILES = 20
DEFAULT_MAX_TOKENS = 50_000
CHARS_PER_TOKEN_RATIO = 4
DEFAULT_MAX_CHARS_PER_FILE = DEFAULT_MAX_TOKENS * CHARS_PER_TOKEN_RATIO
DEFAULT_SKILL_SEARCH_MODEL = "text-embedding-3-small"
DEFAULT_SKILL_SEARCH_TOP_K = 5
DEFAULT_SKILL_SEARCH_SCORE_THRESHOLD = 0.2
IGNORED_DIR_NAMES = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "node_modules",
    "venv",
}
SKILL_NAME_PATTERN = re.compile(r"^[a-z0-9-]{1,64}$")


@dataclass(frozen=True)
class SkillFile:
    """Parsed skill file content plus stable path metadata."""

    path: Path
    relative_path: str
    skill_root: Path
    frontmatter: dict[str, Any]
    body: str


@dataclass(frozen=True)
class SkillInstructions:
    """Structured instructions payload for one skill."""

    path: str
    relative_path: str
    content: str


@dataclass(frozen=True)
class SkillReference:
    """Structured reference payload for one text resource."""

    path: str
    relative_path: str
    kind: str
    content: str


@dataclass(frozen=True)
class SkillScript:
    """Structured script payload for one runnable resource."""

    path: str
    relative_path: str
    content: str


@dataclass(frozen=True)
class LoadedSkill:
    """Structured loaded skill payload used by the skill tools."""

    path: str
    name: str
    description: str
    instructions: SkillInstructions
    examples: list[SkillScript]
    references: list[SkillReference]
    scripts: list[SkillScript]


@dataclass(frozen=True)
class SkillSearchEntry:
    """Indexed skills metadata plus one cached embedding vector."""

    payload: dict[str, Any]
    embedding: list[float]


@dataclass(frozen=True)
class SkillSearchIndex:
    """One cached embedding index for workspace skills search."""

    signature: tuple[tuple[str, int, int], ...]
    entries: list[SkillSearchEntry]


_SKILL_SEARCH_CACHE: dict[tuple[str, str], SkillSearchIndex] = {}


def _skill_name_error(name: str) -> str | None:
    """Return a validation error for a skill package name."""
    if not name:
        return "Missing required skill name."
    if not SKILL_NAME_PATTERN.fullmatch(name) or name.startswith("-") or name.endswith("-") or "--" in name:
        return f"Invalid skill name: {name!r}."
    return None


def _skill_title(name: str) -> str:
    """Return a readable heading from a kebab-case skill name."""
    return " ".join(part.upper() if len(part) <= 3 else part.capitalize() for part in name.split("-"))


def _skill_error_result(error_type: str, message: str) -> dict[str, Any]:
    """Return the shared skill-package creation error payload."""
    return {
        "status": "error",
        "error_type": error_type,
        "message": message,
    }


def _skill_frame_text(*, name: str, description: str) -> str:
    """Return the deterministic initial SKILL.md scaffold."""
    frontmatter = yaml.safe_dump(
        {
            "name": name,
            "description": description,
        },
        sort_keys=False,
        allow_unicode=False,
        width=1000,
    ).strip()
    return "\n".join(
        [
            "---",
            frontmatter,
            "---",
            "",
            f"# {_skill_title(name)}",
            "",
            "## Workflow",
            "",
            "- TODO: Describe the repeatable workflow this skill owns.",
            "",
            "## References",
            "",
            "- Add detailed contracts, schemas, or SQL under `references/` when needed.",
            "",
            "## Scripts",
            "",
            "- Add deterministic scripts under `scripts/` when the workflow needs executable support.",
            "",
        ]
    )


def _resource_path_error(path: str) -> str | None:
    """Return a validation error for a starter resource file path."""
    candidate = Path(path)
    if not path.strip():
        return "Resource file names cannot be empty."
    if candidate.is_absolute() or path.startswith("~") or ".." in candidate.parts:
        return f"Resource file must be a relative path without traversal: {path!r}."
    if any(part in {"", ".", ".."} for part in candidate.parts):
        return f"Resource file contains an invalid path segment: {path!r}."
    return None


def _resource_frame_text(path: str) -> str:
    """Return editable starter content for one optional resource file."""
    resource_path = Path(path)
    title = _skill_title(resource_path.stem.replace("_", "-"))
    suffix = resource_path.suffix.lower()
    if suffix == ".sql":
        return "-- Description: TODO\n\n"
    if suffix in {".md", ".markdown"}:
        return f"# {title}\n\n"
    if suffix == ".py":
        return '"""TODO: Describe this skill support script."""\n'
    if suffix == ".sh":
        return "#!/usr/bin/env bash\nset -euo pipefail\n\n"
    return "TODO\n"


def _write_starter_resources(
    *,
    skill_dir: Path,
    resource_dir_name: str,
    resource_files: list[str] | None,
) -> list[Path]:
    """Write optional editable starter resources under a skill resource directory."""
    created_paths: list[Path] = []
    for resource_file in resource_files or []:
        if error_message := _resource_path_error(resource_file):
            raise ValueError(error_message)
        resource_path = (skill_dir / resource_dir_name / resource_file).resolve()
        try:
            resource_path.relative_to(skill_dir / resource_dir_name)
        except ValueError as exc:
            raise ValueError(f"Resource file must stay inside {resource_dir_name}/: {resource_file!r}.") from exc
        if resource_path.exists():
            raise FileExistsError(f"Resource file already exists: {resource_path}")
        resource_path.parent.mkdir(parents=True, exist_ok=True)
        resource_path.write_text(_resource_frame_text(resource_file), encoding="utf-8")
        created_paths.append(resource_path)
    return created_paths


def create_skill_package_frame(
    *,
    path: str = "skills",
    name: str,
    description: str,
    reference_files: list[str] | None = None,
    script_files: list[str] | None = None,
) -> dict[str, Any]:
    """Create a deterministic skill package frame for later scoped edits."""
    normalized_name = name.strip()
    normalized_description = " ".join(description.strip().split())
    normalized_reference_files = list(reference_files or [])
    normalized_script_files = list(script_files or [])

    if error_message := _skill_name_error(normalized_name):
        return _skill_error_result("invalid_skill_name", error_message)
    if not normalized_description:
        return _skill_error_result("invalid_description", "Missing required skill description.")
    for resource_files in (normalized_reference_files, normalized_script_files):
        if len(set(resource_files)) != len(resource_files):
            return _skill_error_result(
                "duplicate_resource_path",
                "Starter resource file names must be unique within each resource directory.",
            )
    for resource_file in [*normalized_reference_files, *normalized_script_files]:
        if error_message := _resource_path_error(resource_file):
            return _skill_error_result("invalid_resource_path", error_message)

    skill_root = _resolve_search_path(root_dir=Path.cwd().resolve(), path=path)
    skill_dir = (skill_root / normalized_name).resolve()

    try:
        skill_dir.relative_to(skill_root.resolve())
    except ValueError:
        return _skill_error_result("invalid_skill_path", f"Skill package path must stay inside {skill_root}.")

    if skill_dir.exists():
        return _skill_error_result("skill_exists", f"Skill package already exists: {skill_dir}")

    try:
        skill_dir.mkdir(parents=True)
        references_dir = skill_dir / "references"
        scripts_dir = skill_dir / "scripts"
        references_dir.mkdir()
        scripts_dir.mkdir()

        skill_file = skill_dir / SKILL_FILENAME
        skill_file.write_text(
            _skill_frame_text(
                name=normalized_name,
                description=normalized_description,
            ),
            encoding="utf-8",
        )
        created_resource_paths = [
            *_write_starter_resources(
                skill_dir=skill_dir,
                resource_dir_name="references",
                resource_files=normalized_reference_files,
            ),
            *_write_starter_resources(
                skill_dir=skill_dir,
                resource_dir_name="scripts",
                resource_files=normalized_script_files,
            ),
        ]
    except (OSError, ValueError) as exc:
        return _skill_error_result("create_failed", str(exc))

    created_paths = [
        skill_file,
        references_dir,
        scripts_dir,
        *created_resource_paths,
    ]
    return {
        "status": "created",
        "name": normalized_name,
        "description": normalized_description,
        "skill_dir": str(skill_dir),
        "path": str(skill_file.relative_to(skill_root)),
        "relative_path": str(skill_file.relative_to(skill_root)),
        "directories": [
            str(references_dir),
            str(scripts_dir),
        ],
        "created_paths": [str(created_path) for created_path in created_paths],
        "summary": "Skill package frame created.",
    }


def _search_tokens(text: str) -> set[str]:
    """Return normalized lexical tokens used by the skill fallback search."""
    return {token for token in re.findall(r"[a-z0-9]+", text.lower()) if len(token) >= 2}


def _resolve_search_path(
    *,
    root_dir: Path,
    path: str,
) -> Path:
    """Resolve one search path from the current workspace or an absolute path."""
    cleaned_path = path.strip() or "."
    candidate_path = Path(cleaned_path).expanduser()
    if not candidate_path.is_absolute():
        candidate_path = root_dir / candidate_path
    candidate_path = candidate_path.resolve()
    return candidate_path


def _resolve_skill_root(
    *,
    search_path: Path,
    root_dir: Path,
) -> Path:
    """Choose the relative-path root for one skill search path."""
    try:
        search_path.relative_to(root_dir)
    except ValueError:
        return search_path if search_path.is_dir() else search_path.parent
    return root_dir


def _discover_skill_files_for_path(path: str) -> tuple[list[SkillFile], Path]:
    """Return discovered skill entries plus the root used for relative skill paths."""
    root_dir = Path.cwd().resolve()
    search_path = _resolve_search_path(
        root_dir=root_dir,
        path=path,
    )
    skill_root = _resolve_skill_root(
        search_path=search_path,
        root_dir=root_dir,
    )
    return _discover_skill_files(search_path, root_dir=skill_root), skill_root


def _skill_search_signature(skill_files: list[SkillFile]) -> tuple[tuple[str, int, int], ...]:
    """Return one stable cache signature for the current skill files."""
    return tuple(
        (
            skill_file.relative_path,
            skill_file.path.stat().st_mtime_ns,
            skill_file.path.stat().st_size,
        )
        for skill_file in skill_files
    )


def _iter_skill_file_paths(search_path: Path) -> list[Path]:
    """Return stable SKILL.md paths under one rooted search path."""
    if search_path.is_file():
        return [search_path] if search_path.name == SKILL_FILENAME else []

    discovered_paths: list[Path] = []
    for current_root, dir_names, file_names in os.walk(search_path):
        dir_names[:] = sorted(name for name in dir_names if name not in IGNORED_DIR_NAMES)
        for file_name in sorted(file_names):
            if file_name == SKILL_FILENAME:
                discovered_paths.append(Path(current_root) / file_name)
    return discovered_paths


def _parse_skill_file(
    path: Path,
    *,
    root_dir: Path,
) -> SkillFile:
    """Read one skill file and return parsed frontmatter plus markdown body."""
    text = path.read_text(encoding="utf-8")
    frontmatter, body = _split_frontmatter(text)
    return SkillFile(
        path=path,
        relative_path=str(path.relative_to(root_dir)),
        skill_root=path.parent,
        frontmatter=frontmatter,
        body=body,
    )


def _discover_skill_files(
    search_path: Path,
    *,
    root_dir: Path,
) -> list[SkillFile]:
    """Return parsed skill files under one rooted search path."""
    return [
        _parse_skill_file(
            path,
            root_dir=root_dir,
        )
        for path in _iter_skill_file_paths(search_path)
    ]


def _split_frontmatter(
    text: str,
) -> tuple[
    dict[str, Any],
    str,
]:
    """Return parsed YAML frontmatter plus the remaining markdown body."""
    if not text.startswith("---\n"):
        return {}, text

    end_marker = text.find("\n---\n", 4)
    if end_marker == -1:
        return {}, text

    frontmatter_text = text[4:end_marker]
    body = text[end_marker + 5 :]
    try:
        parsed_frontmatter = yaml.safe_load(frontmatter_text) or {}
    except yaml.YAMLError:
        return {}, body
    if not isinstance(parsed_frontmatter, dict):
        return {}, body
    return parsed_frontmatter, body


def _iter_resource_paths(skill_root: Path, resource_dir_name: str) -> list[Path]:
    """Return stable file paths under one optional skill resource directory."""
    resource_root = skill_root / resource_dir_name
    if not resource_root.is_dir():
        return []

    discovered_paths: list[Path] = []
    for current_root, dir_names, file_names in os.walk(resource_root):
        dir_names[:] = sorted(name for name in dir_names if name not in IGNORED_DIR_NAMES)
        for file_name in sorted(file_names):
            discovered_paths.append(Path(current_root) / file_name)
    return discovered_paths


def _skill_metadata(
    skill_file: SkillFile,
) -> tuple[
    dict[str, Any] | None,
    str | None,
]:
    """Build one standards-aligned metadata payload for a skill file."""
    skill_name = skill_file.frontmatter.get("name")
    description = skill_file.frontmatter.get("description")

    if not isinstance(skill_name, str) or not skill_name.strip():
        return None, f"{skill_file.relative_path}: missing required frontmatter field 'name'"
    if not SKILL_NAME_PATTERN.fullmatch(skill_name) or skill_name.startswith("-") or skill_name.endswith("-") or "--" in skill_name:
        return None, f"{skill_file.relative_path}: invalid skill name '{skill_name}'"
    if skill_file.skill_root.name != skill_name:
        return None, f"{skill_file.relative_path}: skill name '{skill_name}' must match parent directory '{skill_file.skill_root.name}'"
    if not isinstance(description, str) or not description.strip():
        return None, f"{skill_file.relative_path}: missing required frontmatter field 'description'"

    return {
        "path": skill_file.relative_path,
        "name": skill_name,
        "description": description,
    }, None


def _read_text_file(
    path: Path,
    *,
    max_chars: int,
) -> str:
    """Return UTF-8 text content for one file within the configured bound."""
    text = path.read_text(encoding="utf-8")
    if len(text) > max_chars:
        raise ValueError(f"Skill resource exceeds max supported size: {path}")
    return text


def _validate_bounded_text(text: str, *, path: Path, max_chars: int, label: str) -> str:
    """Return text when it fits within the configured bound."""
    if len(text) > max_chars:
        raise ValueError(f"{label} exceeds max supported size: {path}")
    return text


def _load_reference_group(
    paths: list[Path],
    *,
    root_dir: Path,
    max_chars_per_file: int,
) -> list[SkillReference]:
    """Load structured text references for one resource group."""
    return [
        SkillReference(
            path=str(path),
            relative_path=str(path.relative_to(root_dir)),
            kind="sql" if path.suffix.lower() == ".sql" else "text",
            content=_read_text_file(
                path,
                max_chars=max_chars_per_file,
            ),
        )
        for path in paths
    ]


def _load_script_group(
    paths: list[Path],
    *,
    root_dir: Path,
    max_chars_per_file: int,
) -> list[SkillScript]:
    """Load structured script text for one resource group."""
    return [
        SkillScript(
            path=str(path),
            relative_path=str(path.relative_to(root_dir)),
            content=_read_text_file(
                path,
                max_chars=max_chars_per_file,
            ),
        )
        for path in paths
    ]


def _load_skill_entry(
    skill_file: SkillFile,
    *,
    root_dir: Path,
    max_chars_per_file: int,
) -> tuple[dict[str, Any] | None, str | None]:
    """Load one skill entry with instructions plus scripts and references."""
    metadata, error_message = _skill_metadata(skill_file)
    if metadata is None:
        return None, error_message

    scripts = _iter_resource_paths(skill_file.skill_root, "scripts")
    examples = _iter_resource_paths(skill_file.skill_root, "examples")
    references = _iter_resource_paths(skill_file.skill_root, "references")
    try:
        loaded_skill = LoadedSkill(
            path=metadata["path"],
            name=metadata["name"],
            description=metadata["description"],
            instructions=SkillInstructions(
                path=str(skill_file.path),
                relative_path=metadata["path"],
                content=_validate_bounded_text(
                    skill_file.body,
                    path=skill_file.path,
                    max_chars=max_chars_per_file,
                    label="Skill instructions",
                ),
            ),
            examples=_load_script_group(
                examples,
                root_dir=root_dir,
                max_chars_per_file=max_chars_per_file,
            ),
            references=_load_reference_group(
                references,
                root_dir=root_dir,
                max_chars_per_file=max_chars_per_file,
            ),
            scripts=_load_script_group(
                scripts,
                root_dir=root_dir,
                max_chars_per_file=max_chars_per_file,
            ),
        )
    except ValueError as exc:
        return None, str(exc)

    return asdict(loaded_skill), None


def _matching_skill_files(
    skill_files: list[SkillFile],
    names: list[str],
) -> tuple[list[SkillFile], list[str]]:
    """Return selected skill entries plus diagnostics for unknown names."""
    if not names:
        return skill_files, []

    skill_files_by_name: dict[str, SkillFile] = {}
    diagnostics: list[str] = []
    for skill_file in skill_files:
        metadata, error_message = _skill_metadata(skill_file)
        if metadata is not None:
            skill_files_by_name[metadata["name"]] = skill_file
        elif error_message is not None:
            diagnostics.append(error_message)

    selected_skill_files: list[SkillFile] = []
    for name in names:
        matched_skill_file = skill_files_by_name.get(name)
        if matched_skill_file is None:
            diagnostics.append(f"Skill not found: {name}")
            continue
        selected_skill_files.append(matched_skill_file)
    return selected_skill_files, diagnostics


def _result_payload(
    skills: list[dict[str, Any]],
    diagnostics: list[str],
) -> dict[str, Any]:
    """Build the shared result shape for skills tools."""
    result: dict[str, Any] = {
        "status": "ok",
        "skills": skills,
    }
    if diagnostics:
        result["diagnostics"] = diagnostics
    return result


@tool(parse_docstring=True)
def create_skill_package(
    name: str,
    description: str,
    path: str = "skills",
    reference_files: list[str] | None = None,
    script_files: list[str] | None = None,
) -> dict[str, Any]:
    """Create a deterministic skill package frame for later scoped edits.

    Args:
        name: Kebab-case skill package name. It must match the created folder name.
        description: Frontmatter routing description written at the top of SKILL.md.
        path: Skills root directory relative to the current working directory, or an absolute path.
        reference_files: Optional starter file names created under references/. Use .sql for SQL reference frames.
        script_files: Optional starter file names created under scripts/.
    """

    return create_skill_package_frame(
        path=path,
        name=name,
        description=description,
        reference_files=reference_files,
        script_files=script_files,
    )


def _searchable_skill_entry(skill_file: SkillFile) -> tuple[tuple[dict[str, Any], str] | None, str | None]:
    """Return one searchable metadata payload plus embedding text."""
    metadata, error_message = _skill_metadata(skill_file)
    if metadata is None:
        return None, error_message

    aliases = skill_file.frontmatter.get("aliases")
    alias_text = ", ".join(alias for alias in aliases if isinstance(alias, str) and alias.strip()) if isinstance(aliases, list) else ""
    tags = skill_file.frontmatter.get("tags")
    tag_text = ", ".join(tag for tag in tags if isinstance(tag, str) and tag.strip()) if isinstance(tags, list) else ""
    parts = [
        f"Description: {metadata['description']}",
        f"Skill: {metadata['name']}",
    ]
    if alias_text:
        parts.append(f"Aliases: {alias_text}")
    if tag_text:
        parts.append(f"Tags: {tag_text}")
    return (metadata, "\n".join(parts)), None


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    """Return cosine similarity for two embedding vectors."""
    if not left or not right or len(left) != len(right):
        return 0.0

    dot_product = sum(left_value * right_value for left_value, right_value in zip(left, right, strict=False))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return dot_product / (left_norm * right_norm)


def _lexical_search_results(
    *,
    skill_files: list[SkillFile],
    query: str,
    top_k: int,
    score_threshold: float,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Return lexical skills matches when embedding search is unavailable."""
    diagnostics: list[str] = []
    query_tokens = _search_tokens(query)
    if not query_tokens:
        return [], diagnostics

    scored_results: list[dict[str, Any]] = []
    for skill_file in skill_files:
        searchable_entry, error_message = _searchable_skill_entry(skill_file)
        if searchable_entry is None:
            if error_message is not None:
                diagnostics.append(error_message)
            continue

        payload, search_text = searchable_entry
        text_tokens = _search_tokens(search_text)
        if not text_tokens:
            continue

        shared_tokens = query_tokens & text_tokens
        score = len(shared_tokens) / len(query_tokens)
        if score < score_threshold:
            continue
        scored_results.append(
            {
                **payload,
                "score": round(score, 6),
            }
        )

    scored_results.sort(key=lambda skill: (-float(skill["score"]), str(skill["name"])))
    return scored_results[: max(0, top_k)], diagnostics


def _build_skill_search_index(
    *,
    skill_files: list[SkillFile],
    skill_root: Path,
    embeddings: Embeddings,
) -> tuple[SkillSearchIndex, list[str]]:
    """Build or reuse one cached embedding index for the current skill files."""
    diagnostics: list[str] = []
    signature = _skill_search_signature(skill_files)
    cache_key = (str(skill_root), getattr(embeddings, "model", ""))
    cached_index = _SKILL_SEARCH_CACHE.get(cache_key)
    if cached_index is not None and cached_index.signature == signature:
        return cached_index, diagnostics

    searchable_entries: list[tuple[dict[str, Any], str]] = []
    for skill_file in skill_files:
        searchable_entry, error_message = _searchable_skill_entry(skill_file)
        if searchable_entry is not None:
            searchable_entries.append(searchable_entry)
        elif error_message is not None:
            diagnostics.append(error_message)

    if not searchable_entries:
        empty_index = SkillSearchIndex(signature=signature, entries=[])
        _SKILL_SEARCH_CACHE[cache_key] = empty_index
        return empty_index, diagnostics

    search_texts = [search_text for _, search_text in searchable_entries]
    vectors = embeddings.embed_documents(search_texts)
    entries = [
        SkillSearchEntry(
            payload=payload,
            embedding=[float(value) for value in vector],
        )
        for (payload, _), vector in zip(searchable_entries, vectors, strict=False)
    ]
    index = SkillSearchIndex(signature=signature, entries=entries)
    _SKILL_SEARCH_CACHE[cache_key] = index
    return index, diagnostics


@tool(parse_docstring=True)
def list_skills(
    path: str = ".",
    max_files: int = DEFAULT_MAX_FILES,
) -> dict[str, Any]:
    """List skills from the workspace.

    Args:
        path: Relative directory or file path from the current working directory, or an absolute path. Defaults to the current working directory.
        max_files: Maximum number of skills entries to return.
    """
    skill_files, _ = _discover_skill_files_for_path(path)
    bounded_files = skill_files[: max(0, max_files)]
    skills: list[dict[str, Any]] = []
    diagnostics: list[str] = []
    for skill_file in bounded_files:
        payload, error_message = _skill_metadata(skill_file)
        if payload is not None:
            skills.append(payload)
        elif error_message is not None:
            diagnostics.append(error_message)
    return _result_payload(skills, diagnostics)


@tool(parse_docstring=True)
def search_skills(
    query: str,
    path: str = ".",
    top_k: int = DEFAULT_SKILL_SEARCH_TOP_K,
    score_threshold: float = DEFAULT_SKILL_SEARCH_SCORE_THRESHOLD,
    search_mode: str = "lexical",
    model: str = DEFAULT_SKILL_SEARCH_MODEL,
) -> dict[str, Any]:
    """Search workspace skills semantically from descriptions and metadata only.

    Args:
        query: Natural-language task or question used to find relevant skills.
        path: Relative directory or file path from the current working directory, or an absolute path. Defaults to the current working directory.
        top_k: Maximum number of matching skills entries to return.
        score_threshold: Minimum cosine similarity score required to include a match.
        search_mode: Search strategy. Use "lexical" for token overlap or "embedding" for embedding similarity.
        model: Embedding model name understood by the configured OpenAI-compatible endpoint.
    """
    if not query.strip():
        return _result_payload([], ["Missing required query"])

    skill_files, skill_root = _discover_skill_files_for_path(path)
    normalized_search_mode = search_mode.strip().lower()
    if normalized_search_mode != "embedding":
        lexical_skills, diagnostics = _lexical_search_results(
            skill_files=skill_files,
            query=query,
            top_k=top_k,
            score_threshold=score_threshold,
        )
        result = _result_payload(lexical_skills, diagnostics)
        result["search_mode"] = "lexical"
        result["score_threshold"] = score_threshold
        return result

    try:
        embeddings = OpenAIEmbeddings(
            model=model,
            check_embedding_ctx_length=False,
        )
        index, diagnostics = _build_skill_search_index(
            skill_files=skill_files,
            skill_root=skill_root,
            embeddings=embeddings,
        )
        query_embedding = [float(value) for value in embeddings.embed_query(query)]
    except Exception as exc:
        lexical_skills, diagnostics = _lexical_search_results(
            skill_files=skill_files,
            query=query,
            top_k=top_k,
            score_threshold=score_threshold,
        )
        result = _result_payload(
            lexical_skills,
            [f"Embedding search unavailable: {exc}", "Used lexical fallback search.", *diagnostics],
        )
        result["search_mode"] = "lexical"
        result["model"] = model
        result["score_threshold"] = score_threshold
        return result

    bounded_top_k = max(0, top_k)
    scored_skills = []
    for entry in index.entries:
        score = _cosine_similarity(entry.embedding, [float(value) for value in query_embedding])
        if score < score_threshold:
            continue
        scored_skills.append(
            {
                **entry.payload,
                "score": round(score, 6),
            }
        )

    scored_skills.sort(key=lambda skill: float(skill["score"]), reverse=True)
    result = _result_payload(scored_skills[:bounded_top_k], diagnostics)
    result["indexed_skills"] = len(index.entries)
    result["model"] = model
    result["score_threshold"] = score_threshold
    return result


@tool(parse_docstring=True)
def load_skill(
    path: str = ".",
    skill: str = "",
) -> dict[str, Any]:
    """Load one selected skill entry from the workspace, including instructions, scripts, and references.

    Args:
        path: Relative directory or file path from the current working directory, or an absolute path. Defaults to the current working directory.
        skill: Skill entry name to load.
    """
    skill_files, skill_root = _discover_skill_files_for_path(path)
    if not skill.strip():
        return _result_payload([], ["Missing required skill"])

    selected_skill_files, diagnostics = _matching_skill_files(
        skill_files,
        names=[skill],
    )
    loaded_skills: list[dict[str, Any]] = []
    for skill_file in selected_skill_files[:1]:
        payload, error_message = _load_skill_entry(
            skill_file,
            root_dir=skill_root,
            max_chars_per_file=DEFAULT_MAX_CHARS_PER_FILE,
        )
        if payload is not None:
            loaded_skills.append(payload)
        elif error_message is not None:
            diagnostics.append(error_message)
    return _result_payload(loaded_skills, diagnostics)
