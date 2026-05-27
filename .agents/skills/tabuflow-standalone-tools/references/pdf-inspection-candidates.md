# PDF Inspection Candidates

Use `pdf inspect` to produce evidence for an extraction plan, not to declare the final table output. Treat each candidate or region as an independent decision unit.

## Candidate Surfaces

`table_region_hints` is the preferred candidate list when present after a focused page-window inspect. Each group is already a recommended unit with pages, source detections, likely columns, and repaired row previews.

`table_detections` is the raw per-page PyMuPDF table detector view. Use candidates where `interpretation.usable` is true and read `interpretation.kind`, `suggested_method`, `columns`, `rows`, and `diagnostics` before extracting. Ignore `false_positive` candidates instead of forcing them into tables.

`row_geometry` is compact row evidence for coordinate or line-based extraction. Use row text and bounding boxes when visual x-bands, wrapped labels, or nearby value lines are more reliable than detected table cells.

`overview_batches` are selected 2x2 page contact sheets chosen from layout-profile sample pages. Use them to find layout families, page transitions, table starts/ends, repeated sections, and continuation edges without loading every rendered batch into agent context. `overview_batch_index` lists the other rendered batches by page range for optional follow-up.

Focused full-page images are for ambiguous pages only. Use prepared page artifacts or a focused render action when the 2x2 overview or row geometry is not enough to resolve a boundary or visual hierarchy.

Raw page `text` is supporting evidence. Use it for exact spelling, punctuation, table titles, and wrapped values after a structured candidate exists. Do not make raw linear text the first table strategy.

## Priority

Apply priority inside each candidate or region, not globally across the PDF:

1. `table_region_hints` groups.
2. Plausible usable `table_detections`.
3. Field/value or line/value extraction for label/value blocks.
4. Coordinate extraction from stable row geometry or x-bands.
5. 2x2 overview batches and focused images for layout decisions.
6. Raw linear text as the final supporting fallback.

## Recommended Candidate Shape

When turning inspection evidence into a plan, keep this shape in mind:

```text
candidate_id
layout_type
pages
region or source_detections
suggested_method
columns
row_preview
confidence or warnings
evidence_used
```

The current tool may not emit this exact object. The agent should still reason in this shape so extraction plans stay reviewable.

## Example-Derived Signals

The local example PDFs show why priority must be situational:

- Horizontal benchmark tables can produce usable grid candidates.
- Some benchmark pages mix usable grid detections with false positives.
- Datasheet-style prose can produce only false positives even when visual tables are present.
- Long amount-heavy PDFs can have strong text layers but weak grid detections; amount rows are usually better handled as line/value or field/value regions.

These are layout signals, not filename-specific rules.
