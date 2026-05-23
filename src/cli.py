"""Minimal command line wrappers around standalone Tabuflow tools."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

from .tools.artifacts import (
    DEFAULT_CLI_ARTIFACT_LIST_LIMIT,
    SQL_ARTIFACT_LIST_DETAILS,
    artifacts_from_source,
    describe_sql_artifact,
    list_sql_artifacts,
    query_artifacts,
    save_artifact_view,
    suggest_sql_artifacts,
)
from .tools.mail import inspect_email_file
from .tools.pdf import (
    DEFAULT_DPI,
    DEFAULT_INSPECT_PAGE_LIMIT,
    DEFAULT_INSPECT_TEXT_CHARS,
    DEFAULT_MAX_CONCURRENCY,
    DEFAULT_PAGES_PER_CHUNK,
    extract_pdf_file,
    inspect_pdf_file,
)
from .tools.tabular import (
    MAX_METADATA_ROWS,
    MAX_SAMPLE_ROWS,
    extract_tabular_file,
    inspect_tabular_file,
    profile_tabular_file,
    profile_tabular_workbook_sheets,
)


def print_json(payload: Any) -> None:
    """Print one JSON payload for script-friendly consumption."""
    print(json.dumps(payload, indent=2, sort_keys=True, default=str))


def read_sql_argument(
    sql: str,
    *,
    root_dir: Path | None = None,
) -> str:
    """Return inline SQL or the text from an @file argument."""
    if not sql.startswith("@"):
        return sql
    sql_path = Path(sql[1:]).expanduser()
    if not sql_path.is_absolute() and root_dir is not None:
        sql_path = root_dir / sql_path
    return sql_path.read_text(encoding="utf-8")


def resolve_cli_root(args: argparse.Namespace) -> Path | None:
    """Resolve the optional CLI workspace root."""
    root_dir = getattr(args, "root_dir", None)
    if root_dir is None:
        return None
    return Path(root_dir).expanduser().resolve()


def resolve_cli_path(
    path: str,
    args: argparse.Namespace,
) -> Path:
    """Resolve a source path against the optional CLI workspace root."""
    source_path = Path(path).expanduser()
    if source_path.is_absolute():
        return source_path.resolve()
    root_dir = resolve_cli_root(args)
    if root_dir is None:
        return source_path
    return (root_dir / source_path).resolve()


def resolve_cli_database_path(args: argparse.Namespace) -> Path | None:
    """Resolve the optional artifact database path against the CLI workspace root."""
    database_path = getattr(args, "database_path", None)
    if database_path is None:
        return None
    path = Path(database_path).expanduser()
    if path.is_absolute():
        return path.resolve()
    root_dir = resolve_cli_root(args)
    if root_dir is None:
        return path
    return (root_dir / path).resolve()


def add_root_argument(parser: argparse.ArgumentParser) -> None:
    """Add the shared workspace-root argument."""
    parser.add_argument("--root-dir", default=None, help="Workspace root for relative source paths and the default artifact database.")


def add_tabular_commands(subparsers: Any) -> None:
    """Add CSV/XLS/XLSX inspection and extraction commands."""
    tabular = subparsers.add_parser("tabular", help="Inspect or extract CSV/XLS/XLSX files.")
    add_root_argument(tabular)
    tabular_subparsers = tabular.add_subparsers(dest="tabular_command", required=True)

    inspect = tabular_subparsers.add_parser("inspect", help="Inspect a bounded raw grid window.")
    inspect.add_argument("path")
    inspect.add_argument("--start-row", type=int, default=1)
    inspect.add_argument("--limit", type=int, default=20)
    inspect.add_argument("--start-col", type=int, default=1)
    inspect.add_argument("--end-col", type=int, default=None)
    inspect.add_argument("--sheet", default=None)
    inspect.set_defaults(
        handler=lambda args: inspect_tabular_file(
            resolve_cli_path(args.path, args),
            start_row=args.start_row,
            limit=args.limit,
            start_col=args.start_col,
            end_col=args.end_col,
            sheet=args.sheet,
        )
    )

    profile = tabular_subparsers.add_parser("profile", help="Profile file structure without extraction.")
    profile.add_argument("path")
    profile.add_argument("--max-sample-rows", type=int, default=MAX_SAMPLE_ROWS)
    profile.add_argument("--sheet", default=None)
    profile.add_argument("--all-sheets", action="store_true", help="Profile every worksheet in an XLS/XLSX workbook.")
    profile.set_defaults(
        handler=lambda args: (
            profile_tabular_workbook_sheets(
                resolve_cli_path(args.path, args),
                max_sample_rows=args.max_sample_rows,
            )
            if args.all_sheets
            else profile_tabular_file(
                resolve_cli_path(args.path, args),
                max_sample_rows=args.max_sample_rows,
                sheet=args.sheet,
            )
        )
    )

    extract = tabular_subparsers.add_parser("extract", help="Extract tables into the shared SQLite cache.")
    extract.add_argument("path")
    extract.add_argument("--metadata-rows", type=int, default=MAX_METADATA_ROWS)
    extract.add_argument("--sheet", default=None)
    extract.set_defaults(
        handler=lambda args: extract_tabular_file(
            args.path,
            root_dir=resolve_cli_root(args),
            metadata_rows=args.metadata_rows,
            sheet=args.sheet,
        )
    )


def add_pdf_commands(subparsers: Any) -> None:
    """Add PDF inspection and extraction commands."""
    pdf = subparsers.add_parser("pdf", help="Inspect or extract PDF files.")
    add_root_argument(pdf)
    pdf_subparsers = pdf.add_subparsers(dest="pdf_command", required=True)

    inspect = pdf_subparsers.add_parser("inspect", help="Inspect PDF text and optional page images.")
    inspect.add_argument("path")
    inspect.add_argument("--page-start", type=int, default=1)
    inspect.add_argument("--page-limit", type=int, default=DEFAULT_INSPECT_PAGE_LIMIT)
    inspect.add_argument("--max-text-chars", type=int, default=DEFAULT_INSPECT_TEXT_CHARS)
    inspect.add_argument("--include-images", action="store_true")
    inspect.add_argument("--dpi", type=int, default=96)
    inspect.set_defaults(
        handler=lambda args: inspect_pdf_file(
            resolve_cli_path(args.path, args),
            page_start=args.page_start,
            page_limit=args.page_limit,
            max_text_chars=args.max_text_chars,
            include_images=args.include_images,
            dpi=args.dpi,
        )
    )

    extract = pdf_subparsers.add_parser("extract", help="Extract PDF tables into the shared SQLite cache.")
    extract.add_argument("path")
    extract.add_argument("--model", default=None)
    extract.add_argument("--pages-per-chunk", type=int, default=DEFAULT_PAGES_PER_CHUNK)
    extract.add_argument("--max-concurrency", type=int, default=DEFAULT_MAX_CONCURRENCY)
    extract.add_argument("--dpi", type=int, default=DEFAULT_DPI)
    extract.add_argument("--max-chunks", type=int, default=None)
    extract.add_argument("--no-fix-bridges", action="store_true")
    extract.add_argument("--no-fix-overall", action="store_true")
    extract.add_argument("--no-markdown", action="store_true")
    extract.set_defaults(
        handler=lambda args: extract_pdf_file(
            args.path,
            root_dir=resolve_cli_root(args),
            model=args.model,
            pages_per_chunk=args.pages_per_chunk,
            max_concurrency=args.max_concurrency,
            dpi=args.dpi,
            max_chunks=args.max_chunks,
            fix_bridges=not args.no_fix_bridges,
            fix_overall=not args.no_fix_overall,
            write_markdown=not args.no_markdown,
        )
    )


def add_artifact_commands(subparsers: Any) -> None:
    """Add SQLite-backed artifact commands."""
    artifacts = subparsers.add_parser("artifacts", help="Inspect and query prepared artifacts.")
    add_root_argument(artifacts)
    artifacts.add_argument("--database-path", default=None, help="SQLite artifact database path. Relative paths resolve under --root-dir when provided.")
    artifact_subparsers = artifacts.add_subparsers(dest="artifact_command", required=True)

    query = artifact_subparsers.add_parser("query", help="Query prepared artifacts with read-only SQL. Prefix SQL with @ to read from a file.")
    query.add_argument("sql")
    query.add_argument("--max-rows", type=int, default=200)
    query.set_defaults(
        handler=lambda args: query_artifacts(
            root_dir=resolve_cli_root(args),
            sql=read_sql_argument(args.sql, root_dir=resolve_cli_root(args)),
            database_path=resolve_cli_database_path(args),
            max_rows=args.max_rows,
        )
    )

    save = artifact_subparsers.add_parser("save-view", help="Save an artifact query as a named SQLite view.")
    save.add_argument("view_name")
    save.add_argument("sql")
    save.add_argument("--replace", action="store_true")
    save.set_defaults(
        handler=lambda args: save_artifact_view(
            read_sql_argument(args.sql, root_dir=resolve_cli_root(args)),
            args.view_name,
            root_dir=resolve_cli_root(args),
            database_path=resolve_cli_database_path(args),
            replace=args.replace,
        )
    )

    list_command = artifact_subparsers.add_parser("list", help="List queryable prepared artifacts.")
    list_command.add_argument("--include-internal", action="store_true")
    list_command.add_argument("--max-items", type=int, default=DEFAULT_CLI_ARTIFACT_LIST_LIMIT)
    list_command.add_argument("--detail", choices=sorted(SQL_ARTIFACT_LIST_DETAILS), default="compact")
    list_command.add_argument("--all", action="store_true", help="Return the full artifact catalog.")
    list_command.set_defaults(
        handler=lambda args: list_sql_artifacts(
            root_dir=resolve_cli_root(args),
            database_path=resolve_cli_database_path(args),
            include_internal=args.include_internal,
            max_items=None if args.all else args.max_items,
            detail=args.detail,
        )
    )

    from_source = artifact_subparsers.add_parser("from-source", help="List artifacts created from one source file.")
    from_source.add_argument("path")
    from_source.add_argument("--source-format", default=None)
    from_source.add_argument("--include-internal", action="store_true")
    from_source.set_defaults(
        handler=lambda args: artifacts_from_source(
            str(resolve_cli_path(args.path, args)),
            root_dir=resolve_cli_root(args),
            database_path=resolve_cli_database_path(args),
            include_internal=args.include_internal,
            source_format=args.source_format,
        )
    )

    suggest = artifact_subparsers.add_parser("suggest", help="Suggest queryable artifacts for a natural-language question.")
    suggest.add_argument("question")
    suggest.add_argument("--max-results", type=int, default=5)
    suggest.add_argument("--include-internal", action="store_true")
    suggest.set_defaults(
        handler=lambda args: suggest_sql_artifacts(
            args.question,
            root_dir=resolve_cli_root(args),
            database_path=resolve_cli_database_path(args),
            include_internal=args.include_internal,
            max_results=args.max_results,
        )
    )

    describe = artifact_subparsers.add_parser("describe", help="Describe one queryable artifact.")
    describe.add_argument("name")
    describe.add_argument("--sample-rows", type=int, default=10)
    describe.add_argument("--text-value-hints", type=int, default=3)
    describe.set_defaults(
        handler=lambda args: describe_sql_artifact(
            args.name,
            root_dir=resolve_cli_root(args),
            database_path=resolve_cli_database_path(args),
            sample_rows=args.sample_rows,
            text_value_hints=args.text_value_hints,
        )
    )


def add_email_commands(subparsers: Any) -> None:
    """Add email reference inspection commands."""
    email = subparsers.add_parser("email", help="Inspect EML/MSG reference emails.")
    add_root_argument(email)
    email_subparsers = email.add_subparsers(dest="email_command", required=True)

    inspect = email_subparsers.add_parser("inspect", help="Inspect an email as reference context.")
    inspect.add_argument("path")
    inspect.add_argument("--max-body-chars", type=int, default=2000)
    inspect.set_defaults(
        handler=lambda args: inspect_email_file(
            resolve_cli_path(args.path, args),
            max_body_chars=args.max_body_chars,
        )
    )


def build_parser() -> argparse.ArgumentParser:
    """Build the Tabuflow command parser."""
    parser = argparse.ArgumentParser(
        prog="tabuflow",
        description="Inspect tabular/PDF/email sources and query prepared artifacts.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    add_tabular_commands(subparsers)
    add_pdf_commands(subparsers)
    add_email_commands(subparsers)
    add_artifact_commands(subparsers)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the Tabuflow CLI."""
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        payload = args.handler(args)
    except Exception as exc:
        print_json(
            {
                "status": "error",
                "error_type": exc.__class__.__name__,
                "message": str(exc),
            }
        )
        return 1
    print_json(payload)
    return 1 if isinstance(payload, dict) and payload.get("status") == "error" else 0


if __name__ == "__main__":
    sys.exit(main())
