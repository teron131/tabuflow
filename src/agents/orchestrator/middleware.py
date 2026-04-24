"""Middleware for deterministic orchestrator skills context injection."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain.messages import HumanMessage, SystemMessage

from .skill_context import (
    format_skill_matches,
    format_skills_overview,
    list_skills_context,
    search_skills_context,
)

SKILLS_CONTEXT_HINT = "Load a situational workspace skill only when this use case needs its detailed instructions or references."


def _latest_user_text(messages: list[object]) -> str | None:
    """Return the latest human-message text from the model request."""
    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            return str(message.text).strip()
    return None


class SkillsContextMiddleware(AgentMiddleware):
    """Inject workspace-skills context into each orchestrator model call."""

    def __init__(self, *, path: str = "skills"):
        super().__init__()
        self.path = path
        self.available_skills = list_skills_context(path=path)
        self.skills_overview = format_skills_overview(self.available_skills)

    def _request_with_skills_context(self, request: ModelRequest) -> ModelRequest:
        """Return a model request augmented with deterministic skills context."""
        system_blocks = [] if request.system_message is None else list(request.system_message.content_blocks)
        system_blocks.append({"type": "text", "text": self.skills_overview})

        if latest_user_text := _latest_user_text(request.messages):
            skill_matches = format_skill_matches(
                search_skills_context(
                    latest_user_text,
                    path=self.path,
                )
            )
            system_blocks.append(
                {
                    "type": "text",
                    "text": "\n".join(
                        [
                            "Situational workspace skills matching the latest user request:",
                            skill_matches,
                            SKILLS_CONTEXT_HINT,
                        ]
                    ),
                }
            )

        return request.override(
            system_message=SystemMessage(content=system_blocks),
        )

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        """Augment the system message with available and relevant skills context."""
        return handler(self._request_with_skills_context(request))

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        """Augment async model calls with the same skills context as sync calls."""
        enriched_request = await asyncio.to_thread(self._request_with_skills_context, request)
        return await handler(enriched_request)
