"""State schema for the top-level orchestrator graph."""

from __future__ import annotations

from typing import Any, NotRequired

from langchain.agents import AgentState


class OrchestratorState(AgentState[None]):
    """Messages-first state for the orchestrator graph."""

    latest_artifact: NotRequired[dict[str, Any]]
    active_worker: NotRequired[str | None]
