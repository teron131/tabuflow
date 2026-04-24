"""LangGraph nodes and routes for SQL question answering."""

from __future__ import annotations

from collections.abc import Callable
import re
from typing import Any

from langgraph.graph import END
import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

from ...tools.sql.query import describe_target, run_query, suggest_sql_error_repair, suggest_targets
from ..trace_utils import SQL_STAGE, append_stage_trace
from .state import PlannerFn, SQLAgentState

MAX_INSPECTED_TARGETS = 4
MAX_PREFERRED_INSPECTED_TARGETS = 6
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
_EXPLAIN_PREFIX = re.compile(r"^\s*EXPLAIN(?:\s+QUERY\s+PLAN)?\s+", re.IGNORECASE)


def _append_trace(
    state: SQLAgentState,
    message: str,
) -> list[str]:
    """Append one trace message."""
    return append_stage_trace(state.trace, SQL_STAGE, message)


def _needs_clarification(question: str) -> str | None:
    """Return a clarification message when the question is too vague."""
    tokens = [token for token in re.findall(r"[a-z0-9_]+", question.lower()) if token not in _QUESTION_STOP_WORDS]
    if not tokens:
        return "Question is too vague. Ask for a specific metric, dimension, or summary target."
    if all(token in _VAGUE_QUESTION_TOKENS for token in tokens):
        return "Question is too vague. Ask for a concrete metric or breakdown, for example revenue by customer or gross profit summary."
    return None


def _dedupe_names(names: list[str]) -> list[str]:
    """Return non-empty target names in first-seen order."""
    ordered_names: list[str] = []
    seen_names: set[str] = set()
    for name in names:
        target_name = name.strip()
        if not target_name or target_name in seen_names:
            continue
        seen_names.add(target_name)
        ordered_names.append(target_name)
    return ordered_names


def _suggested_target_names(state: SQLAgentState) -> list[str]:
    """Return suggested target names in suggestion-rank order."""
    return _dedupe_names([str(suggestion["name"]) for suggestion in state.suggestions if suggestion.get("name")])


def _preferred_target_names(state: SQLAgentState) -> list[str]:
    """Return current-run target names in orchestrator-provided order."""
    return _dedupe_names([str(target_name) for target_name in state.preferred_targets])


def _ordered_inspection_target_names(state: SQLAgentState) -> list[str]:
    """Return targets to inspect, ranking current-run targets by suggestion relevance."""
    suggested_names = _suggested_target_names(state)
    preferred_names = _preferred_target_names(state)
    if not preferred_names:
        return suggested_names

    preferred_name_set = set(preferred_names)
    suggested_preferred_names = [target_name for target_name in suggested_names if target_name in preferred_name_set]
    remaining_preferred_names = [target_name for target_name in preferred_names if target_name not in set(suggested_preferred_names)]
    suggested_other_names = [target_name for target_name in suggested_names if target_name not in preferred_name_set]
    return _dedupe_names([*suggested_preferred_names, *remaining_preferred_names, *suggested_other_names])


def _inspection_limit(state: SQLAgentState) -> int:
    """Return a bounded inspection limit that gives current-run candidates enough room."""
    preferred_count = len(_preferred_target_names(state))
    if not preferred_count:
        return MAX_INSPECTED_TARGETS
    return min(max(MAX_INSPECTED_TARGETS, preferred_count), MAX_PREFERRED_INSPECTED_TARGETS)


def _allowed_sql_target_names(state: SQLAgentState) -> set[str]:
    """Return target names the planned SQL is allowed to reference."""
    preferred_names = set(_preferred_target_names(state))
    if preferred_names:
        return preferred_names
    return {str(target["name"]) for target in state.inspected_targets if target.get("name")}


def _referenced_sql_target_names(sql: str) -> set[str]:
    """Return base table/view names referenced by one SQL statement."""
    parsed = sqlglot.parse_one(_EXPLAIN_PREFIX.sub("", sql, count=1), read="sqlite")
    cte_names = {cte.alias for cte in parsed.find_all(exp.CTE) if cte.alias}
    return {table.name for table in parsed.find_all(exp.Table) if table.name and table.name not in cte_names}


def _sql_target_violation(state: SQLAgentState) -> str | None:
    """Return an execution-blocking message when SQL leaves the allowed target set."""
    if not state.candidate_sql:
        return None

    allowed_targets = _allowed_sql_target_names(state)
    if not allowed_targets:
        return "No inspected or current-run SQL targets are available for execution."

    try:
        referenced_targets = _referenced_sql_target_names(state.candidate_sql)
    except ParseError as exc:
        return f"Planned SQL could not be parsed before execution: {exc}"

    if not referenced_targets:
        return "Planned SQL must reference at least one inspected or current-run target."

    allowed_target_names = {target_name.lower() for target_name in allowed_targets}
    disallowed_targets = sorted(target_name for target_name in referenced_targets if target_name.lower() not in allowed_target_names)
    if disallowed_targets:
        allowed_preview = ", ".join(sorted(allowed_targets)[:MAX_PREFERRED_INSPECTED_TARGETS])
        return f"Planned SQL referenced target(s) outside the allowed set: {', '.join(disallowed_targets)}. Allowed targets: {allowed_preview}."

    return None


def suggest_node(state: SQLAgentState) -> dict[str, Any]:
    """Suggest likely SQL targets for the question."""
    suggestion_result = suggest_targets(
        state.question,
        database_path=state.database_path,
        max_results=max(state.max_suggestions, min(len(_preferred_target_names(state)), MAX_PREFERRED_INSPECTED_TARGETS)),
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
    ordered_target_names = _ordered_inspection_target_names(state)

    for target_name in ordered_target_names[: _inspection_limit(state)]:
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

    attempts = state.attempts + 1
    target_violation = _sql_target_violation(state)
    if target_violation is not None:
        return {
            "status": "needs_repair",
            "attempts": attempts,
            "result": {
                "status": "error",
                "error_type": "disallowed_sql_target",
                "message": target_violation,
                "database_path": state.database_path,
            },
            "last_error": target_violation,
            "trace": _append_trace(state, f"execute blocked before SQL run: {target_violation}"),
        }

    result = run_query(
        state.candidate_sql,
        database_path=state.database_path,
    )
    if result["status"] == "ok":
        return {
            "status": "complete",
            "attempts": attempts,
            "result": result,
            "last_error": None,
            "trace": _append_trace(state, f"execute succeeded on attempt {attempts}"),
        }

    repair_target_names = sorted(_allowed_sql_target_names(state))
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
