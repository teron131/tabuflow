"""Prompt helpers for the deterministic tabular analysis workflow."""

FINAL_ANSWER_SYSTEM_PROMPT = """Write the final answer for a completed tabular-to-SQL analysis run.

Use only the provided structured execution results.

Rules:
- answer the user's task directly,
- respect the task prompt provided by the user message,
- mention the source file(s), the SQL target(s), and the saved view name when available,
- do not invent facts or explanations that are not present in the payload,
- keep the answer concise and audit-friendly,
- if the run is blocked or failed, explain that plainly instead of pretending it succeeded.
"""


def format_source_file_list(source_files: list[str]) -> str:
    """Render the source file list once for prompts and console output."""
    return "\n".join(f"- {source_file}" for source_file in source_files) or "- (none)"


def build_task_prompt(prompt: str, task: str, source_files: list[str]) -> str:
    """Build the full user-facing task prompt."""
    if prompt.strip():
        return f"{prompt}\n\nSource files:\n{format_source_file_list(source_files)}\nTask: {task}"
    return f"Source files:\n{format_source_file_list(source_files)}\nTask: {task}"
