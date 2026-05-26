"""PDF command registration."""

from __future__ import annotations

from typing import Any

from ...pdf import (
    DEFAULT_DPI,
    DEFAULT_INSPECT_PAGE_LIMIT,
    DEFAULT_INSPECT_TEXT_CHARS,
    DEFAULT_MAX_PREPARE_PAGES,
    extract_pdf_file,
    inspect_pdf_file,
    prepare_pdf_file,
)
from ..paths import add_root_argument, resolve_cli_path, resolve_cli_root
from ..pdf_spec import (
    PDF_TABLE_PRESET_MODES,
    PDF_TABLE_SCALAR_TUNING_OPTIONS,
    PDF_TABLE_STRATEGIES,
    PDF_VALUE_PRESETS,
    pdf_extract_spec_from_args,
)


def add_pdf_inspect_command(pdf_subparsers: Any) -> None:
    """Add the PDF text and image inspection command."""
    inspect = pdf_subparsers.add_parser("inspect", help="Inspect PDF profile hints, default 2x2 overview batches, row geometry, text, and optional page images.")
    inspect.add_argument("path")
    inspect.add_argument("--page-start", type=int, default=1)
    inspect.add_argument("--page-limit", type=int, default=DEFAULT_INSPECT_PAGE_LIMIT)
    inspect.add_argument("--max-text-chars", type=int, default=DEFAULT_INSPECT_TEXT_CHARS)
    inspect.add_argument("--include-images", action="store_true")
    inspect.add_argument("--dpi", type=int, default=DEFAULT_DPI)
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


def add_pdf_prepare_command(pdf_subparsers: Any) -> None:
    """Add the PDF artifact workspace preparation command."""
    prepare = pdf_subparsers.add_parser("prepare", help="Render a PDF into a resumable artifact workspace.")
    prepare.add_argument("path")
    prepare.add_argument("--dpi", type=int, default=DEFAULT_DPI)
    prepare.add_argument("--max-pages", type=int, default=DEFAULT_MAX_PREPARE_PAGES)
    prepare.set_defaults(
        handler=lambda args: prepare_pdf_file(
            args.path,
            root_dir=resolve_cli_root(args),
            dpi=args.dpi,
            max_pages=args.max_pages,
        )
    )


