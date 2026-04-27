"""Prompt builders for the generic file fixer workflow."""

DEFAULT_FIXER_TASK_PROMPT = "Fix grammar, spelling, and obvious typos while preserving meaning and structure."

DEFAULT_FIXER_SYSTEM_PROMPT = """Edit the file directly to satisfy the task.

Hard constraints:
- Treat the file as generic UTF-8 text unless the content itself implies stricter structure.
- Be format-aware: preserve the native structure and syntax of the file you are editing.
- For structured formats such as JSON, YAML, TOML, XML, CSV, Markdown frontmatter, or code/config files, make local edits that keep the file parseable and keep delimiters, indentation, keys, list/object shape, and ordering stable unless the task clearly requires a structural change.
- For structured files containing long embedded text blocks, prefer the smallest local fix that resolves a concrete issue; do not rewrite large text values just to improve formatting, grammar, or stylistic consistency.
- For prose or plain-text formats, preserve surrounding layout and conventions unless the task clearly requires reflow.
- Never "repair" parts of the file that are outside the requested change just because the format suggests a broader cleanup.
- Each pass will provide the current file as hashline-formatted text where every line looks like `LINE#HASH:content`.
- If changes are needed, return ONLY structured hashline edits that target the provided refs.
- If no changes are needed, return an empty `edits` list.
- Preserve meaning and surrounding structure; avoid broad rewrites.
- Do not add explanations, markdown fences, or commentary when returning updated text.
"""

CLEAN_TASK_LOG = "DONE:\n- clean\nREMAINING:\n- none"
ALLOWED_FIXER_ACTIONS = ("ADD", "ALIGN", "DEDUPE", "FILL", "FIX", "MERGE", "NORMALIZE", "REMOVE", "REORDER", "SPLIT", "TRIM")
HIGH_PRIORITY_FIXER_ACTIONS = ("FIX", "ADD", "REMOVE", "MERGE", "FILL", "SPLIT", "DEDUPE")
LOW_PRIORITY_FIXER_ACTIONS = ("NORMALIZE", "REORDER", "ALIGN", "TRIM")
FIXER_ACTION_MEANINGS = {
    "FIX": "correct incorrect existing text or values",
    "ADD": "insert a small missing local value, delimiter, field, or line when the current file clearly shows where it belongs",
    "REMOVE": "delete stray, broken, or duplicate text",
    "MERGE": "consolidate repeated or split content into one correct form",
    "FILL": "populate an obviously missing local value when the current file provides enough evidence",
    "NORMALIZE": "align nearby formatting or consistency without broad rewriting",
    "SPLIT": "separate wrongly combined local content into distinct correct parts",
    "REORDER": "reorder nearby local items only when the current file itself makes the correct order clear",
    "DEDUPE": "remove duplicated local content while keeping one correct copy",
    "ALIGN": "make nearby sibling fields or entries follow the same local pattern when the correct pattern is already present",
    "TRIM": "remove clearly extraneous surrounding whitespace or punctuation without changing meaning",
}


def build_fixer_agent_prompt(
    *,
    target_file: str,
    fixer_context: str = "",
    max_turns: int,
) -> str:
    """Build the stable task framing for the fixer model."""
    context_block = f"\nContext:\n{fixer_context}\n" if fixer_context else ""
    return f"""Work on /{target_file.lstrip("/")} directly.

Fix as many obvious safe issues as you can per pass.
Prefer fewer, higher-yield passes over many tiny passes.
Keep unchanged text stable and return only structured hashline edits.
Stay within {max_turns} passes.{context_block}
"""


