"""Read-only tool guard middleware for explorer and reviewer subagents."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

_DISALLOWED_MUTATING_TOOLS = frozenset({"write_file", "edit_file", "delete", "execute"})


def _tool_name(tool_obj: object) -> str:
    if isinstance(tool_obj, dict):
        fn = tool_obj.get("function")
        if isinstance(fn, dict) and isinstance(fn.get("name"), str):
            return fn["name"]
        return str(tool_obj.get("name") or "")
    return str(getattr(tool_obj, "name", ""))


class ReadOnlyToolGuardMiddleware(AgentMiddleware):
    """Hides and rejects mutating file/shell tools on read-only subagents."""

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        filtered = [t for t in request.tools if _tool_name(t) not in _DISALLOWED_MUTATING_TOOLS]
        return await handler(request.override(tools=filtered))

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        name = str(request.tool_call.get("name") or "")
        if name in _DISALLOWED_MUTATING_TOOLS:
            return ToolMessage(
                content=f"Error: Tool '{name}' is disabled in read-only subagent.",
                name=name,
                tool_call_id=str(request.tool_call.get("id") or ""),
                status="error",
            )
        return await handler(request)