def add_pdf_extract_command(pdf_subparsers: Any) -> None:
    """Add the PDF table extraction command."""
    extract = pdf_subparsers.add_parser("extract", help="Extract PDF artifacts with narrow PyMuPDF-backed presets.")
    extract.add_argument("path")
    extract.add_argument("target", nargs="?", choices=["tables"], metavar="tables", help="Artifact kind to extract.")
    extract.add_argument(
        "preset",
        nargs="?",
        choices=sorted(PDF_TABLE_PRESET_MODES),
        metavar="{detected,coordinate,field-value,line-value}",
        help="PyMuPDF-backed extraction preset.",
    )
    extract.add_argument("--name", default=None)
    extract.add_argument("--pages", default=None, help="Comma-separated 1-based pages, for example 1,3,5.")
    extract.add_argument("--page-start", type=int, default=None)
    extract.add_argument("--page-end", type=int, default=None)
    extract.add_argument("--include-page", action="store_true")
    extract.add_argument("--rules", default=None, help="YAML rules file for reusable PDF extraction options.")
    extract.add_argument("--skip-line", dest="skip_lines", action="append", default=[])
    extract.add_argument("--skip-prefix", dest="skip_prefixes", action="append", default=[])
    extract.add_argument("--stop-prefix", dest="stop_prefixes", action="append", default=[])
    extract.add_argument("--section", default=None, help="tables line-value/field-value: regex for section heading lines; stored as the section context.")
    extract.add_argument(
        "--context",
        action="append",
        default=[],
        help="tables line-value/field-value: FIELD=REGEX. Matching lines update a carried context column.",
    )
    extract.add_argument(
        "--clear-context",
        action="append",
        default=[],
        help="tables line-value/field-value: FIELD=REGEX. Matching lines clear a carried context column.",
    )
    extract.add_argument("--split-by", default=None, help="tables line-value/field-value/coordinate: write one CSV per distinct FIELD value.")
    extract.add_argument("--split-sections", action="store_true", help="tables line-value/field-value: shortcut for --split-by section.")
    extract.add_argument("--drop-empty-split", action="store_true", help="tables split outputs: omit rows where the split field is empty.")
    extract.add_argument("--output-columns", default=None, help="Comma-separated output columns.")
    extract.add_argument("--min-rows", type=int, default=1, help="tables detected: minimum detected rows.")
    extract.add_argument("--min-filled-cells", type=int, default=None, help="tables detected: minimum non-empty cells in a forced-column data row.")
    extract.add_argument("--strategy", choices=PDF_TABLE_STRATEGIES, default=None, help="tables detected: set both PyMuPDF table strategies.")
    extract.add_argument("--vertical-strategy", choices=PDF_TABLE_STRATEGIES, default=None, help="tables detected: PyMuPDF vertical strategy.")
    extract.add_argument("--horizontal-strategy", choices=PDF_TABLE_STRATEGIES, default=None, help="tables detected: PyMuPDF horizontal strategy.")
    extract.add_argument("--clip", default=None, help="tables detected: clip rectangle X0,Y0,X1,Y1 in PDF points.")
    for option in PDF_TABLE_SCALAR_TUNING_OPTIONS:
        extract.add_argument(f"--{option.replace('_', '-')}", dest=option, type=float, default=None, help=f"tables detected: PyMuPDF {option.replace('_', ' ')}.")
    extract.add_argument("--require-header", action="store_true", help="tables detected: skip tables without useful PyMuPDF header metadata.")
    extract.add_argument(
        "--merge-tables",
        choices=["auto", "always", "never"],
        default=None,
        help="tables detected: merge same-schema detected tables by continuation geometry, always, or never.",
    )
    extract.add_argument("--value-preset", choices=sorted(PDF_VALUE_PRESETS), default=None, help="tables line-value: built-in value regex preset.")
    extract.add_argument("--value-pattern", default=None, help="tables line-value: regex matching value lines.")
    extract.add_argument("--label-column", default=None, help="tables line-value: output column for label text.")
    extract.add_argument("--value-column", default=None, help="tables line-value/field-value: output column for extracted values.")
    extract.add_argument("--field-column", default=None, help="tables field-value: output column for field names.")
    extract.add_argument("--field", dest="fields", action="append", default=[], help="tables field-value: FIELD=OUTPUT mapping. Repeat for each field.")
    extract.add_argument("--collect-until-next-field", action="store_true", help="tables field-value: collect multiline values until another configured field.")
    extract.add_argument("--column", dest="columns", action="append", default=[], help="tables coordinate: NAME:X_MIN:X_MAX. Repeat for each column.")
    extract.add_argument("--y-min", type=float, default=0)
    extract.add_argument("--y-max", type=float, default=10_000)
    extract.add_argument("--y-tolerance", type=float, default=4)
    extract.add_argument("--anchor-y-slop", type=float, default=None)
    extract.add_argument("--required-columns", default=None, help="tables coordinate: comma-separated columns required for a row.")
    extract.add_argument("--continuation-column", default=None, help="tables coordinate: column whose wrapped text joins anchored rows.")
    extract.set_defaults(
        handler=lambda args: extract_pdf_file(
            resolve_cli_path(args.path, args),
            extraction=pdf_extract_spec_from_args(args, root_dir=resolve_cli_root(args)),
            root_dir=resolve_cli_root(args),
        )
    )


def add_pdf_commands(subparsers: Any) -> None:
    """Add PDF inspection, preparation, and extraction-boundary commands."""
    pdf = subparsers.add_parser("pdf", help="Inspect or extract PDF files.")
    add_root_argument(pdf)
    pdf_subparsers = pdf.add_subparsers(dest="pdf_command", required=True)

    add_pdf_inspect_command(pdf_subparsers)
    add_pdf_prepare_command(pdf_subparsers)
    add_pdf_extract_command(pdf_subparsers)
