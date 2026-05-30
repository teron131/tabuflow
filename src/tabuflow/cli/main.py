"""Tabuflow command-line entrypoint."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from ..doctor import doctor
from .commands.artifacts import add_artifacts_commands
from .commands.email import add_email_commands
from .commands.pdf import add_pdf_commands
from .commands.tabular import add_tabular_commands


def print_json(payload: Any) -> None:
    """Print one JSON payload for script-friendly consumption."""
    print(json.dumps(payload, indent=2, sort_keys=True, default=str))


def build_parser() -> argparse.ArgumentParser:
    """Build the Tabuflow command parser."""
    parser = argparse.ArgumentParser(
        prog="tabuflow",
        description="Inspect tabular/PDF/email sources and query prepared artifacts.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    doctor_parser = subparsers.add_parser("doctor", help="Check local Tabuflow tool dependencies.")
    doctor_parser.set_defaults(handler=lambda _: doctor())
    add_tabular_commands(subparsers)
    add_pdf_commands(subparsers)
    add_email_commands(subparsers)
    add_artifacts_commands(subparsers)
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
    if isinstance(payload, str):
        print(payload)
    else:
        print_json(payload)
    return 1 if isinstance(payload, dict) and payload.get("status") == "error" else 0


if __name__ == "__main__":
    sys.exit(main())
