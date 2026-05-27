"""Minimal FastMCP server for Tabuflow's standalone tools."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Literal

from fastmcp import FastMCP

from tabuflow.artifacts import artifacts_from_source, describe_sql_artifact, list_sql_artifacts, run_query, save_view
from tabuflow.cli.paths import read_sql_argument, resolve_cli_database_path, resolve_cli_path, resolve_cli_root, resolve_sql_argument_path
from tabuflow.cli.pdf_spec import pdf_extract_spec_from_args
from tabuflow.email import inspect_email_file
from tabuflow.pdf import DEFAULT_DPI, DEFAULT_INSPECT_PAGE_LIMIT, DEFAULT_INSPECT_TEXT_CHARS, DEFAULT_MAX_PREPARE_PAGES, extract_pdf_file, inspect_pdf_file, prepare_pdf_file
from tabuflow.tabular import MAX_METADATA_ROWS, MAX_SAMPLE_ROWS, extract_tabular_file, inspect_tabular_file, profile_tabular_file, profile_tabular_workbook_sheets

SERVER_NAME = "Tabuflow MCP"
SERVER_VERSION = "0.1.0"


def cli_args(
    *,
    root_dir: str | None = None,
    database_path: str | None = None,
    **values: Any,
) -> SimpleNamespace:
    """Build the small argparse-like object consumed by CLI helper functions."""
    return SimpleNamespace(root_dir=root_dir, database_path=database_path, **values)


def create_mcp_server() -> FastMCP:
    """Create the Tabuflow FastMCP server."""
    mcp = FastMCP(
        SERVER_NAME,
        version=SERVER_VERSION,
        instructions="Use Tabuflow to inspect messy business files, extract tables, and query SQLite-backed artifacts. Always pass root_dir as the active project/workspace directory when it is known; the MCP server process cwd may differ from the coding agent cwd. For CSV/XLS/XLSX analysis, inspect/profile first, then call tabular_extract to create <root_dir>/artifacts/tabular.sqlite before writing SQL or business-output scripts. Keep reusable SQL or scratch transformations under <root_dir>/artifacts/sql/<domain>/, validated deliverables under <root_dir>/artifacts/outputs/<domain>/, and source/example directories read-only. PDF workspaces are tool-owned under <root_dir>/artifacts/pdf/<source>/.",
    )

    @mcp.tool(name="health", description="Return Tabuflow MCP health information.")
    def health() -> dict[str, Any]:
        return {
            "status": "healthy",
            "server": SERVER_NAME,
            "version": SERVER_VERSION,
        }

    @mcp.tool(name="tabular_inspect", description="Inspect a bounded raw window from a CSV, XLS, or XLSX file.")
    def tabular_inspect(
        path: str,
        root_dir: str | None = None,
        start_row: int = 1,
        limit: int = 20,
        start_col: int = 1,
        end_col: int | None = None,
        sheet: str | None = None,
    ) -> dict[str, Any]:
        args = cli_args(root_dir=root_dir)
        return inspect_tabular_file(
            resolve_cli_path(path, args),
            start_row=start_row,
            limit=limit,
            start_col=start_col,
            end_col=end_col,
            sheet=sheet,
        )

    @mcp.tool(name="tabular_profile", description="Profile CSV, XLS, or XLSX structure without extracting tables.")
    def tabular_profile(
        path: str,
        root_dir: str | None = None,
        max_sample_rows: int = MAX_SAMPLE_ROWS,
        sheet: str | None = None,
        all_sheets: bool = False,
    ) -> dict[str, Any]:
        args = cli_args(root_dir=root_dir)
        source_path = resolve_cli_path(path, args)
        if all_sheets:
            return profile_tabular_workbook_sheets(
                source_path,
                max_sample_rows=max_sample_rows,
            )
        return profile_tabular_file(
            source_path,
            max_sample_rows=max_sample_rows,
            sheet=sheet,
        )

    @mcp.tool(
        name="tabular_extract",
        description="Extract tables from a CSV, XLS, or XLSX file into <root_dir>/artifacts/tabular.sqlite for later artifact queries.",
    )
    def tabular_extract(
        path: str,
        root_dir: str | None = None,
        metadata_rows: int = MAX_METADATA_ROWS,
        sheet: str | None = None,
    ) -> dict[str, Any]:
        return extract_tabular_file(
            path,
            root_dir=root_dir,
            metadata_rows=metadata_rows,
            sheet=sheet,
        )

    @mcp.tool(name="pdf_inspect", description="Inspect PDF profile evidence, selected page text, table detections, and overview artifacts.")
    def pdf_inspect(
        path: str,
        page_start: int = 1,
        page_limit: int = DEFAULT_INSPECT_PAGE_LIMIT,
        max_text_chars: int = DEFAULT_INSPECT_TEXT_CHARS,
        root_dir: str | None = None,
        dpi: int = DEFAULT_DPI,
    ) -> dict[str, Any]:
        return inspect_pdf_file(
            path,
            page_start=page_start,
            page_limit=page_limit,
            max_text_chars=max_text_chars,
            root_dir=root_dir,
            dpi=dpi,
        )

    @mcp.tool(name="pdf_prepare", description="Render a PDF into a resumable Tabuflow artifact workspace.")
    def pdf_prepare(
        path: str,
        root_dir: str | None = None,
        dpi: int = DEFAULT_DPI,
        max_pages: int | None = DEFAULT_MAX_PREPARE_PAGES,
    ) -> dict[str, Any]:
        return prepare_pdf_file(
            path,
            root_dir=root_dir,
            dpi=dpi,
            max_pages=max_pages,
        )

    @mcp.tool(name="pdf_extract", description="Extract PDF table artifacts with the same options as `tabuflow pdf extract`.")
    def pdf_extract(
        path: str,
        root_dir: str | None = None,
        target: Literal["tables"] | None = "tables",
        preset: Literal["detected", "coordinate", "field-value", "line-value"] | None = None,
        name: str | None = None,
        pages: str | None = None,
        page_start: int | None = None,
        page_end: int | None = None,
        include_page: bool = False,
        rules: str | None = None,
        skip_lines: list[str] | None = None,
        skip_prefixes: list[str] | None = None,
        stop_prefixes: list[str] | None = None,
        section: str | None = None,
        context: list[str] | None = None,
        clear_context: list[str] | None = None,
        split_by: str | None = None,
        split_sections: bool = False,
        drop_empty_split: bool = False,
        output_columns: str | None = None,
        min_rows: int = 1,
        min_filled_cells: int | None = None,
        strategy: Literal["lines", "lines-strict", "text"] | None = None,
        vertical_strategy: Literal["lines", "lines-strict", "text"] | None = None,
        horizontal_strategy: Literal["lines", "lines-strict", "text"] | None = None,
        clip: str | None = None,
        snap_tolerance: float | None = None,
        join_tolerance: float | None = None,
        intersection_tolerance: float | None = None,
        text_tolerance: float | None = None,
        edge_min_length: float | None = None,
        min_words_vertical: float | None = None,
        min_words_horizontal: float | None = None,
        require_header: bool = False,
        merge_tables: Literal["auto", "always", "never"] | None = None,
        value_preset: Literal["money", "number"] | None = None,
        value_pattern: str | None = None,
        label_column: str | None = None,
        value_column: str | None = None,
        field_column: str | None = None,
        fields: list[str] | None = None,
        collect_until_next_field: bool = False,
        columns: list[str] | None = None,
        y_min: float = 0,
        y_max: float = 10_000,
        y_tolerance: float = 4,
        anchor_y_slop: float | None = None,
        required_columns: str | None = None,
        continuation_column: str | None = None,
    ) -> dict[str, Any]:
        args = cli_args(
            root_dir=root_dir,
            target=target,
            preset=preset,
            name=name,
            pages=pages,
            page_start=page_start,
            page_end=page_end,
            include_page=include_page,
            rules=rules,
            skip_lines=skip_lines or [],
            skip_prefixes=skip_prefixes or [],
            stop_prefixes=stop_prefixes or [],
            section=section,
            context=context or [],
            clear_context=clear_context or [],
            split_by=split_by,
            split_sections=split_sections,
            drop_empty_split=drop_empty_split,
            output_columns=output_columns,
            min_rows=min_rows,
            min_filled_cells=min_filled_cells,
            strategy=strategy,
            vertical_strategy=vertical_strategy,
            horizontal_strategy=horizontal_strategy,
            clip=clip,
            snap_tolerance=snap_tolerance,
            join_tolerance=join_tolerance,
            intersection_tolerance=intersection_tolerance,
            text_tolerance=text_tolerance,
            edge_min_length=edge_min_length,
            min_words_vertical=min_words_vertical,
            min_words_horizontal=min_words_horizontal,
            require_header=require_header,
            merge_tables=merge_tables,
            value_preset=value_preset,
            value_pattern=value_pattern,
            label_column=label_column,
            value_column=value_column,
            field_column=field_column,
            fields=fields or [],
            collect_until_next_field=collect_until_next_field,
            columns=columns or [],
            y_min=y_min,
            y_max=y_max,
            y_tolerance=y_tolerance,
            anchor_y_slop=anchor_y_slop,
            required_columns=required_columns,
            continuation_column=continuation_column,
        )
        return extract_pdf_file(
            resolve_cli_path(path, args),
            extraction=pdf_extract_spec_from_args(args, root_dir=resolve_cli_root(args)),
            root_dir=resolve_cli_root(args),
        )

    @mcp.tool(name="email_inspect", description="Inspect an EML or MSG file as reference context.")
    def email_inspect(
        path: str,
        root_dir: str | None = None,
        max_body_chars: int = 4_000,
    ) -> dict[str, Any]:
        args = cli_args(root_dir=root_dir)
        return inspect_email_file(
            resolve_cli_path(path, args),
            max_body_chars=max_body_chars,
        )

    @mcp.tool(name="artifacts_list", description="List queryable SQLite-backed Tabuflow artifacts.")
    def artifacts_list(
        root_dir: str | None = None,
        database_path: str | None = None,
        include_internal: bool = False,
        max_items: int | None = 20,
        detail: Literal["compact", "full"] = "compact",
    ) -> dict[str, Any]:
        args = cli_args(root_dir=root_dir, database_path=database_path)
        return list_sql_artifacts(
            root_dir=resolve_cli_root(args),
            database_path=resolve_cli_database_path(args),
            include_internal=include_internal,
            max_items=max_items,
            detail=detail,
        )

    @mcp.tool(name="artifacts_from_source", description="Find queryable artifacts created from one source file.")
    def artifacts_lookup_from_source(
        path: str,
        root_dir: str | None = None,
        database_path: str | None = None,
        include_internal: bool = False,
        source_format: str | None = None,
    ) -> dict[str, Any]:
        args = cli_args(root_dir=root_dir, database_path=database_path)
        return artifacts_from_source(
            str(resolve_cli_path(path, args)),
            root_dir=resolve_cli_root(args),
            database_path=resolve_cli_database_path(args),
            include_internal=include_internal,
            source_format=source_format,
        )

    @mcp.tool(name="artifacts_describe", description="Describe one queryable SQLite table or view.")
    def artifacts_describe(
        name: str,
        root_dir: str | None = None,
        database_path: str | None = None,
        sample_rows: int = 10,
        text_value_hints: int = 3,
    ) -> dict[str, Any]:
        args = cli_args(root_dir=root_dir, database_path=database_path)
        return describe_sql_artifact(
            name,
            root_dir=resolve_cli_root(args),
            database_path=resolve_cli_database_path(args),
            sample_rows=sample_rows,
            text_value_hints=text_value_hints,
        )

    @mcp.tool(name="artifacts_query", description="Run bounded read-only SQL against Tabuflow's SQLite artifact database.")
    def artifacts_query(
        sql: str,
        root_dir: str | None = None,
        database_path: str | None = None,
        max_rows: int = 200,
    ) -> dict[str, Any]:
        args = cli_args(root_dir=root_dir, database_path=database_path)
        return run_query(
            read_sql_argument(sql, root_dir=resolve_cli_root(args)),
            root_dir=resolve_cli_root(args),
            database_path=resolve_cli_database_path(args),
            max_rows=max_rows,
        )

    @mcp.tool(name="artifacts_save_view", description="Save a read-only SQL query as a named SQLite view.")
    def artifacts_save_view(
        view_name: str,
        sql: str,
        root_dir: str | None = None,
        database_path: str | None = None,
        replace: bool = False,
    ) -> dict[str, Any]:
        args = cli_args(root_dir=root_dir, database_path=database_path)
        return save_view(
            read_sql_argument(sql, root_dir=resolve_cli_root(args)),
            view_name,
            root_dir=resolve_cli_root(args),
            database_path=resolve_cli_database_path(args),
            sql_file_path=resolve_sql_argument_path(sql, root_dir=resolve_cli_root(args)),
            replace=replace,
        )

    return mcp


def main() -> int:
    """Run the Tabuflow FastMCP server on stdio."""
    create_mcp_server().run("stdio")
    return 0
