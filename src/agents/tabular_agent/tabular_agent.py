"""Public entrypoints for the deterministic tabular analysis workflow."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Literal

from langchain.messages import ToolMessage

from ...clients.openai import ChatOpenAI
from ...tools import list_skills
from ...utils import write_langgraph_artifacts
from .graph import create_tabular_graph
from .payloads import compact_extracted_targets, compact_sql_agent_output, compact_sql_result, compact_validation_feedback
from .prompts import format_source_file_list
from .state import TabularTaskOutput, build_graph_input

DEFAULT_MODEL_ENV = "FAST_LLM"
DEFAULT_MODEL = "openai/gpt-5.4-nano"
DEFAULT_REASONING_EFFORT: Literal["minimal", "low", "medium", "high", "xhigh"] = "high"
SKILLS_PATH = "skills"
STEP_FIELDS: dict[str, dict[str, Any]] = {
    "skills": {
        "matched_skill_names": [],
    },
    "prep": {
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
    "validate": {
        "status": None,
        "last_error": None,
        "validation_feedback": None,
        "validation_attempts": 0,
    },
    "save": {
        "status": None,
        "saved_view_name": None,
        "last_error": None,
    },
    "package": {
        "result_message": None,
        "result_artifact": None,
    },
}


def _build_system_prompt(prompt: str) -> str:
    """Build the base system prompt from the workspace skills list."""
    skills_result = list_skills.invoke({"path": SKILLS_PATH})
    skills = skills_result.get("skills", [])
    skill_lines = [f"- {skill['name']}: {skill['description']} ({skill['path']})" for skill in skills]
    section_lines = (
        [
            f"Workspace skills discovered by `list_skills` under `{SKILLS_PATH}`:",
            *skill_lines,
        ]
        if skill_lines
        else [f"Workspace skills discovered by `list_skills` under `{SKILLS_PATH}`: none."]
    )
    if diagnostics := skills_result.get("diagnostics", []):
        section_lines.extend(["Skill discovery diagnostics:", *(f"- {message}" for message in diagnostics)])

    parts = [part.strip() for part in (prompt, "\n".join(section_lines)) if part.strip()]
    return "\n\n".join(parts)


class TabularTaskAgent:
    """Deterministic tabular workflow with a pinned save-view step."""

    def __init__(
        self,
        *,
        prompt: str = "",
        root_dir: str | Path | None = None,
    ):
        self.prompt = prompt
        self.root_dir = root_dir
        self.system_prompt = _build_system_prompt(self.prompt)
        self.model = os.getenv(DEFAULT_MODEL_ENV) or DEFAULT_MODEL
        self.llm = ChatOpenAI(
            model=self.model,
            temperature=0,
            reasoning_effort=DEFAULT_REASONING_EFFORT,
        )
        self.graph = self.build_graph()
        self.graph_artifacts = write_langgraph_artifacts(
            self.graph,
            filename_stem="tabular-agent-graph",
        )

    def build_graph(self):
        """Build the deterministic workflow for parent-chain orchestration."""
        return create_tabular_graph(
            llm=self.llm,
            prompt=self.system_prompt,
            root_dir=self.root_dir,
        )

    def invoke(
        self,
        task: str,
        *,
        source_files: list[str],
    ) -> TabularTaskOutput:
        """Run the graph once and validate the final output."""
        input = build_graph_input(task, source_files)
        result = self.graph.invoke(input)
        return TabularTaskOutput.model_validate(result)


def render_step_update(
    step_name: str,
    update: dict[str, Any],
) -> str:
    """Render one streamed graph update compactly."""
    if step_fields := STEP_FIELDS.get(step_name):
        payload = {
            field_name: update.get(
                field_name,
                default_value,
            )
            for field_name, default_value in step_fields.items()
        }
        if step_name == "prep":
            payload["extracted_targets"] = compact_extracted_targets(list(payload.get("extracted_targets", [])))
        if step_name == "sql":
            payload["sql_result"] = compact_sql_result(payload.get("sql_result"))
            if "sql_agent_output" in update:
                payload["sql_agent_output"] = compact_sql_agent_output(update.get("sql_agent_output"))
        if step_name == "validate":
            payload["validation_feedback"] = compact_validation_feedback(payload.get("validation_feedback"))
        if step_name == "package":
            return str(payload.get("result_message", "")).strip()
        return json.dumps(
            payload,
            ensure_ascii=True,
            sort_keys=True,
        )
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
    """Run the deterministic graph and stream workflow packaging updates."""
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
    print("\n[result]")
    print(output.result_message or "")
    return output


def build_tool_message(
    output: TabularTaskOutput,
    *,
    tool_call_id: str,
    name: str = "run_tabular_workflow",
) -> ToolMessage:
    """Convert a completed workflow result into a LangChain ToolMessage."""
    if not output.result_message:
        raise ValueError("Tabular workflow output is missing `result_message`.")
    return ToolMessage(
        content=output.result_message,
        tool_call_id=tool_call_id,
        name=name,
        artifact=output.result_artifact,
    )
