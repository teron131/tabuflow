"""Prompt helpers for the deterministic tabular analysis workflow."""


def format_source_file_list(source_files: list[str]) -> str:
    """Render the source file list once for prompts and console output."""
    return "\n".join(f"- {source_file}" for source_file in source_files) or "- (none)"


def build_task_prompt(
    prompt: str,
    task: str,
    source_files: list[str],
    *,
    search_context: str = "",
) -> str:
    """Build the full user-facing task prompt."""
    parts = [prompt.strip()] if prompt.strip() else []
    parts.append(f"Source files:\n{format_source_file_list(source_files)}\nTask: {task}")
    if search_context.strip():
        parts.append(search_context.strip())
    return "\n\n".join(parts)
