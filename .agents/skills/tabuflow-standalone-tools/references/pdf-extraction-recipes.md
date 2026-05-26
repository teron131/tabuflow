# PDF Extraction Recipes

These recipes are starting shapes. Adjust page ranges, columns, patterns, clips, and merge behavior from inspection evidence.

## Detected Tables

Use for plausible horizontal grid tables.

```bash
uv run tabuflow pdf extract path/to/file.pdf tables detected --page-start 1 --min-rows 2
```

Borderless text-positioned tables:

```bash
uv run tabuflow pdf extract path/to/file.pdf tables detected \
  --strategy text \
  --require-header \
  --page-start 2 \
  --page-end 3 \
  --min-rows 2
```

Mixed strategy and continuing chunks:

```bash
uv run tabuflow pdf extract path/to/file.pdf tables detected \
  --vertical-strategy text \
  --horizontal-strategy lines \
  --page-start 3 \
  --page-end 13 \
  --output-columns model,organization,score \
  --min-filled-cells 2 \
  --merge-tables auto
```

Use `--clip X0,Y0,X1,Y1` when non-table page regions are contaminating cells.

## Coordinate Tables

Use for stable x-band tables when detector output is missing or suspicious.

```bash
uv run tabuflow pdf extract path/to/file.pdf tables coordinate \
  --pages 2 \
  --y-min 180 \
  --y-max 760 \
  --column model:50:190 \
  --column score:190:260 \
  --required-columns model,score
```

Wrapped label or description column:

```bash
uv run tabuflow pdf extract path/to/file.pdf tables coordinate \
  --pages 2 \
  --column label:50:240 \
  --column amount:420:520 \
  --required-columns amount \
  --continuation-column label \
  --anchor-y-slop 5
```

## Line/Value Amount Rows

Use for stacked amount rows, repeated amount sections, and pages where labels and values are visually paired.

```bash
uv run tabuflow pdf extract path/to/file.pdf tables line-value \
  --value-preset money \
  --label-column label \
  --value-column amount \
  --section '^(Summary|Detail|Activity By Account)$' \
  --context 'account=^(?P<value>.+ \\([0-9]{12}\\))$' \
  --include-page
```

Split sections when the carried section should become separate CSV outputs:

```bash
uv run tabuflow pdf extract path/to/file.pdf tables line-value \
  --value-preset money \
  --label-column label \
  --value-column amount \
  --split-sections \
  --drop-empty-split \
  --include-page
```

## Field/Value Blocks

Use for known labels with multiline values.

```bash
uv run tabuflow pdf extract path/to/file.pdf tables field-value \
  --field 'GPU=^GPU$' \
  --field 'Networking=^Networking$' \
  --field 'Support=^Support$' \
  --collect-until-next-field \
  --output-columns field,value
```

## After Extraction

Check the manifest status and diagnostics. `extraction_status` can be `ok`, `empty`, or `low_confidence`.

For PDFs, table CSVs are written under the root-owned PDF artifact workspace. Filenames use the normalized source stem plus a page tag such as `p01p88`; descriptors and fingerprints are collision fallbacks only.
