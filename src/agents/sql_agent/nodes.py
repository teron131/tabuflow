"""LangGraph nodes and routes for SQL question answering."""

from __future__ import annotations

from collections.abc import Callable
import re
from typing import Any

from langgraph.graph import END

from ...tools.sql.query import describe_target, run_query, suggest_sql_error_repair, suggest_targets
from .state import PlannerFn, SQLAgentState

MAX_INSPECTED_TARGETS = 2
MAX_TRACE_MESSAGES = 8
_QUESTION_STOP_WORDS = {
    "a",
    "an",
    "and",
    "are",
    "by",
    "for",
    "get",
    "give",
    "how",
    "i",
    "in",
    "is",
    "me",
    "of",
    "our",
    "show",
    "tell",
    "the",
    "to",
    "us",
    "we",
    "what",
    "which",
    "with",
}
_VAGUE_QUESTION_TOKENS = {"doing", "everything", "numbers", "overall", "overview", "performance", "stats", "status", "summary"}


def _append_trace(
    state: SQLAgentState,
    message: str,
) -> list[str]:
    """Append one trace message."""
    next_trace = [*state.trace, message]
    return next_trace[-MAX_TRACE_MESSAGES:]


def _needs_clarification(question: str) -> str | None:
    """Return a clarification message when the question is too vague."""
    tokens = [token for token in re.findall(r"[a-z0-9_]+", question.lower()) if token not in _QUESTION_STOP_WORDS]
    if not tokens:
        return "Question is too vague. Ask for a specific metric, dimension, or summary target."
    if all(token in _VAGUE_QUESTION_TOKENS for token in tokens):
        return "Question is too vague. Ask for a concrete metric or breakdown, for example revenue by customer or gross profit summary."
    return None


def suggest_node(state: SQLAgentState) -> dict[str, Any]:
    """Suggest likely SQL targets for the question."""
    suggestion_result = suggest_targets(
        state.question,
        database_path=state.database_path,
        max_results=state.max_suggestions,
    )
    if suggestion_result["status"] != "ok":
        return {
            "status": "error",
            "last_error": suggestion_result["message"],
            "result": {
                "status": suggestion_result.get("status"),
                "error_type": suggestion_result.get("error_type"),
                "message": suggestion_result.get("message"),
                "database_path": suggestion_result.get("database_path"),
            },
            "trace": _append_trace(state, f"suggest failed: {suggestion_result['message']}"),
        }

    return {
        "status": "suggested",
        "suggestions": suggestion_result["suggestions"],
        "trace": _append_trace(state, f"suggested {suggestion_result['suggestion_count']} targets"),
    }


def inspect_node(state: SQLAgentState) -> dict[str, Any]:
    """Inspect the top suggested targets before planning SQL."""
    inspected_targets: list[dict[str, Any]] = []
    candidate_target_names = [
        *[target_name for target_name in state.preferred_targets if target_name],
        *[str(suggestion["name"]) for suggestion in state.suggestions if suggestion.get("name")],
    ]
    seen_target_names: set[str] = set()
    ordered_target_names: list[str] = []
    for target_name in candidate_target_names:
        if target_name in seen_target_names:
            continue
        seen_target_names.add(target_name)
        ordered_target_names.append(target_name)

    for target_name in ordered_target_names[:MAX_INSPECTED_TARGETS]:
        description = describe_target(
            target_name,
            database_path=state.database_path,
            sample_rows=state.sample_rows,
            text_value_hints=state.text_value_hints,
        )
        if description["status"] == "ok":
            inspected_targets.append(description)

    if not inspected_targets:
        return {
            "status": "error",
            "last_error": "No inspectable SQL targets were available.",
            "trace": _append_trace(state, "inspect found no usable targets"),
        }

    return {
        "status": "inspected",
        "inspected_targets": inspected_targets,
        "trace": _append_trace(state, f"inspected {len(inspected_targets)} targets"),
    }


def clarify_node(state: SQLAgentState) -> dict[str, Any]:
    """Block execution when the question needs clarification."""
    message = _needs_clarification(state.question) or "Question needs clarification before SQL planning."
    return {
        "status": "blocked",
        "last_error": message,
        "trace": _append_trace(state, "blocked for clarification"),
    }


def make_plan_node(planner: PlannerFn) -> Callable[[SQLAgentState], dict[str, Any]]:
    """Create the planning node using the supplied planner function."""

    def plan_node(state: SQLAgentState) -> dict[str, Any]:
        """Run the planning node in the SQL agent graph."""
        plan = planner(state)
        selected_targets = plan.selected_targets or [str(target["name"]) for target in state.inspected_targets]
        if not plan.ready or not plan.sql:
            return {
                "status": "blocked",
                "plan": plan,
                "selected_targets": selected_targets,
                "rationale": plan.rationale,
                "last_error": plan.blocking_reason or "Planner could not produce a safe SQL query.",
                "trace": _append_trace(state, "planner blocked execution"),
            }

        return {
            "status": "planned",
            "plan": plan,
            "candidate_sql": plan.sql,
            "selected_targets": selected_targets,
            "rationale": plan.rationale,
            "trace": _append_trace(state, f"planned SQL for {', '.join(selected_targets)}"),
        }

    return plan_node


def execute_node(state: SQLAgentState) -> dict[str, Any]:
    """Execute the planned SQL."""
    if not state.candidate_sql:
        return {
            "status": "error",
            "last_error": "No SQL query was available for execution.",
            "trace": _append_trace(state, "execute skipped because no SQL was planned"),
        }

    result = run_query(
        state.candidate_sql,
        database_path=state.database_path,
    )
    attempts = state.attempts + 1
    if result["status"] == "ok":
        return {
            "status": "complete",
            "attempts": attempts,
            "result": result,
            "last_error": None,
            "trace": _append_trace(state, f"execute succeeded on attempt {attempts}"),
        }

    repair_target_names = [str(suggestion["name"]) for suggestion in state.suggestions if suggestion.get("name")]
    repair_columns = {
        str(target["name"]): [str(column["name"]) for column in target.get("columns", []) if column.get("name")] for target in state.inspected_targets if target.get("name")
    }
    error_message = str(result["message"])
    repair_hints = suggest_sql_error_repair(
        error_message,
        available_targets=repair_target_names,
        target_columns=repair_columns,
    )
    trace_message = f"execute failed on attempt {attempts}: {error_message}"
    if repair_hints:
        trace_message += f" ({len(repair_hints)} repair hints)"
    return {
        "status": "needs_repair",
        "attempts": attempts,
        "repair_hints": repair_hints,
        "result": result,
        "last_error": error_message,
        "trace": _append_trace(state, trace_message),
    }


def repair_node(state: SQLAgentState) -> dict[str, Any]:
    """Mark a repair pass before planning again."""
    return {
        "status": "repairing",
        "repair_count": state.repair_count + 1,
        "trace": _append_trace(state, f"repair pass {state.repair_count + 1}"),
    }


def route_after_suggest(state: SQLAgentState) -> str:
    """Route after target suggestion."""
    if state.status != "suggested":
        return END
    if _needs_clarification(state.question):
        return "clarify"
    return "inspect"


def route_after_inspect(state: SQLAgentState) -> str:
    """Route after target inspection."""
    return "plan" if state.status == "inspected" else END


def route_after_plan(state: SQLAgentState) -> str:
    """Route after SQL planning."""
    return "execute" if state.status == "planned" else END


def route_after_execute(state: SQLAgentState) -> str:
    """Route after execution."""
    if state.status == "complete":
        return END
    if state.status == "needs_repair" and state.repair_count < state.max_repairs:
        return "repair"
    return END
