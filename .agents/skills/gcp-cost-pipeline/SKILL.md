---
name: gcp-cost-pipeline
description: Use for monthly GCP billing cost-table work: raw or aggregated cost summaries, account reconciliation, optional IBS charge-item upload rows, and optional Rev-and-Cost/management-summary views from visible GCP billing inputs.
---

# GCP Cost Pipeline

## Use

Use this skill for GCP-only monthly billing workflows. The durable source boundary is the raw monthly GCP cost table. Optional reference workbooks, cost reports, templates, and prior outputs are context only when the user or test case actually provides them.

The workflow may produce a raw/aggregated GCP cost summary, an IBS charge-item upload output, or a selected-month management summary. A raw-only test should still produce the best directly supported summary from the raw export and should report unavailable accountant-facing details plainly.

## Tools

Use Tabuflow first for messy local billing files:

- `tabuflow tabular inspect <file>` to identify headers, metadata rows, sheets, and table shape.
- `tabuflow tabular extract <file>` to create queryable artifacts.
- `tabuflow artifacts from-source <file>` to find the extracted artifact for a source.
- `tabuflow artifacts describe <artifact>` before writing non-trivial SQL.
- `tabuflow artifacts query ...` or `tabuflow artifacts query @query.sql` for bounded validation queries.

Use ordinary shell/editor tools for reading files, writing SQL or scratch scripts under `artifacts/sql/`, and creating final CSV/XLSX outputs under `artifacts/outputs/`.

## Inputs

Required input:

- monthly raw GCP cost table export. Identify it by stable GCP billing columns, not by filename. CSV copies of the same export are acceptable fixtures.

Optional visible inputs:

- historical GCP Rev and Cost style workbook, for expected shape, month block detection, rates, ordering, forecast rows, and historical deltas,
- per-account monthly customer cost reports, for accountant-facing service-charge and savings buckets,
- IBS upload template, for column order, constants, date format, and total-row expectations.

Maintained implementation/config may provide customer mappings, rates, charge codes, contact IDs, or template constants. Do not ask for extra companion spreadsheets as the default workflow path. If a target needs a mapping that is neither visible nor maintained, skip or mark the affected part and report the gap.

Treat raw GCP export headers as ingestion-boundary labels. Normalize them into semantic fields before applying business logic. Expected raw columns include:

- `Billing account name`
- `Billing account ID`
- `Project name`
- `Project ID`
- `Project hierarchy`
- `Service description`
- `Service ID`
- `SKU description`
- `SKU ID`
- `Consumption model description`
- `Credit type`
- `Cost type`
- `Usage start date`
- `Usage end date`
- `Usage amount`
- `Usage unit`
- `Unrounded Cost ($)`
- `Cost ($)`

Use `references/gcp-cost-view-contract.md` for the semantic field mapping, formulas, and output contracts. Use `references/gcp-management-summary-csv.md` for the detailed management-summary contract.

For future months, derive the billing month from source dates, visible month blocks, or the user request. Do not hardcode a prior month or fixed worksheet columns.

## Targets

**Raw or aggregated GCP cost summary.** Reconcile the monthly cost table by account, category, and summary. Use semantic rows and columns, not workbook-mirrored pivot labels. Expected levels include summary, category, and account/customer. Expected metrics include usage before reseller margin, net GCP cost, discount, customer charge or revenue when supported, HKD revenue when rate basis is available, gross profit, gross profit percent, and row/item count.

**IBS charge-item upload.** Produce charge rows in the IBS upload-template shape when the user asks for IBS output and the needed template/mapping/defaults are available. The core sheet is `Bill Item`. Preserve template column order, constants, date format, and total row unless the user asks for a different export shape. Important fields include customer name, `bill_acct`, `contct_id`, `chrg_code`, `chrg_amt`, `start_bill`, `end_bill`, remarks, `bill_date`, `ccc`, `bill_methd`, and `uploaded`. Template constants such as `DY80`, `CAM8`, `O`, and `N` should come from maintained defaults when not visible.

**Management summary.** Produce a selected-month metric-by-account summary when the user asks for a monthly summary, Rev and Cost style output, similar format, or CSV demo. The default shape is compact: one metric/row-label column, one billing-account column per account, and a total column. Use the raw GCP export for normalized account totals and `RESELLER_MARGIN`. Use visible customer cost reports for service-charge versus savings presentation when available. Use a visible historical summary workbook for month selection, rates, forecast rows, ordering, and validation deltas when available. If those files are not visible, keep the row shape honest and mark unavailable presentation details instead of inventing them.

Write outputs under the current working directory's `artifacts/` path:

- `artifacts/tabular.sqlite` for prepared tabular data,
- `artifacts/sql/` for reusable SQL or scratch transformation files,
- `artifacts/outputs/` for final review/upload files.

Do not write generated workbooks into source/example directories or overwrite source/reference files.

## Workflow

1. Locate visible input files by role. First find the raw GCP export by its billing columns. Then, only if visible and relevant, identify customer cost reports, historical summary workbooks, or upload templates by content and sheet/header shape.
2. Inspect the raw cost table shape. Confirm expected GCP export columns, recover visible invoice/report metadata when present, and do not infer business fields from workbook pivot labels.
3. Load or normalize the cost table into an inspectable tabular layer. Prefer `tabuflow tabular extract ...` or the equivalent tool when Tabuflow is available.
4. Rediscover artifacts with `artifacts from-source`, `list`, `describe`, and bounded `query` calls. Do not copy generated table names from memory.
5. Normalize exact raw headers into semantic fields such as `account_id`, `account_name`, `credit_type`, `cost_type`, `usage_start`, `usage_end`, `unrounded_cost_usd`, and `cost_usd`.
6. Derive account-level business rows. Group by billing account and compute usage before reseller margin, credits/adjustments, reseller margin, net GCP cost, item count, and any maintained/configured customer charge or HKD values.
7. Use optional visible files only for the target they actually support. Customer reports support service/credit presentation. Reference summaries support historical shape/rates/deltas. IBS templates support upload-column fidelity.
8. Write reviewable outputs under `artifacts/outputs/`. Keep one-off scripts under `artifacts/sql/` unless the logic should be promoted into maintained repo code.

## Validate

Always reconcile raw and aggregated totals to the raw GCP export after excluding footer or non-business rows such as `Total` and `Rounding error`. Prefer unrounded cost for reconciliation and reseller margin where available, and report cent-level differences instead of silently choosing one source.

For management-summary output, validate the visible basis for each section: customer reports for service-charge and savings buckets, raw export for `RESELLER_MARGIN`, reference workbook for historical deltas/rates/forecast rows, and maintained defaults for any configured customer or HKD assumptions. If a basis is missing in a raw-only test, report it as unavailable instead of filling it from memory.

For IBS output, validate row count, amount totals, required template constants, date format, one total row, and skipped accounts caused by missing maintained mappings.

## Boundaries

- Do not hardcode a generated table name, extracted table hash, billing month, account count, or file-specific row position.
- Do not require companion spreadsheets or config CSVs as default inputs.
- Do not derive the service-charge versus savings split from the raw export alone when monthly cost-report workbooks are visible; use the reports for that presentation split and the raw export for reseller-margin validation.
- Do not mirror accountant workbook labels exactly unless the user asks for workbook-like output.
- Do not use hidden reference files or previous generated outputs in blind tests.
- Do not hide core formulas in workbook cells when an inspectable query/script layer is possible.
- Do not treat missing mappings, charge types, or rates as permission to fabricate values.
- Keep local file paths out of user-facing output columns.
