"""Minimal filesystem tools for tool-calling.

Sandbox-oriented with root_dir + traversal protection.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import fnmatch
from pathlib import Path

from langchain.tools import tool
from langchain_core.tools import BaseTool

from .hashline import HashlineEdit, edit_hashline, format_hashline_text

PATH_TRAVERSAL_ERROR = "Path traversal not allowed"
PATH_OUTSIDE_ROOT_ERROR = "Path outside root"

type FSWritePredicate = Callable[[Path, Path], bool]
DEFAULT_WRITE_DENIED_MESSAGE = "Writes are only allowed for permitted workspace files."


@dataclass(frozen=True)
class SandboxFS:
    """Sandboxed filesystem wrapper with path traversal protection."""

    root_dir: Path

    def resolve(self, user_path: str) -> Path:
        """Resolve a relative path against the configured root."""
        cleaned_path = user_path.strip()
        if not cleaned_path:
            raise ValueError("Empty path")
        if cleaned_path.startswith("~"):
            raise ValueError(PATH_TRAVERSAL_ERROR)

        virtual_path = cleaned_path if cleaned_path.startswith("/") else f"/{cleaned_path}"
        if ".." in virtual_path:
            raise ValueError(PATH_TRAVERSAL_ERROR)

        resolved_path = (self.root_dir / virtual_path.lstrip("/")).resolve()
        try:
            resolved_path.relative_to(self.root_dir)
        except ValueError as e:
            raise ValueError(PATH_OUTSIDE_ROOT_ERROR) from e
        return resolved_path

    def require_file(self, path: str) -> Path:
        """Return a path only when it points to an existing file."""
        file_path = self.resolve(path)
        if not file_path.is_file():
            raise FileNotFoundError(f"File not found: {path}")
        return file_path

    def read_text(self, path: str) -> str:
        """Read text from a rooted file path."""
        return self.require_file(path).read_text(encoding="utf-8")

    def write_text(
        self,
        path: str,
        text: str,
    ) -> None:
        """Write text to a rooted file path."""
        file_path = self.resolve(path)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(text, encoding="utf-8")

    def read_hashline(self, path: str) -> str:
        """Read a hashline reference from a rooted file."""
        return format_hashline_text(self.read_text(path))

    def edit_hashline(self, path: str, edits: list[HashlineEdit]) -> str:
        """Edit one hashline range inside a rooted file."""
        original_text = self.read_text(path)
        updated_text = edit_hashline(original_text, edits)
        self.write_text(path, updated_text)
        return updated_text


def _rooted_files(
    fs: SandboxFS,
    path: str,
    glob_pattern: str,
    max_files: int,
) -> list[Path]:
    """Return matching files under one sandboxed directory or file path."""
    search_path = path.strip() or "."
    root_path = fs.resolve(search_path)
    if root_path.is_file():
        return [root_path]
    if not root_path.is_dir():
        raise FileNotFoundError(f"Directory not found: {search_path}")

    matches: list[Path] = []
    for candidate in root_path.rglob("*"):
        if not candidate.is_file():
            continue
        relative_path = candidate.relative_to(fs.root_dir).as_posix()
        if glob_pattern in {"*", "**/*"} or fnmatch.fnmatch(relative_path, glob_pattern) or fnmatch.fnmatch(candidate.name, glob_pattern):
            matches.append(candidate)
        if len(matches) >= max_files:
            break
    return matches


def _tool_path(
    fs: SandboxFS,
    file_path: Path,
) -> str:
    """Return a stable virtual path for a resolved sandbox file."""
    return file_path.relative_to(fs.root_dir).as_posix()


def allow_sql_or_skill_write(
    resolved_path: Path,
    relative_path: Path,
) -> bool:
    """Return whether a resolved sandbox path is an editable SQL or workspace skill resource."""
    is_sql_file = resolved_path.suffix.lower() == ".sql"
    is_skill_file = resolved_path.name == "SKILL.md" and relative_path.parts[:1] == ("skills",)
    is_skill_resource = len(relative_path.parts) >= 4 and relative_path.parts[0] == "skills" and relative_path.parts[2] in {"references", "scripts"}
    return is_sql_file or is_skill_file or is_skill_resource


def _require_write_allowed(
    fs: SandboxFS,
    path: str,
    can_write: FSWritePredicate | None,
    write_denied_message: str,
) -> None:
    """Raise when a write path fails the optional write predicate."""
    if can_write is None:
        return
    resolved_path = fs.resolve(path)
    relative_path = resolved_path.relative_to(fs.root_dir)
    if can_write(resolved_path, relative_path):
        return
    raise ValueError(write_denied_message)


def make_fs_tools(
    *,
    root_dir: str | Path,
    include_discovery: bool = False,
    include_write_text: bool = True,
    can_write: FSWritePredicate | None = None,
    write_denied_message: str = DEFAULT_WRITE_DENIED_MESSAGE,
) -> list[BaseTool]:
    """Create sandboxed filesystem tools for file operations.

    Args:
        root_dir: Sandbox root for all file operations.
        include_discovery: Include list/search read tools in addition to direct file reads.
        include_write_text: Include full-file writes. Hashline edit writes are always included.
        can_write: Optional predicate receiving `(resolved_path, relative_path)` for write/edit permission checks.
        write_denied_message: Error message raised when `can_write` rejects a path.
    """
    fs = SandboxFS(Path(root_dir).resolve())

    @tool(parse_docstring=True)
    def fs_list_files(
        path: str = ".",
        glob_pattern: str = "**/*",
        max_files: int = 200,
    ) -> list[str]:
        """List files under a sandboxed workspace path.

        Args:
            path: Directory path relative to the sandbox root, or virtual absolute like "/skills".
            glob_pattern: Optional shell-style file pattern, such as "*.sql", "**/*.md", or "SKILL.md".
            max_files: Maximum number of matching files to return.
        """

        safe_max_files = max(1, min(max_files, 500))
        files = _rooted_files(fs, path, glob_pattern, safe_max_files)
        return [_tool_path(fs, file_path) for file_path in files]

    @tool(parse_docstring=True)
    def fs_search_text(
        query: str,
        path: str = ".",
        glob_pattern: str = "**/*",
        max_matches: int = 50,
    ) -> list[dict[str, str | int]]:
        """Search UTF-8 files for literal text in the sandboxed workspace.

        Args:
            query: Literal text to search for.
            path: File or directory path relative to the sandbox root.
            glob_pattern: Optional shell-style file pattern, such as "*.sql", "**/*.md", or "SKILL.md".
            max_matches: Maximum number of line matches to return.
        """

        if not query:
            raise ValueError("Search query cannot be empty.")

        safe_max_matches = max(1, min(max_matches, 200))
        matches: list[dict[str, str | int]] = []
        for file_path in _rooted_files(fs, path, glob_pattern, max_files=1_000):
            try:
                text = file_path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            for line_number, line in enumerate(text.splitlines(), start=1):
                if query in line:
                    matches.append({"path": _tool_path(fs, file_path), "line": line_number, "text": line})
                if len(matches) >= safe_max_matches:
                    return matches
        return matches

    @tool(parse_docstring=True)
    def fs_read_text(path: str) -> str:
        """Read a UTF-8 text file from the sandboxed workspace.

        Args:
            path: File path relative to the sandbox root, or virtual absolute like "/foo.txt".
        """

        return fs.read_text(path)

    @tool(parse_docstring=True)
    def fs_write_text(
        path: str,
        text: str,
    ) -> str:
        """Write a UTF-8 text file into the sandboxed workspace.

        Creates parent directories as needed. When can_write is provided, writes are limited by that predicate.

        Args:
            path: File path relative to the sandbox root, or virtual absolute like "/out.txt".
            text: Full file contents.
        """

        _require_write_allowed(fs, path, can_write, write_denied_message)
        fs.write_text(path, text)
        return f"Wrote {path}"

    @tool(parse_docstring=True)
    def fs_read_hashline(path: str) -> str:
        """Read a UTF-8 text file rendered as `LINE#HASH:content` entries.

        Use this before fs_edit_hashline so edits have current anchors.

        Args:
            path: File path relative to the sandbox root, or virtual absolute like "/foo.txt".
        """

        return fs.read_hashline(path)

    @tool(parse_docstring=True)
    def fs_edit_hashline(
        path: str,
        edits: list[HashlineEdit],
    ) -> str:
        """Apply hashline edits to an existing UTF-8 text file.

        When can_write is provided, edits are limited by that predicate.
        Prefer full refs from fs_read_hashline such as `12#ab3f9d`; unique bare hash fragments are accepted as a recovery path.

        Args:
            path: File path relative to the sandbox root, or virtual absolute like "/foo.txt".
            edits: Hashline edit operations to apply to the file.
        """

        _require_write_allowed(fs, path, can_write, write_denied_message)
        return fs.edit_hashline(path, edits)

    tools: list[BaseTool] = []
    if include_discovery:
        tools.extend([fs_list_files, fs_search_text])
    tools.append(fs_read_text)
    if include_write_text:
        tools.append(fs_write_text)
    tools.extend([fs_read_hashline, fs_edit_hashline])
    return tools
