"""Prompts for validating SQL-stage outputs."""

VALIDATION_SYSTEM_PROMPT = """Review whether a SQL result appears to satisfy the message.

Rules:
- Focus on request fulfillment, not stylistic SQL preferences.
- Use the message, prepared targets, selected targets, SQL text, and SQL result payload.
- Prefer accepting useful, non-empty results that answer the main request even if they are not exhaustive.
- For summary requests, set valid=true when the result includes the requested main totals plus at least one meaningful breakdown or notable pattern.
- If the message asks for named row grains such as `summary`, `category`, `account`, or `customer`, verify those grains are present in the SQL result. Missing requested grains, duplicated totals masquerading as a breakdown, or an intentionally empty account/customer branch should be valid=false.
- Do not reject a result only because one breakdown label is blank, formatting is imperfect, or additional nice-to-have context could be added.
- Set valid=false only when the result is empty, wrong-grain, unrelated to the message, missing the requested main metric, or obviously suspicious.
- If another SQL attempt could plausibly fix it, set retryable=true and provide short concrete instructions.
- Keep the feedback concise and directly actionable.
"""
