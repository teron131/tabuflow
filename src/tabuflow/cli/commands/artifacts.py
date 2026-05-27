"""Artifact command registration."""

from __future__ import annotations

from typing import Any

from ...artifacts import (
    DEFAULT_CLI_ARTIFACT_LIST_LIMIT,
    SQL_ARTIFACT_LIST_DETAILS,
    artifacts_from_source,
    describe_sql_artifact,
    list_sql_artifacts,
    run_query,
    save_view,
    suggest_sql_artifacts,
)
from ..paths import (
    read_sql_argument,
    resolve_cli_path,
    resolve_sql_argument_path,
)


def add_artifacts_query_command(artifacts_subparsers: Any) -> None:
    """Add the read-only SQL artifact query command."""
    query = artifacts_subparsers.add_parser("query", help="Query prepared artifacts with read-only SQL. Prefix SQL with @ to read from a file.")
    query.add_argument("sql")
    query.add_argument("--max-rows", type=int, default=200)
    query.set_defaults(
        handler=lambda args: run_query(
            sql=read_sql_argument(args.sql),
            max_rows=args.max_rows,
        )
    )


def add_artifacts_save_view_command(artifacts_subparsers: Any) -> None:
    """Add the SQL saved-view command."""
    save = artifacts_subparsers.add_parser("save-view", help="Save an artifact query as a named SQLite view.")
    save.add_argument("view_name")
    save.add_argument("sql")
    save.add_argument("--replace", action="store_true")
    save.set_defaults(
        handler=lambda args: save_view(
            read_sql_argument(args.sql),
            args.view_name,
            sql_file_path=resolve_sql_argument_path(args.sql),
            replace=args.replace,
        )
    )


def add_artifacts_list_command(artifacts_subparsers: Any) -> None:
    """Add the SQL artifact listing command."""
    list_command = artifacts_subparsers.add_parser("list", help="List queryable prepared artifacts.")
    list_command.add_argument("--include-internal", action="store_true")
    list_command.add_argument("--max-items", type=int, default=DEFAULT_CLI_ARTIFACT_LIST_LIMIT)
    list_command.add_argument("--detail", choices=sorted(SQL_ARTIFACT_LIST_DETAILS), default="compact")
    list_command.add_argument("--all", action="store_true", help="Return the full artifact catalog.")
    list_command.set_defaults(
        handler=lambda args: list_sql_artifacts(
            include_internal=args.include_internal,
            max_items=None if args.all else args.max_items,
            detail=args.detail,
        )
    )


def add_artifacts_from_source_command(artifacts_subparsers: Any) -> None:
    """Add the source-to-artifacts lookup command."""
    from_source = artifacts_subparsers.add_parser("from-source", help="List artifacts created from one source file.")
    from_source.add_argument("path")
    from_source.add_argument("--source-format", default=None)
    from_source.add_argument("--include-internal", action="store_true")
    from_source.set_defaults(
        handler=lambda args: artifacts_from_source(
            str(resolve_cli_path(args.path)),
            include_internal=args.include_internal,
            source_format=args.source_format,
        )
    )


def add_artifacts_suggest_command(artifacts_subparsers: Any) -> None:
    """Add the natural-language artifact suggestion command."""
    suggest = artifacts_subparsers.add_parser("suggest", help="Suggest queryable artifacts for a natural-language question.")
    suggest.add_argument("question")
    suggest.add_argument("--max-results", type=int, default=5)
    suggest.add_argument("--include-internal", action="store_true")
    suggest.set_defaults(
        handler=lambda args: suggest_sql_artifacts(
            args.question,
            include_internal=args.include_internal,
            max_results=args.max_results,
        )
    )


def add_artifacts_describe_command(artifacts_subparsers: Any) -> None:
    """Add the SQL artifact description command."""
    describe = artifacts_subparsers.add_parser("describe", help="Describe one queryable artifact.")
    describe.add_argument("name")
    describe.add_argument("--sample-rows", type=int, default=10)
    describe.add_argument("--text-value-hints", type=int, default=3)
    describe.set_defaults(
        handler=lambda args: describe_sql_artifact(
            args.name,
            sample_rows=args.sample_rows,
            text_value_hints=args.text_value_hints,
        )
    )


def add_artifacts_commands(subparsers: Any) -> None:
    """Add SQLite-backed artifact commands."""
    artifacts = subparsers.add_parser("artifacts", help="Inspect and query prepared artifacts.")
    artifacts_subparsers = artifacts.add_subparsers(dest="artifact_command", required=True)

    add_artifacts_query_command(artifacts_subparsers)
    add_artifacts_save_view_command(artifacts_subparsers)
    add_artifacts_list_command(artifacts_subparsers)
    add_artifacts_from_source_command(artifacts_subparsers)
    add_artifacts_suggest_command(artifacts_subparsers)
    add_artifacts_describe_command(artifacts_subparsers)
