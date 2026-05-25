"""Email command registration."""

from __future__ import annotations

from typing import Any

from ...email import inspect_email_file
from ..paths import add_root_argument, resolve_cli_path


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
