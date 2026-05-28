# GCP Management Summary CSV Contract

Use this reference when the user asks to recreate a historical GCP monthly summary workbook or produce a CSV in the same visible format.

## Inputs

Use only the artifacts the user provides or that already belong to the workspace:

- Raw monthly GCP export: `cost_table.xlsx` or CSV copy with the stable GCP billing columns.
- Optional historical summary workbook: a workbook shaped like `GCP Rev and Cost Excel.xlsx`.
- Optional monthly customer cost reports: one workbook per billing account, with columns like `Service`, `Usage cost`, `Negotiated savings`, `Savings programs`, `Other savings`, and `Subtotal`.

The raw GCP export remains the durable source for normalized account totals and `RESELLER_MARGIN`. The customer cost reports explain the accountant-facing service-charge and savings buckets. Do not require the reference summary workbook or customer cost reports for the default aggregate/IBS workflow.

## Month Block Detection

Do not hardcode a month or fixed Excel columns.

For a reference summary workbook:

- Row 1 names month blocks such as `Jan'26`, `Feb'26`, `Mar'26`, or a quarter block.
- Row 2 names customers.
- Row 3 names billing account IDs.
- Row 4 names charge types such as `Invoice` or `Cost Transfer`.
- A month block is the contiguous group of customer columns under the selected month label.

When row 1 uses merged cells, read the month from the leftmost populated cell and carry it across the following customer columns until the next month label appears.

## Output Shape

Write the CSV or workbook under `artifacts/outputs/`.

Recommended rows:

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
- optional forecast/status block from the reference workbook

Include a total column when the row is additive. Leave margin total cells blank unless the user asks for a weighted margin and the basis is explicit.

## Formulas

For each selected month/account column:

- `service_charge_usd`: total `Usage cost` from the matching customer cost report when available.
- `credits_usd`: `Negotiated savings + Savings programs + Other savings` from the matching customer cost report when available.
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

## Forecast Block

Only include the forecast block when the reference summary workbook has one or the user asks for a management-summary-style output.

For a month block with one `Invoice` account and multiple `Cost Transfer` accounts:

- `Forecast Input (Rev)`: the invoice account's `billed_amount_hkd`.
- `Forecast Input (Cost)`: negative sum of all selected account `gcp_cost_hkd`.
- `Forecast Input (Cost) (non-invoice accounts)`: sum `billed_amount_hkd` for non-invoice or cost-transfer accounts.
- `Forecast Net`: forecast cost plus non-invoice billed amount.
- `Forecast Margin %`: `(forecast_rev + forecast_net) / forecast_rev`.

Name the non-invoice forecast row according to the visible reference wording if the workbook provides one; otherwise use a semantic label.

## Validation

Before reporting the CSV as done:

- Confirm every output customer column has a billing account ID.
- Confirm source cost-report totals match the visible service/credits/billed rows, allowing only explained cent-level rounding differences.
- Confirm raw `RESELLER_MARGIN` from unrounded cost matches the program-discount row.
- Confirm `E = C + D`, HKD cost conversion, HKD billed conversion, and margins.
- Confirm totals add across account columns where the row is additive.
- State plainly when the raw export can validate net totals but cannot reproduce the service-vs-credit presentation split by itself.
