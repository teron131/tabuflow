"""Minimal filesystem tools for tool-calling.

Sandbox-oriented with root_dir + traversal protection.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from langchain.tools import tool

from .hashline import HashlineEdit, edit_hashline, format_hashline_text

PATH_TRAVERSAL_ERROR = "Path traversal not allowed"
PATH_OUTSIDE_ROOT_ERROR = "Path outside root"


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

    def write_text(self, path: str, text: str) -> None:
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


def make_fs_tools(*, root_dir: str | Path):
    """Create sandboxed filesystem tools for file operations."""
    fs = SandboxFS(Path(root_dir).resolve())

    @tool(parse_docstring=True)
    def fs_read_text(path: str) -> str:
        """Read a UTF-8 text file from the sandboxed workspace.

        Args:
            path: File path relative to the sandbox root (or virtual absolute like "/foo.txt").
        """

        return fs.read_text(path)

    @tool(parse_docstring=True)
    def fs_write_text(path: str, text: str) -> str:
        """Write a UTF-8 text file into the sandboxed workspace.

        Creates parent directories as needed.

        Args:
            path: File path relative to the sandbox root (or virtual absolute like "/out.txt").
            text: Full file contents.
        """

        fs.write_text(path, text)
        return f"Wrote {path}"

    @tool(parse_docstring=True)
    def fs_read_hashline(path: str) -> str:
        """Read a UTF-8 text file rendered as `LINE#HASH:content` entries.

        Args:
            path: File path relative to the sandbox root (or virtual absolute like "/foo.txt").
        """

        return fs.read_hashline(path)

    @tool(parse_docstring=True)
    def fs_edit_hashline(path: str, edits: list[HashlineEdit]) -> str:
        """Apply hashline edits to an existing UTF-8 text file.

        Args:
            path: File path relative to the sandbox root (or virtual absolute like "/foo.txt").
            edits: Hashline edit operations to apply to the file.
        """

        return fs.edit_hashline(path, edits)

    return [
        fs_read_text,
        fs_write_text,
        fs_read_hashline,
        fs_edit_hashline,
    ]
