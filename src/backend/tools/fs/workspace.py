"""Workspace-file helpers built on top of the sandboxed filesystem."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from .fs_tools import SandboxFS
from .hashline import HashlineEdit, edit_hashline, format_hashline_text


@dataclass(frozen=True)
class WorkspaceFile:
    """Resolved workspace file path with its sandbox-relative user path."""

    user_path: str
    path: Path
    root_dir: Path

    @property
    def relative_path(self) -> str:
        """Return the workspace-relative path in portable form."""
        return self.path.relative_to(self.root_dir).as_posix()


def workspace_root(root_dir: str | Path | None = None) -> Path:
    """Return the absolute root that bounds workspace file operations."""
    return Path.cwd().resolve() if root_dir is None else Path(root_dir).expanduser().resolve()


def resolve_workspace_file(
    user_path: str | Path,
    *,
    root_dir: str | Path | None = None,
) -> WorkspaceFile:
    """Resolve one user path inside the bounded workspace filesystem."""
    root_path = workspace_root(root_dir)
    path_text = str(user_path)
    resolved_path = SandboxFS(root_path).resolve(path_text)
    return WorkspaceFile(
        user_path=path_text,
        path=resolved_path,
        root_dir=root_path,
    )


def read_workspace_text(workspace_file: WorkspaceFile) -> str:
    """Read UTF-8 text from a resolved workspace file."""
    if not workspace_file.path.is_file():
        raise FileNotFoundError(f"File not found: {workspace_file.user_path}")
    return workspace_file.path.read_text(encoding="utf-8")


def write_workspace_text(
    workspace_file: WorkspaceFile,
    text: str,
) -> None:
    """Write UTF-8 text to a resolved workspace file."""
    workspace_file.path.parent.mkdir(parents=True, exist_ok=True)
    workspace_file.path.write_text(text, encoding="utf-8")


def read_workspace_hashlines(workspace_file: WorkspaceFile) -> str:
    """Read one resolved workspace file as hashline-addressed text."""
    return format_hashline_text(read_workspace_text(workspace_file))


def edit_workspace_hashlines(
    workspace_file: WorkspaceFile,
    edits: Sequence[HashlineEdit],
) -> str:
    """Apply hashline edits to a resolved workspace file."""
    updated_text = edit_hashline(read_workspace_text(workspace_file), list(edits))
    write_workspace_text(workspace_file, updated_text)
    return updated_text


def replace_workspace_text(
    workspace_file: WorkspaceFile,
    text: str,
) -> None:
    """Replace a workspace file through hashline edits when the file exists."""
    if not workspace_file.path.exists():
        write_workspace_text(workspace_file, text)
        return

    refs = read_workspace_hashlines(workspace_file).splitlines()
    if not refs:
        write_workspace_text(workspace_file, text)
        return

    edit_workspace_hashlines(
        workspace_file,
        [
            HashlineEdit(
                operation="replace_range",
                start_ref=refs[0].split(":", maxsplit=1)[0],
                end_ref=refs[-1].split(":", maxsplit=1)[0],
                lines=text.splitlines(),
            )
        ],
    )
