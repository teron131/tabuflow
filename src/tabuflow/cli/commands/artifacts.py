"""Artifact command registration."""

from __future__ import annotations

from typing import Any

from ...artifacts import (
    ARTIFACT_SEARCH_SCOPES,
    DEFAULT_ARTIFACT_SEARCH_MATCHES,
    DEFAULT_CLI_ARTIFACT_LIST_LIMIT,
    SQL_ARTIFACT_LIST_DETAILS,
    artifacts_from_source,
    describe_sql_artifact,
    format_artifact_map,
    format_artifact_search,
    list_sql_artifacts,
    map_artifacts,
    run_query,
    save_view,
    search_artifacts,
    suggest_sql_artifacts,
)
from ..paths import (
    read_sql_argument,
    resolve_cli_path,
    resolve_sql_argument_path,
)


def _handle_artifacts_map(args: Any) -> dict[str, Any] | str:
    """Return pretty map output, leaving error payloads as JSON."""
    payload = map_artifacts(include_internal=args.include_internal)
    if payload.get("status") == "error":
        return payload
    return format_artifact_map(payload)


def _handle_artifacts_search(args: Any) -> dict[str, Any] | str:
    """Return pretty search output by default, leaving JSON available for tools."""
    payload = search_artifacts(
        args.query,
        scope=args.scope,
        artifact=args.artifact,
        regex=args.regex,
        max_matches=args.max_matches,
        include_internal=args.include_internal,
        case_sensitive=args.case_sensitive,
    )
    if args.json or payload.get("status") == "error":
        return payload
    return format_artifact_search(payload)


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


def add_artifacts_map_command(artifacts_subparsers: Any) -> None:
    """Add the mixed artifact workspace map command."""
    map_command = artifacts_subparsers.add_parser("map", help="Trace input files to tables, SQL files, and result files.")
    map_command.add_argument("--include-internal", action="store_true")
    map_command.set_defaults(handler=_handle_artifacts_map)


def add_artifacts_search_command(artifacts_subparsers: Any) -> None:
    """Add the mixed artifact workspace search command."""
    search = artifacts_subparsers.add_parser("search", help="Search SQLite artifacts and managed artifact files.")
    search.add_argument("query")
    search.add_argument("--scope", choices=sorted(ARTIFACT_SEARCH_SCOPES), default="all")
    search.add_argument("--artifact", default=None, help="Limit SQLite row/metadata matches to one artifact name.")
    search.add_argument("--regex", action="store_true")
    search.add_argument("--case-sensitive", action="store_true", default=None)
    search.add_argument("--max-matches", type=int, default=DEFAULT_ARTIFACT_SEARCH_MATCHES)
    search.add_argument("--include-internal", action="store_true")
    search.add_argument("--json", action="store_true", help="Print the structured JSON payload instead of rg-like lines.")
    search.set_defaults(handler=_handle_artifacts_search)


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
    add_artifacts_map_command(artifacts_subparsers)
    add_artifacts_search_command(artifacts_subparsers)
    add_artifacts_list_command(artifacts_subparsers)
    add_artifacts_from_source_command(artifacts_subparsers)
    add_artifacts_suggest_command(artifacts_subparsers)
    add_artifacts_describe_command(artifacts_subparsers)
