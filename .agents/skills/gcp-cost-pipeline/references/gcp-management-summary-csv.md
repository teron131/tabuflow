# GCP Management Summary CSV Contract

Use this reference when the user asks to create a GCP monthly management summary, recreate a historical GCP Rev and Cost workbook, or produce a CSV in the same accountant-facing style.

## Inputs

Use only the artifacts the user provides or that already belong to the workspace:

- Raw monthly GCP export: a GCP cost table or CSV copy with the stable GCP billing columns.
- Optional historical summary workbook: a workbook shaped like a GCP Rev and Cost monthly summary.
- Optional monthly customer cost reports: one workbook per billing account, with columns like `Service`, `Usage cost`, `Negotiated savings`, `Savings programs`, `Other savings`, and `Subtotal`.

The raw GCP export remains the durable source for normalized account totals and `RESELLER_MARGIN`. The customer cost reports explain the accountant-facing service-charge and savings buckets. The historical summary workbook is optional reference evidence, not a required runtime input.

## Month Block Detection

Do not hardcode a month or fixed Excel columns.

For a reference summary workbook:

- Row 1 names month blocks such as `Jan'26`, `Feb'26`, `Mar'26`, or a quarter block.
- Row 2 names customers.
- Row 3 names billing account IDs.
- Row 4 names charge types such as `Invoice` or `Cost Transfer`.
- A month block is the contiguous group of customer columns under the selected month label.

When row 1 uses merged cells, read the month from the leftmost populated cell and carry it across the following customer columns until the next month label appears.

## Default Output Shape

Write the CSV or workbook under `artifacts/outputs/`.

Default to a compact selected-month cross-tab, even when no historical reference workbook is available:

- Column A header: `Metric`.
- Columns B onward: one billing account per account column.
- Final column: `Total`.
- Account column header: `<customer> - <tenant/domain> - <sequence> (<billing_account_id>)` when that mapping is known. Otherwise use `<customer/account name> (<billing_account_id>)`.

Rows, in order:

- `Usage Month`
- `Customer`
- `Billing Account ID`
- `Charge Type`
- `Source Workbook`
- `(A) GCP Service Charge`
- `(B) GCP Credits`
- optional savings breakdown rows:
  - `Negotiated savings`
  - `Savings programs`
  - `Other savings`
- `(C) HKT Billed Amount (A+B)`
- `(D) GCP Program Discount / RESELLER_MARGIN`
- `(E) GCP Cost (A+B+D)`
- `Margin % (USD)`
- `Rev Rate`
- `Cost Rate`
- `GCP Cost HKD`
- `HKT Billed Amount HKD`
- `Margin % (HKD)`
- `Raw row/item count`
- `Validation: cost-report subtotal delta`
- `Validation: raw export net-cost delta`
- `Reference summary revenue HKD delta`
- `Reference summary cost HKD delta`
- `Source notes`

Include totals for additive numeric rows. For rate and margin rows, use an explicit weighted/recomputed total only when the basis is clear; otherwise leave the total blank or repeat a shared rate only when it truly applies to every account.

Historical Rev and Cost style workbooks may store multiple months in one wide sheet. Use those sheets to identify the selected month block, rates, forecast rows, and reference deltas, but do not reproduce the entire multi-month block by default. The management-summary deliverable should be one selected month in the compact `Metric` plus account columns shape.

If the historical workbook is not available, keep the same row labels and account columns. Leave `Reference summary revenue HKD delta` and `Reference summary cost HKD delta` blank or set them to `not available`, and explain the missing reference in `Source notes`.

## Formulas

For each selected month/account column:

- `service_charge_usd`: total `Usage cost` from the matching customer cost report when available.
- `credits_usd`: `Negotiated savings + Savings programs + Other savings` from the matching customer cost report when available.
- If no customer cost report is available, use the raw export to produce whatever service/credit split is supported by its normalized fields, and state any unavailable presentation split in `Source notes`.
- `billed_amount_usd`: `service_charge_usd + credits_usd`. If a reference summary workbook provides exact cells, preserve those values and report any cent-level delta from the report subtotal.
- `program_discount_usd`: sum raw export rows for the same billing account where normalized `credit_type == RESELLER_MARGIN`, using `Unrounded Cost ($)` and excluding footer rows such as `Total` and `Rounding error`.
- `gcp_cost_usd`: `billed_amount_usd + program_discount_usd`.
- `margin_usd_pct`: `(billed_amount_usd - gcp_cost_usd) / billed_amount_usd`.

HKD conversion:

- `gcp_cost_hkd`: `gcp_cost_usd * month_cost_rate`.
- `billed_amount_hkd`: use `month_cost_rate` for `Invoice` columns and `revenue_rate` for `Cost Transfer` columns.
- `margin_hkd_pct`: `(billed_amount_hkd - gcp_cost_hkd) / billed_amount_hkd`.

Historical reference workbooks may store:

- `revenue_rate` near the `Rev Rate` row.
- `month_cost_rate` in the selected month block's `Cost Rate` row.
- Forecast status and forecast formulas below the HKD section.

Preserve the explicit rates in the output so the conversion basis is visible.

## Forecast Rows

Only include forecast rows when the reference summary workbook has them or the user asks for them.

For a month block with one `Invoice` account and multiple `Cost Transfer` accounts:

- `Forecast Input (Rev)`: the invoice account's `billed_amount_hkd`.
- `Forecast Input (Cost)`: negative sum of all selected account `gcp_cost_hkd`.
- `Forecast Input (Cost) (non-invoice accounts)`: sum `billed_amount_hkd` for non-invoice or cost-transfer accounts.
- `Forecast Net`: forecast cost plus non-invoice billed amount.
- `Forecast Margin %`: `(forecast_rev + forecast_net) / forecast_rev`.

Place forecast rows below the main source/validation rows unless the user asks to mirror the historical workbook ordering. Name the non-invoice forecast row according to the visible reference wording if the workbook provides one; otherwise use a semantic label.

## Validation

Before reporting the CSV as done:

- Confirm every output customer column has a billing account ID.
- Confirm source cost-report totals match the visible service/credits/billed rows, allowing only explained cent-level rounding differences.
- Confirm raw `RESELLER_MARGIN` from unrounded cost matches the program-discount row.
- Confirm `E = C + D`, HKD cost conversion, HKD billed conversion, and margins.
- Confirm totals add across account columns where the row is additive.
- Confirm each account column names the usage month, customer, billing account ID, charge type, and source workbook/report.
- State plainly when the raw export can validate net totals but cannot reproduce the service-vs-credit presentation split by itself.
