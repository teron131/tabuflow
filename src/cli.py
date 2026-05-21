"""Minimal command line wrappers around standalone Tabuflow tools."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

from .tools.artifacts import describe_artifact, list_artifacts, query_artifacts, save_artifact_view
from .tools.pdf import (
    DEFAULT_DPI,
    DEFAULT_INSPECT_PAGE_LIMIT,
    DEFAULT_INSPECT_TEXT_CHARS,
    DEFAULT_MAX_CONCURRENCY,
    DEFAULT_PAGES_PER_CHUNK,
    extract_pdf_file,
    inspect_pdf_file,
)
from .tools.tabular import MAX_METADATA_ROWS, MAX_SAMPLE_ROWS, extract_tabular_file, inspect_tabular_file, profile_tabular_file


def print_json(payload: Any) -> None:
    """Print one JSON payload for script-friendly consumption."""
    print(json.dumps(payload, indent=2, sort_keys=True, default=str))


def read_sql_argument(sql: str) -> str:
    """Return inline SQL or the text from an @file argument."""
    if not sql.startswith("@"):
        return sql
    return Path(sql[1:]).read_text(encoding="utf-8")


def add_tabular_commands(subparsers: Any) -> None:
    """Add CSV/XLSX inspection and extraction commands."""
    tabular = subparsers.add_parser("tabular", help="Inspect or extract CSV/XLSX files.")
    tabular_subparsers = tabular.add_subparsers(dest="tabular_command", required=True)

    inspect = tabular_subparsers.add_parser("inspect", help="Inspect a bounded raw grid window.")
    inspect.add_argument("path")
    inspect.add_argument("--start-row", type=int, default=1)
    inspect.add_argument("--limit", type=int, default=5)
    inspect.add_argument("--start-col", type=int, default=1)
    inspect.add_argument("--end-col", type=int, default=None)
    inspect.add_argument("--sheet", default=None)
    inspect.set_defaults(
        handler=lambda args: inspect_tabular_file(
            args.path,
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
    profile.set_defaults(
        handler=lambda args: profile_tabular_file(
            args.path,
            max_sample_rows=args.max_sample_rows,
            sheet=args.sheet,
        )
    )

    extract = tabular_subparsers.add_parser("extract", help="Extract tables into the shared SQLite cache.")
    extract.add_argument("path")
    extract.add_argument("--sample-rows", type=int, default=MAX_SAMPLE_ROWS)
    extract.add_argument("--metadata-rows", type=int, default=MAX_METADATA_ROWS)
    extract.add_argument("--sheet", default=None)
    extract.set_defaults(
        handler=lambda args: extract_tabular_file(
            args.path,
            sample_rows=args.sample_rows,
            metadata_rows=args.metadata_rows,
            sheet=args.sheet,
        )
    )


def add_pdf_commands(subparsers: Any) -> None:
    """Add PDF inspection and extraction commands."""
    pdf = subparsers.add_parser("pdf", help="Inspect or extract PDF files.")
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
            args.path,
            page_start=args.page_start,
            page_limit=args.page_limit,
            max_text_chars=args.max_text_chars,
            include_images=args.include_images,
            dpi=args.dpi,
        )
    )

    extract = pdf_subparsers.add_parser("extract", help="Extract visual tables into the shared SQLite cache.")
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
    artifact_subparsers = artifacts.add_subparsers(dest="artifact_command", required=True)

    query = artifact_subparsers.add_parser("query", help="Query prepared artifacts with read-only SQL. Prefix SQL with @ to read from a file.")
    query.add_argument("sql")
    query.add_argument("--max-rows", type=int, default=200)
    query.set_defaults(
        handler=lambda args: query_artifacts(
            read_sql_argument(args.sql),
            max_rows=args.max_rows,
        )
    )

    save = artifact_subparsers.add_parser("save-view", help="Save an artifact query as a named SQLite view.")
    save.add_argument("view_name")
    save.add_argument("sql")
    save.add_argument("--replace", action="store_true")
    save.set_defaults(
        handler=lambda args: save_artifact_view(
            read_sql_argument(args.sql),
            args.view_name,
            replace=args.replace,
        )
    )

    list_command = artifact_subparsers.add_parser("list", help="List queryable prepared artifacts.")
    list_command.add_argument("--include-internal", action="store_true")
    list_command.set_defaults(
        handler=lambda args: list_artifacts(
            include_internal=args.include_internal,
        )
    )

    describe = artifact_subparsers.add_parser("describe", help="Describe one queryable artifact.")
    describe.add_argument("name")
    describe.add_argument("--sample-rows", type=int, default=3)
    describe.add_argument("--text-value-hints", type=int, default=3)
    describe.set_defaults(
        handler=lambda args: describe_artifact(
            args.name,
            sample_rows=args.sample_rows,
            text_value_hints=args.text_value_hints,
        )
    )


def build_parser() -> argparse.ArgumentParser:
    """Build the Tabuflow command parser."""
    parser = argparse.ArgumentParser(
        prog="tabuflow",
        description="Inspect tabular/PDF sources and query prepared artifacts.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    add_tabular_commands(subparsers)
    add_pdf_commands(subparsers)
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