def build_fixer_pass_prompt(
    *,
    target_file: str,
    current_text: str,
    pass_number: int,
    max_turns: int,
    task_log: str = "",
) -> str:
    """Build the per-pass edit prompt."""
    task_log_block = f"\nCurrent task log:\n{task_log}\n" if task_log else ""
    passes_remaining = max_turns - pass_number + 1
    normalized_target = target_file.lstrip("/")
    return f"""Pass {pass_number} for /{normalized_target}.

Keep working until the file satisfies the system prompt. Do not stop just because you fixed one issue if visible remaining work still exists.
Fix all obvious issues you can confidently resolve in this pass, not just the first one you notice.
You have {passes_remaining} passes remaining including this one, so prefer a broader safe cleanup over a tiny one-issue edit.
If the task log lists remaining work, use it as a priority guide and try to clear several items before stopping.
Allowed local cleanup actions are: {", ".join(ALLOWED_FIXER_ACTIONS)}.
Prefer edits that complete several nearby allowed actions in one pass when the context is exact.
For structured files with long string values, do not rewrite a large embedded text block unless the task log identifies a concrete local corruption inside that block.
Do not spend passes on heading-level normalization, whitespace polish, punctuation polish, or grammar polish unless those issues clearly break structure or signal local corruption.

Hashline rules:
- The current file text below uses refs in the form `LINE#HASH:content`.
- Target only refs that appear in the current file text below.
- Replacement or inserted lines must be raw file lines without the `LINE#HASH:` prefix.
- Never copy any `LINE#HASH:` prefix into returned replacement text.
- Replacement or inserted lines must preserve any syntax that belongs on those physical lines, including trailing commas, braces, brackets, quotes, and indentation.
- Prefer the smallest complete-line edit that keeps the surrounding structure valid.
- Use `replace_range` for replacements or deletions.
- Use `insert_before` or `insert_after` for pure insertions.
- If no changes are needed in this pass, return an empty `edits` list.
{task_log_block}

Current file text (hashline-formatted):
{current_text}
"""


def build_fixer_progress_prompt(
    *,
    target_file: str,
    current_text: str,
) -> str:
    """Build the review prompt that summarizes remaining work."""
    action_meanings = "\n".join(f"  {action} = {FIXER_ACTION_MEANINGS[action]}" for action in ALLOWED_FIXER_ACTIONS)
    return f"""Update a tiny progress checklist for /{target_file.lstrip("/")}.

Use the current file text only.
Judge the current file against the active system prompt for this fixer run.
Return ONLY a compact checklist in this exact shape:

DONE:
- ...
REMAINING:
- ACTION: ...

Rules:
- Keep the checklist short and concrete.
- Tick off work clearly completed in the current file.
- Keep only visible remaining work needed to satisfy the task in the REMAINING section.
- Prefer issue clusters over line-by-line details.
- Keep at most 3 REMAINING bullets.
- Keep only actionable, local cleanup items that can be fixed from the current file alone.
- Every REMAINING bullet must start with one of these actions: {", ".join(ALLOWED_FIXER_ACTIONS)}.
- Action meanings:
{action_meanings}
- Treat {", ".join(HIGH_PRIORITY_FIXER_ACTIONS)} as higher-priority remaining work.
- Treat {", ".join(LOW_PRIORITY_FIXER_ACTIONS)} as lower-priority cleanup.
- For structured files with long embedded text values, only call out issues inside those values when they are clearly local corruption, duplication, broken delimiters, or visibly broken numbering/label sequences with an obvious local fix.
- Do not include source-validation, segmentation, line-tag, anchor, or coverage tasks.
- Do not include confirm/verify/check/review-only tasks.
- Do not include optional polish or stylistic suggestions as remaining work.
- Do not include grammar polish, markdown heading-level normalization, whitespace normalization, or punctuation cleanup unless it changes parseability or fixes clear local corruption.
- If only lower-priority cleanup remains and the file otherwise satisfies the task, treat the file as clean.
- Do not repeat uncertain or non-actionable items.
- If an unresolved item cannot be safely fixed from the current file alone, drop it instead of repeating it.
- If the file now looks clean, return exactly:
DONE:
- clean
REMAINING:
- none

Current file text:
{current_text}
"""


def build_review_system_prompt(system_prompt: str) -> str:
    """Build the review-call system prompt."""
    return f"""{system_prompt}

For this call, do not rewrite the file. Return only the compact checklist requested by the user prompt."""


def build_hashline_repair_system_prompt(system_prompt: str) -> str:
    """Build the repair-call system prompt."""
    return f"""{system_prompt}

The previous hashline edit plan failed to apply. Return only a corrected, smaller structured hashline edit response for the same file."""


def build_hashline_repair_prompt(
    *,
    error_text: str,
    task_log: str,
    current_text: str,
    attempted_edits: str,
) -> str:
    """Build the retry prompt after a failed edit application."""
    return f"""Hashline apply error:
{error_text}

Correct the edit plan against the current hashline-formatted file contents.
Use the updated refs shown in the file if the old ones went stale.
Keep the retry smaller and more local than the failed attempt.
Do not copy `LINE#HASH:` prefixes into replacement text.
If no changes are needed, return an empty `edits` list.
Do not respond to a local apply error by rewriting a much larger embedded text block unless the error itself shows that block is the only broken area.

Current task log:
{task_log}

Current file text (hashline-formatted):
{current_text}

Failed edit plan:
{attempted_edits}"""
