# PDF Strategy Policy

Choose strategies per visual table, grouped logical table, coordinate band, or text region. Do not choose one global strategy for the PDF unless inspection proves one repeated layout.

## Decision Tree

Start from the best candidate evidence available:

1. If `table_region_hints` gives a usable group, treat the group as a candidate.
2. If a per-page `table_detections` candidate is usable, treat it as a candidate.
3. If detector output is missing, suspicious, or generic, inspect `row_geometry` and overview images.
4. If layout is still ambiguous, render focused page images for only the relevant pages.
5. Use raw linear text only to confirm details or as a last fallback.

## Layout Patterns

Name the visual layout before choosing a strategy:

- `horizontal_grid`: normal rows and columns, often with headers across the top and one record per row.
- `borderless_aligned_grid`: normal rows and columns without visible ruling lines; alignment and repeated x positions carry the structure.
- `coordinate_banded_table`: cells are stable by x-range, but detector output is missing, noisy, or flattened.
- `vertical_label_value`: labels and values are stacked vertically, often one fact or amount per visual pair.
- `sectioned_amount_rows`: repeated sections or parent labels with amount rows underneath; context carry rules matter.
- `multi_page_continuation`: one logical table continues across page breaks and may repeat or drift headers.
- `prose_or_spec_blocks`: visually structured text that may look table-like but should not become rows unless fields are explicit.

Treat mixed PDFs as several layout regions, not one document-level layout.

## Horizontal Grid Tables

Use this path for normal rows-and-columns tables where fields run left to right:

- Prefer `tables detected` when PyMuPDF finds plausible headers and rows.
- Use `--strategy text` for borderless but aligned text tables.
- Use `--vertical-strategy text` or `--horizontal-strategy lines` when mixed ruling/text evidence matches the page.
- Use `--require-header` when generic columns such as `column_1` indicate detector noise.
- Use `--clip X0,Y0,X1,Y1` when page chrome, titles, legends, or footers are being captured as table cells.
- Use `--output-columns` when one logical table continues across pages but later chunks have header drift.
- Use `--merge-tables auto` by default; switch to `always` only when inspection shows one split logical table, and `never` when same-schema tables are visually separate.

If detected rows are plausible but cell layering is flattened or columns are generic, demote the detected strategy and inspect coordinate bands.

## Coordinate Tables

Use `tables coordinate` when row geometry shows stable x-bands and the detector is missing or unreliable.

Good signals:

- repeated x positions across many rows,
- stable required columns,
- labels or descriptions wrapping across multiple baselines,
- visual rows are clear in `row_geometry` or focused images,
- table cells are text-based but not detected as a grid.

Use `--required-columns` to anchor real rows. Use `--continuation-column` and tune `--anchor-y-slop` when a label or description wraps near adjacent rows.

## Vertical Label/Value Blocks

Use `tables field-value` or `tables line-value` when information is stacked vertically instead of laid out as a normal horizontal table.

Good signals:

- repeated labels such as charges, discounts, credits, tax, VAT, total, account, service, or section names,
- one value appears near or after each label,
- visual hierarchy matters more than column borders,
- pages with stacked amount lines and section/account context.

Use `line-value` for label lines paired with nearby value lines. Use `field-value` when known fields should be collected by name. Use section/context regexes to carry parent labels or account context, then split sections only when that context should become separate outputs.

## OCR Or Image-Assisted Recovery

Use visual or OCR evidence when text extraction is empty, obviously incomplete, or cannot preserve table hierarchy.

Do not feed all page images by default. Use 2x2 overview batches to choose pages, then render focused page images around ambiguous boundaries or scanned regions.

## Raw Linear Text

Raw linear text is always last. It is useful for exact spelling, punctuation, table titles, and confirming wrapped values, but it does not preserve table layers, visual hierarchy, or reliable cell boundaries.

Never let raw text outrank a usable structured candidate unless every structured candidate is absent or visibly wrong.
