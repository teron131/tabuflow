"""Tabular command registration."""

from __future__ import annotations

from typing import Any

from ...tabular import (
    MAX_METADATA_ROWS,
    MAX_SAMPLE_ROWS,
    extract_tabular_source,
    inspect_tabular_file,
    profile_tabular_source,
)
from ..paths import resolve_cli_path


def add_tabular_commands(subparsers: Any) -> None:
    """Add CSV/XLS/XLSX inspection and extraction commands."""
    tabular = subparsers.add_parser("tabular", help="Inspect or extract CSV/XLS/XLSX files.")
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
            resolve_cli_path(args.path),
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
    profile.set_defaults(
        handler=lambda args: profile_tabular_source(
            resolve_cli_path(args.path),
            max_sample_rows=args.max_sample_rows,
        )
    )

    extract = tabular_subparsers.add_parser("extract", help="Extract tables into the shared SQLite cache.")
    extract.add_argument("path")
    extract.add_argument("--metadata-rows", type=int, default=MAX_METADATA_ROWS)
    extract.set_defaults(
        handler=lambda args: extract_tabular_source(
            resolve_cli_path(args.path),
            metadata_rows=args.metadata_rows,
        )
    )
