# PDF Failure Modes

Use these checks before trusting extracted PDF tables.

## Missing Visual Validation

Do not accept, prune, merge, split, rename, or document PDF table artifacts from CSV output alone. Visual overview images are required to establish table identity, table count, and page-break behavior. Use focused page images for:

- low-confidence extraction output,
- generic columns such as `column_1` and `column_2`,
- one source concept that may contain several tables,
- one visual table split across pages,
- several visual tables on the same page,
- detector page tags or row counts that do not match the user's expectation,
- copied sample artifacts that need stable user-facing names.

CSV checks should verify extracted content after the visual table set is known.
They should not decide which tables exist.

## Detector False Positives

PyMuPDF `find_tables()` can detect prose, code snippets, legends, or one-cell fragments as tables. Reject or demote candidates with:

- one populated row or one populated column,
- many tiny header fragments,
- sparse cells,
- generic columns such as `column_1`, `column_2`,
- titles, sponsors, footers, or page chrome captured as rows.

When false positives are local to page chrome, use `--clip`. When the detector is generally weak, use coordinate or line/value strategies instead.

## Lost Table Layers

Raw text and some detector strategies can flatten visual structure:

- section headings become ordinary rows,
- child rows lose indentation,
- wrapped labels split into unrelated rows,
- repeated parent labels are not carried,
- columns drift after page breaks.

Use row geometry, coordinate bands, context regexes, continuation-column rules, or visual/OCR evidence when hierarchy matters.

## Multi-Page Continuations

Do not merge tables only because the schemas match. Check the 2x2 overview and source bboxes:

- `--merge-tables auto` is the default for same-schema chunks that look geometrically continuous.
- `--merge-tables always` is for one logical table split by the detector.
- `--merge-tables never` is for visually separate repeated tables.

When later-page headers drift but the logical schema is stable, pass `--output-columns` and required row filters.

Do not reject a table only because its filename spans pages, and do not accept a single table only because a filename names one page. Page tags are artifact names; visual layout decides whether a table spans pages or whether one page holds several separate tables.

## Vertical Amount Tables

Vertical amount PDFs are often not literal grids. They may be stacked amount rows where the useful columns are implied by nearby labels and section/account context.

Prefer `line-value` or `field-value` with section/context carry rules. Keep the raw amount-line table as evidence before reshaping it into accounting columns.

## Empty Or Low-Confidence Output

An `ok` shell exit is not enough. Inspect JSON status, `extraction_status`, diagnostics, row counts, columns, and sample rows.

Escalate when:

- status is `empty`,
- `extraction_status` is `low_confidence`,
- useful sections are missing,
- text layer is absent,
- rows are obviously too few for the page count,
- detector output contains generic columns or suspicious table names.

## Image Usage

Images are required evidence for PDF table acceptance, but they are not the default extraction input. Use 2x2 overview batches first, then focused full-page images for ambiguous pages, scanned pages, boundaries, repeated same-page tables, low-confidence outputs, or hierarchy.

Do not render or send every page image when cheap profile and text/geometry signals can narrow the target pages.
