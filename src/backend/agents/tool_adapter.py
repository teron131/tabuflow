"""LangChain tool adapters for the standalone Tabuflow tool layer."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from langchain.tools import tool
from langchain_core.tools import BaseTool

from tabuflow import create_skill_package as create_skill_package_core, load_skill as load_skill_core, search_skills as search_skills_core
from tabuflow.fs import (
    DEFAULT_WRITE_DENIED_MESSAGE,
    FSWritePredicate,
    HashlineEdit,
    SandboxFS,
    edit_hashline_text,
    list_files,
    search_text,
    write_text,
)
from tabuflow.fs.workspace import resolve_workspace_file
from tabuflow.pdf import (
    DEFAULT_DPI,
    DEFAULT_INSPECT_PAGE_LIMIT,
    DEFAULT_INSPECT_TEXT_CHARS,
    DEFAULT_MAX_PREPARE_PAGES,
    DEFAULT_PAGES_PER_CHUNK,
    extract_pdf_file,
    inspect_pdf_file,
    prepare_pdf_file,
)
from tabuflow.tabular import (
    MAX_METADATA_ROWS,
    MAX_SAMPLE_ROWS,
    extract_tabular_file,
    inspect_tabular_file,
    profile_tabular_file,
)
from tabuflow.tabular.storage import resolve_root_dir


def _workspace_source_path(
    path: str,
    *,
    root_dir: Path,
) -> Path:
    """Resolve one model-supplied source path inside the configured workspace."""
    return resolve_workspace_file(path, root_dir=root_dir).path


def make_tabular_tools(*, root_dir: str | Path | None = None) -> list[BaseTool]:
    """Create LangChain adapters for standalone tabular operations."""
    resolved_root_dir = resolve_root_dir(root_dir=root_dir)

    @tool(parse_docstring=True)
    def inspect_tabular(
        path: str,
        start_row: int = 1,
        limit: int = 5,
        start_col: int = 1,
        end_col: int | None = None,
        sheet: str | None = None,
    ) -> dict[str, Any]:
        """Inspect a CSV or XLSX file with a bounded raw grid window.

        Args:
            path: Path to the CSV or XLSX file to inspect.
            start_row: One-based row number where the preview window begins.
            limit: Maximum number of rows to return in the preview window.
            start_col: One-based column number where the preview window begins.
            end_col: Optional one-based column number where the preview window ends.
            sheet: Optional worksheet name for XLSX files. When omitted, the first sheet is used.
        """
        return inspect_tabular_file(
            _workspace_source_path(path, root_dir=resolved_root_dir),
            start_row=start_row,
            limit=limit,
            start_col=start_col,
            end_col=end_col,
            sheet=sheet,
        )

    @tool(parse_docstring=True)
    def profile_tabular(
        path: str,
        max_sample_rows: int = MAX_SAMPLE_ROWS,
        sheet: str | None = None,
    ) -> dict[str, Any]:
        """Profile a CSV or XLSX file with read-only structural hints.

        Args:
            path: Path to the CSV or XLSX file to profile.
            max_sample_rows: Maximum number of top rows to include in the profile sample.
            sheet: Optional worksheet name for XLSX files. When omitted, the first sheet is used.
        """
        return profile_tabular_file(
            _workspace_source_path(path, root_dir=resolved_root_dir),
            max_sample_rows=max_sample_rows,
            sheet=sheet,
        )

    @tool(parse_docstring=True)
    def extract_tabular(
        path: str,
        metadata_rows: int = MAX_METADATA_ROWS,
        sheet: str | None = None,
    ) -> dict[str, Any]:
        """Extract tables from a CSV or XLSX file into the shared SQLite cache.

        Args:
            path: Path to the CSV or XLSX file to extract.
            metadata_rows: Maximum number of metadata rows to inspect while preparing extraction.
            sheet: Optional worksheet name for XLSX files. When omitted, the first sheet is used.
        """
        return extract_tabular_file(
            _workspace_source_path(path, root_dir=resolved_root_dir),
            root_dir=resolved_root_dir,
            metadata_rows=metadata_rows,
            sheet=sheet,
        )

    return [inspect_tabular, profile_tabular, extract_tabular]


def make_pdf_tools(*, root_dir: str | Path | None = None) -> list[BaseTool]:
    """Create LangChain adapters for standalone PDF operations."""
    resolved_root_dir = resolve_root_dir(root_dir=root_dir)

    @tool(parse_docstring=True)
    def inspect_pdf(
        path: str,
        page_start: int = 1,
        page_limit: int = DEFAULT_INSPECT_PAGE_LIMIT,
        max_text_chars: int = DEFAULT_INSPECT_TEXT_CHARS,
        include_images: bool = False,
    ) -> dict[str, Any]:
        """Inspect a PDF with raw page text and optional rendered page images.

        Args:
            path: Path to the PDF file to inspect.
            page_start: One-based page number where inspection begins.
            page_limit: Maximum number of pages to inspect.
            max_text_chars: Maximum text characters to include per page.
            include_images: Whether to render inspected pages to image artifacts.
        """
        return inspect_pdf_file(
            _workspace_source_path(
                path,
                root_dir=resolved_root_dir,
            ),
            page_start=page_start,
            page_limit=page_limit,
            max_text_chars=max_text_chars,
            include_images=include_images,
        )

    @tool(parse_docstring=True)
    def prepare_pdf(
        path: str,
        dpi: int = DEFAULT_DPI,
        max_pages: int = DEFAULT_MAX_PREPARE_PAGES,
    ) -> dict[str, Any]:
        """Render every page of a PDF into a resumable artifact workspace.

        Args:
            path: Path to the PDF file to prepare.
            dpi: Rendering DPI for page images.
            max_pages: Safety cap on page count before rendering.
        """
        return prepare_pdf_file(
            _workspace_source_path(
                path,
                root_dir=resolved_root_dir,
            ),
            root_dir=resolved_root_dir,
            dpi=dpi,
            max_pages=max_pages,
        )

    @tool(parse_docstring=True)
    def extract_pdf(
        path: str,
        pages_per_chunk: int = DEFAULT_PAGES_PER_CHUNK,
        max_chunks: int | None = None,
        fix_bridges: bool = True,
        fix_overall: bool = True,
    ) -> dict[str, Any]:
        """Report the agent-managed PDF extraction boundary.

        Args:
            path: Path to the PDF file to extract.
            pages_per_chunk: Legacy option preserved for callers.
            max_chunks: Legacy option preserved for bounded trial callers.
            fix_bridges: Legacy option preserved for callers.
            fix_overall: Legacy option preserved for callers.
        """
        return extract_pdf_file(
            _workspace_source_path(
                path,
                root_dir=resolved_root_dir,
            ),
            root_dir=resolved_root_dir,
            pages_per_chunk=pages_per_chunk,
            max_chunks=max_chunks,
            fix_bridges=fix_bridges,
            fix_overall=fix_overall,
        )

    return [inspect_pdf, prepare_pdf, extract_pdf]


def make_fs_tools(
    *,
    root_dir: str | Path,
    include_discovery: bool = False,
    include_write_text: bool = True,
    can_write: FSWritePredicate | None = None,
    write_denied_message: str = DEFAULT_WRITE_DENIED_MESSAGE,
) -> list[BaseTool]:
    """Create LangChain adapters for standalone sandboxed filesystem operations."""
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
        return list_files(fs, path=path, glob_pattern=glob_pattern, max_files=max_files)

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
        return search_text(
            fs,
            query=query,
            path=path,
            glob_pattern=glob_pattern,
            max_matches=max_matches,
        )

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
        return write_text(
            fs=fs,
            path=path,
            text=text,
            can_write=can_write,
            write_denied_message=write_denied_message,
        )

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
        return edit_hashline_text(
            fs=fs,
            path=path,
            edits=edits,
            can_write=can_write,
            write_denied_message=write_denied_message,
        )

    tools: list[BaseTool] = []
    if include_discovery:
        tools.extend([fs_list_files, fs_search_text])
    tools.append(fs_read_text)
    if include_write_text:
        tools.append(fs_write_text)
    tools.extend([fs_read_hashline, fs_edit_hashline])
    return tools


def make_skill_tools(*, skills_path: str | Path = "skills") -> list[BaseTool]:
    """Create LangChain adapters for standalone workspace-skill operations."""
    resolved_skills_path = str(skills_path)

    @tool("create_skill_package", parse_docstring=True)
    def create_skill_package(
        name: str,
        description: str,
        reference_files: list[str] | None = None,
        script_files: list[str] | None = None,
    ) -> dict[str, Any]:
        """Create a deterministic skill package frame for later scoped edits.

        Args:
            name: Kebab-case skill package name. It must match the created folder name.
            description: Frontmatter routing description written at the top of SKILL.md.
            reference_files: Optional starter file names created under references/. Use .sql for SQL reference frames.
            script_files: Optional starter file names created under scripts/.
        """
        return create_skill_package_core(
            path=resolved_skills_path,
            name=name,
            description=description,
            reference_files=reference_files,
            script_files=script_files,
        )

    @tool("search_skills", parse_docstring=True)
    def search_skills(
        query: str,
        top_k: int = 5,
        score_threshold: float = 0.2,
        search_mode: str = "lexical",
        model: str = "text-embedding-3-small",
    ) -> dict[str, Any]:
        """Search workspace skills semantically from descriptions and metadata only.

        Args:
            query: Natural-language task or question used to find relevant skills.
            top_k: Maximum number of matching skills entries to return.
            score_threshold: Minimum similarity score required to include a match.
            search_mode: Search strategy. Use "lexical" for token overlap or "embedding" for embedding similarity.
            model: Embedding model name understood by the configured OpenAI-compatible endpoint.
        """
        return search_skills_core(
            query=query,
            path=resolved_skills_path,
            top_k=top_k,
            score_threshold=score_threshold,
            search_mode=search_mode,
            model=model,
        )

    @tool("load_skill", parse_docstring=True)
    def load_skill(
        skill: str = "",
    ) -> dict[str, Any]:
        """Load one selected skill entry from the workspace, including instructions, scripts, and references.

        Args:
            skill: Skill entry name to load.
        """
        return load_skill_core(
            path=resolved_skills_path,
            skill=skill,
        )

    return [create_skill_package, load_skill, search_skills]
