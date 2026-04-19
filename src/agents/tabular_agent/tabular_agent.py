"""Public entrypoints for the deterministic tabular analysis workflow."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Literal

from llm_harness.clients.openai import ChatOpenAI

from .graph import create_tabular_graph
from .prompts import format_source_file_list
from .state import TabularTaskOutput, build_graph_input

DEFAULT_MODEL_ENV = "FAST_LLM"
DEFAULT_MODEL = "openai/gpt-5.4-nano"
DEFAULT_REASONING_EFFORT: Literal["minimal", "low", "medium", "high", "xhigh"] = "high"
STEP_FIELDS: dict[str, dict[str, Any]] = {
    "extract": {
        "status": None,
        "database_path": None,
        "extracted_targets": [],
    },
    "sql": {
        "status": None,
        "selected_targets": [],
        "candidate_sql": None,
        "last_error": None,
        "sql_result": None,
    },
    "save": {
        "status": None,
        "saved_view_name": None,
        "last_error": None,
    },
}


class TabularTaskAgent:
    """Deterministic tabular analysis agent with a pinned save-view step."""

    def __init__(
        self,
        *,
        prompt: str = "",
        root_dir: str | Path | None = None,
    ):
        self.prompt = prompt
        self.model = os.getenv(DEFAULT_MODEL_ENV) or DEFAULT_MODEL
        self.llm = ChatOpenAI(
            model=self.model,
            temperature=0,
            reasoning_effort=DEFAULT_REASONING_EFFORT,
        )
        self.graph = create_tabular_graph(llm=self.llm, prompt=prompt, root_dir=root_dir)

    def invoke(self, task: str, *, source_files: list[str]) -> TabularTaskOutput:
        """Run the graph once and validate the final output."""
        result = self.graph.invoke(build_graph_input(task, source_files))
        return TabularTaskOutput.model_validate(result)


def render_step_update(step_name: str, update: dict[str, Any]) -> str:
    """Render one streamed graph update compactly."""
    if step_fields := STEP_FIELDS.get(step_name):
        payload = {
            field_name: update.get(
                field_name,
                default_value,
            )
            for field_name, default_value in step_fields.items()
        }
        return json.dumps(payload, ensure_ascii=True, sort_keys=True)
    if step_name == "answer":
        return str(update.get("final_answer", "")).strip()
    return json.dumps(
        update,
        ensure_ascii=True,
        sort_keys=True,
        default=str,
    )


def run_task(
    *,
    prompt: str = "",
    task: str,
    source_files: list[str],
    root_dir: str | Path | None = None,
) -> TabularTaskOutput:
    """Run the deterministic graph and stream step updates."""
    agent = TabularTaskAgent(
        prompt=prompt,
        root_dir=root_dir,
    )
    graph_input = build_graph_input(task, source_files)
    print("\n[human]")
    print(f"Source files:\n{format_source_file_list(source_files)}\nTask: {task}")

    final_state: dict[str, Any] | None = None
    for chunk in agent.graph.stream(
        graph_input,
        stream_mode=["updates", "values"],
        version="v2",
    ):
        chunk_type = chunk.get("type")
        if chunk_type == "updates":
            for step_name, update in chunk.get("data", {}).items():
                if step_name.startswith("__") or not isinstance(update, dict):
                    continue
                print(f"\n[step:{step_name}]")
                print(render_step_update(step_name, update))
        elif chunk_type == "values":
            final_state = chunk.get("data")

    if final_state is None:
        raise RuntimeError("Graph completed without a final state.")

    output = TabularTaskOutput.model_validate(final_state)
    print("\n[final answer]")
    print(output.final_answer or "")
    return output
