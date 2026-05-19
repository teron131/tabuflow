# Data Agentics Skill Context

We are exploring whether a general coding agent with loaded skills can replace
most of the earlier specialized skill-wrapper and pseudo coding-agent machinery.
The immediate work is to identify what is actually needed, what can stay as
simple skill context, and what still needs real code.

Available domain skills:

- `billing-tabular-pipeline`: reusable billing CSV/XLS/XLSX inspection,
  normalization, validation, and output-contract workflow.
- `gcp-cost-pipeline`: GCP cost-table transformation into aggregated
  reconciliation results and IBS charge-item upload rows.
- `aws-invoice-pdf-tables`: AWS invoice PDF table cleanup into SQLite-ready
  visual tables.

Practical process:

- Start from the user's concrete task and decide whether one of the loaded
  domain skills is relevant.
- If a skill applies, use its workflow and references as context, then translate
  that into whatever capabilities the current session actually has.
- Keep the general agent responsible for reading code, editing files, checking
  diffs, and verifying changes.
- Capture discoveries about the minimum config, skill shape, lifecycle, and
  process rules we want to keep.

Things to notice while exploring:

- Which instructions make the agent immediately more effective with less user
  explanation.
- Which old skill-format or pseudo-agent details are only tool plumbing and can
  be removed.
- Which domain processes still need scripts, tests, or product code instead of
  prompt guidance.
- Where a skill over-specifies execution details instead of defining the
  expected result, formula, or validation bar.
