"""LangChain tool adapters for reusable Tabuflow tools and backend agent helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from langchain.tools import tool
from langchain_core.tools import BaseTool

from tabuflow.pdf import (
    DEFAULT_DPI,
    DEFAULT_INSPECT_PAGE_LIMIT,
    DEFAULT_INSPECT_TEXT_CHARS,
    DEFAULT_MAX_PREPARE_PAGES,
    extract_pdf_file,
    inspect_pdf_file,
    prepare_pdf_file,
)
from tabuflow.tabular import (
    MAX_METADATA_ROWS,
    MAX_SAMPLE_ROWS,
    extract_tabular_source,
    inspect_tabular_file,
    profile_tabular_source,
)
from tabuflow.workspace_db import resolve_root_dir

from ..tools.fs import (
    DEFAULT_WRITE_DENIED_MESSAGE,
    FSWritePredicate,
    HashlineEdit,
    SandboxFS,
    edit_hashline_text,
    list_files,
    search_text,
    write_text,
)
from ..tools.fs.workspace import resolve_workspace_file
from ..tools.skills import (
    create_skill_package as create_skill_package_core,
    load_skill as load_skill_core,
    search_skills as search_skills_core,
)

PDF_TABLE_PRESET_MODES = {
    "detected": "pymupdf_tables",
    "coordinate": "coordinate_table",
    "field-value": "field_value",
    "line-value": "line_value",
}
PDF_VALUE_PRESETS = {
    "money": r"^-?[A-Z]{3}\s+[0-9][0-9,]*\.[0-9]{2}$",
    "number": r"^-?[0-9][0-9,]*(?:\.[0-9]+)?$",
}


def _workspace_source_path(
    path: str,
    *,
    root_dir: Path,
) -> Path:
    """Resolve one model-supplied source path inside the configured workspace."""
    return resolve_workspace_file(path, root_dir=root_dir).path


def _add_pdf_list_options(
    table_config: dict[str, Any],
    **options: list[Any] | None,
) -> None:
    """Add non-empty list options to one PDF extraction config."""
    for name, value in options.items():
        if value:
            table_config[name] = value


def _pdf_table_config(
    *,
    preset: str,
    name: str | None,
    pages: list[int] | None,
    page_start: int,
    page_end: int | None,
    skip_lines: list[str] | None,
    skip_prefixes: list[str] | None,
    stop_prefixes: list[str] | None,
    output_columns: list[str] | None,
    transpose_repeated_labels: str,
    min_rows: int,
    min_filled_cells: int | None,
    strategy: str | None,
    vertical_strategy: str | None,
    horizontal_strategy: str | None,
    clip: list[float] | None,
    detected_tuning_options: dict[str, float | None],
    require_header: bool,
    merge_tables: str,
    value_pattern: str | None,
    value_preset: str | None,
    label_column: str | None,
    value_column: str | None,
    field_column: str | None,
    fields: dict[str, str] | None,
    collect_until_next_field: bool,
    columns: list[dict[str, Any]] | None,
    required_columns: list[str] | None,
    y_min: float,
    y_max: float,
    y_tolerance: float,
    continuation_column: str | None,
    anchor_y_slop: float | None,
) -> dict[str, Any]:
    """Build an extraction config for the backend PDF tool adapter."""
    if preset not in PDF_TABLE_PRESET_MODES:
        raise ValueError(f"Unsupported PDF table preset: {preset}")
    table_config: dict[str, Any] = {
        "name": name or ("detected_tables" if preset == "detected" else "table"),
        "preset": preset,
        "mode": PDF_TABLE_PRESET_MODES[preset],
        "page_start": page_start,
    }
    if pages:
        table_config["pages"] = pages
    if page_end is not None:
        table_config["page_end"] = page_end
    _add_pdf_list_options(
        table_config,
        skip_lines=skip_lines,
        skip_prefixes=skip_prefixes,
        stop_prefixes=stop_prefixes,
        output_columns=output_columns,
    )

    if preset == "detected":
        table_config["min_rows"] = min_rows
        table_config["merge_tables"] = merge_tables
        if min_filled_cells is not None:
            table_config["min_filled_cells"] = min_filled_cells
        if strategy:
            table_config["vertical_strategy"] = strategy
            table_config["horizontal_strategy"] = strategy
        if vertical_strategy:
            table_config["vertical_strategy"] = vertical_strategy
        if horizontal_strategy:
            table_config["horizontal_strategy"] = horizontal_strategy
        if clip is not None:
            table_config["clip"] = clip
        for key, value in detected_tuning_options.items():
            if value is not None:
                table_config[key] = value
        if require_header:
            table_config["require_header"] = True
        return table_config

    if preset == "line-value":
        resolved_value_pattern = value_pattern or PDF_VALUE_PRESETS.get(str(value_preset))
        if not resolved_value_pattern:
            raise ValueError("line-value PDF extraction requires value_pattern or value_preset.")
        table_config.update(
            {
                "value_pattern": resolved_value_pattern,
                "label_column": label_column or "label",
                "value_column": value_column or "value",
            }
        )
        if transpose_repeated_labels:
            table_config["transpose_repeated_labels"] = transpose_repeated_labels
        if value_preset:
            table_config["value_preset"] = value_preset
        return table_config

    if preset == "field-value":
        if not fields:
            raise ValueError("field-value PDF extraction requires fields.")
        table_config.update(
            {
                "fields": fields,
                "field_column": field_column or "field",
                "value_column": value_column or "value",
                "collect_until_next_field": collect_until_next_field,
            }
        )
        return table_config

    if preset == "coordinate":
        if not columns:
            raise ValueError("coordinate PDF extraction requires columns.")
        table_config.update(
            {
                "columns": columns,
                "y_min": y_min,
                "y_max": y_max,
                "y_tolerance": y_tolerance,
            }
        )
        _add_pdf_list_options(table_config, required_columns=required_columns)
        if continuation_column:
            table_config["continuation_column"] = continuation_column
        if anchor_y_slop is not None:
            table_config["anchor_y_slop"] = anchor_y_slop
        return table_config

    raise ValueError(f"Unsupported PDF table preset: {preset}")


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
    ) -> dict[str, Any]:
        """Profile a CSV or XLSX file with read-only structural hints.

        Args:
            path: Path to the CSV or XLSX file to profile.
            max_sample_rows: Maximum number of top rows to include in the profile sample.
        """
        return profile_tabular_source(
            _workspace_source_path(path, root_dir=resolved_root_dir),
            max_sample_rows=max_sample_rows,
        )

    @tool(parse_docstring=True)
    def extract_tabular(
        path: str,
        metadata_rows: int = MAX_METADATA_ROWS,
    ) -> dict[str, Any]:
        """Extract tables from a CSV or XLSX file into the shared SQLite cache.

        Args:
            path: Path to the CSV or XLSX file to extract.
            metadata_rows: Maximum number of metadata rows to inspect while preparing extraction.
        """
        return extract_tabular_source(
            _workspace_source_path(path, root_dir=resolved_root_dir),
            root_dir=resolved_root_dir,
            metadata_rows=metadata_rows,
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
    ) -> dict[str, Any]:
        """Inspect a PDF with profile hints and selected 2x2 overview batches.

        Args:
            path: Path to the PDF file to inspect.
            page_start: One-based page number where optional detailed inspection begins.
            page_limit: Number of focused pages to inspect in detail; default 0 returns profile-only output.
            max_text_chars: Maximum text characters to include per page when page_limit is greater than 0.
        """
        return inspect_pdf_file(
            _workspace_source_path(
                path,
                root_dir=resolved_root_dir,
            ),
            root_dir=resolved_root_dir,
            page_start=page_start,
            page_limit=page_limit,
            max_text_chars=max_text_chars,
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
        preset: str = "detected",
        name: str | None = None,
        pages: list[int] | None = None,
        page_start: int = 1,
        page_end: int | None = None,
        skip_lines: list[str] | None = None,
        skip_prefixes: list[str] | None = None,
        stop_prefixes: list[str] | None = None,
        min_rows: int = 1,
        min_filled_cells: int | None = None,
        strategy: str | None = None,
        vertical_strategy: str | None = None,
        horizontal_strategy: str | None = None,
        clip: list[float] | None = None,
        snap_tolerance: float | None = None,
        join_tolerance: float | None = None,
        intersection_tolerance: float | None = None,
        text_tolerance: float | None = None,
        edge_min_length: float | None = None,
        min_words_vertical: float | None = None,
        min_words_horizontal: float | None = None,
        require_header: bool = False,
        merge_tables: str = "auto",
        output_columns: list[str] | None = None,
        transpose_repeated_labels: str = "auto",
        value_pattern: str | None = None,
        value_preset: str | None = None,
        label_column: str | None = None,
        value_column: str | None = None,
        field_column: str | None = None,
        fields: dict[str, str] | None = None,
        collect_until_next_field: bool = False,
        columns: list[dict[str, Any]] | None = None,
        required_columns: list[str] | None = None,
        y_min: float = 0,
        y_max: float = 10_000,
        y_tolerance: float = 4,
        continuation_column: str | None = None,
        anchor_y_slop: float | None = None,
    ) -> dict[str, Any]:
        """Extract PDF tables with a narrow PyMuPDF-backed preset.

        Args:
            path: Path to the PDF file to extract.
            preset: Extraction preset: detected, coordinate, field-value, or line-value.
            name: Optional output descriptor used only when page-tag filenames collide.
            pages: Optional explicit 1-based page numbers.
            page_start: First 1-based page to inspect.
            page_end: Last 1-based page to inspect.
            skip_lines: Exact cleaned text lines to skip.
            skip_prefixes: Cleaned text prefixes to skip.
            stop_prefixes: Cleaned text prefixes that stop page-line scanning.
            min_rows: Minimum detected rows for the detected preset.
            min_filled_cells: Optional minimum non-empty cells in a forced-column data row.
            strategy: Optional PyMuPDF strategy to use for both axes, such as text.
            vertical_strategy: Optional PyMuPDF vertical strategy for detected tables.
            horizontal_strategy: Optional PyMuPDF horizontal strategy for detected tables.
            clip: Optional X0,Y0,X1,Y1 clip rectangle in PDF points.
            snap_tolerance: Optional PyMuPDF snapping tolerance for nearby table edges.
            join_tolerance: Optional PyMuPDF joining tolerance for table edge segments.
            intersection_tolerance: Optional PyMuPDF tolerance for edge intersections.
            text_tolerance: Optional PyMuPDF tolerance for text-derived table edges.
            edge_min_length: Optional minimum edge length for detected-table geometry.
            min_words_vertical: Optional minimum words for text-derived vertical edges.
            min_words_horizontal: Optional minimum words for text-derived horizontal edges.
            require_header: Whether to skip detected tables without useful header metadata.
            merge_tables: Detected-table merge policy: auto, always, or never.
            output_columns: Optional fixed schema for continuing tables whose detected headers drift.
            transpose_repeated_labels: For line-value extraction, auto/always/never promote repeated amount labels into columns.
            value_pattern: Regex matching value lines for line-value extraction.
            value_preset: Built-in line-value regex preset, such as money or number.
            label_column: Output column for line-value labels.
            value_column: Output column for line-value or field-value values.
            field_column: Output column for field-value field labels.
            fields: Field/value mapping for field-value extraction.
            collect_until_next_field: Whether field-value extraction collects multiline values.
            columns: Coordinate extraction columns as name/x_min/x_max dictionaries.
            required_columns: Coordinate columns required for a row.
            y_min: Minimum y coordinate for coordinate extraction.
            y_max: Maximum y coordinate for coordinate extraction.
            y_tolerance: Visual row grouping tolerance.
            continuation_column: Coordinate column whose wrapped text joins anchored rows.
            anchor_y_slop: Y tolerance for attaching wrapped continuation text to anchors.
        """
        table_config = _pdf_table_config(
            preset=preset,
            name=name,
            pages=pages,
            page_start=page_start,
            page_end=page_end,
            skip_lines=skip_lines,
            skip_prefixes=skip_prefixes,
            stop_prefixes=stop_prefixes,
            output_columns=output_columns,
            transpose_repeated_labels=transpose_repeated_labels,
            min_rows=min_rows,
            min_filled_cells=min_filled_cells,
            strategy=strategy,
            vertical_strategy=vertical_strategy,
            horizontal_strategy=horizontal_strategy,
            clip=clip,
            detected_tuning_options={
                "snap_tolerance": snap_tolerance,
                "join_tolerance": join_tolerance,
                "intersection_tolerance": intersection_tolerance,
                "text_tolerance": text_tolerance,
                "edge_min_length": edge_min_length,
                "min_words_vertical": min_words_vertical,
                "min_words_horizontal": min_words_horizontal,
            },
            require_header=require_header,
            merge_tables=merge_tables,
            value_pattern=value_pattern,
            value_preset=value_preset,
            label_column=label_column,
            value_column=value_column,
            field_column=field_column,
            fields=fields,
            collect_until_next_field=collect_until_next_field,
            columns=columns,
            required_columns=required_columns,
            y_min=y_min,
            y_max=y_max,
            y_tolerance=y_tolerance,
            continuation_column=continuation_column,
            anchor_y_slop=anchor_y_slop,
        )
        return extract_pdf_file(
            _workspace_source_path(
                path,
                root_dir=resolved_root_dir,
            ),
            extraction={"tables": [table_config]},
            root_dir=resolved_root_dir,
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
    """Create LangChain adapters for agent-owned sandboxed filesystem operations."""
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
    """Create LangChain adapters for agent-owned workspace-skill operations."""
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
