# AWS Email Reporting Context

Use this reference when AWS invoice folders include `.eml` or `.msg` files alongside PDFs and spreadsheets.

Emails are context and reporting evidence. They should help identify what the invoice package is for, which account/customer/period is being discussed, what attachments belong together, and whether an approval or cost-transfer step has already happened. They should not override the invoice PDF tables for billing amounts unless the user explicitly asks to reconcile email-reported amounts. They are references, not default structured outputs.

## What To Extract

For each email, capture:

- subject,
- sender, recipients, and date,
- message type: invoice forwarding, approval request, approval reply, internal cost transfer, PR/PO settlement, or other,
- mentioned cloud provider and account ID,
- mentioned customer, tenant, or business unit,
- mentioned billing period,
- mentioned amount and currency,
- attachment names or referenced forwarded messages,
- approval/review status and reviewer comments when present.

Keep these facts as notes or internal metadata. Do not add them as billing table rows, and do not produce an email CSV unless the user asks for an email reconciliation dataset.

## Relationship To PDF Tables

Use emails to:

- group related PDFs, spreadsheets, and forwarded messages,
- infer customer-facing context for an invoice package,
- detect expected approval or internal transfer workflow,
- cross-check account IDs and billing periods,
- explain why an invoice PDF exists in a folder.

Do not use emails to:

- replace PDF table extraction,
- invent missing PDF rows,
- collapse multiple invoices into one summary,
- treat an email table as authoritative without checking attachments.

## Parsing Notes

- Prefer the standalone CLI when available:

```bash
uv run tabuflow email inspect <message.eml>
uv run tabuflow email inspect <message.msg>
```

- The CLI output is structural: subject, sender/recipients/date, body source, body preview, body length, and attachments. Derive provider/account/period/amount and approval status from that text; they are not generic tool fields.
- `.eml` files can usually be parsed with Python's standard `email` package.
- `.msg` files need an Outlook MSG parser for reliable headers, body text, and attachment names; avoid raw `strings` scans unless no parser is available.
- HTML email bodies often contain reporting tables. Convert them to plain text or parse the HTML table structure before extracting fields.
- Approval replies may be short, with the useful reporting table in quoted history. Read the thread, not only the newest reply.

## Reference Note Shape

When the user wants email context captured, prefer a short reference note per message. Include only the useful facts:

- `email_path`
- `message_type`
- `subject`
- `sender`
- `recipients`
- `sent_at`
- `provider`
- `account_id`
- `customer`
- `billing_period`
- `reported_amount`
- `reported_currency`
- `approval_status`
- `review_comment`
- `attachments`
- `related_invoice_paths`

These facts are for reconciliation and workflow context. Keep invoice PDF table outputs separate, and keep CSV outputs reserved for actual extracted invoice tables.

## Validation

Before relying on email context:

- Check that account IDs mentioned in emails match the related PDF invoices.
- Check that billing periods are compatible across the email and attachments.
- Treat email-reported amounts as a reconciliation hint, not as the extracted invoice amount.
- Preserve the original email path and subject for traceability.
