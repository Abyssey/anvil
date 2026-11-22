"""Recoverable tool error wrapper that preserves LangGraph interrupts (Open SWE pattern)."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage
from langgraph.errors import GraphBubbleUp, NodeCancelledError
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

logger = logging.getLogger(__name__)


class ToolErrorMiddleware(AgentMiddleware):
    """Converts recoverable tool failures into structured ToolMessages without hiding interrupts."""

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        try:
            return await handler(request)
        except (GraphBubbleUp, NodeCancelledError, asyncio.CancelledError):
            raise
        except Exception as exc:
            tool_name = str(request.tool_call.get("name") or "unknown")
            tool_call_id = str(request.tool_call.get("id") or "")
            logger.warning(
                "agent.tool_error",
                extra={
                    "event": "agent.tool_error",
                    "tool": tool_name,
                    "error_type": type(exc).__name__,
                    "detail": str(exc)[:300],
                },
            )
            payload = json.dumps(
                {
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "tool": tool_name,
                    "hint": "Inspect the error message and adjust arguments or try another tool.",
                }
            )
            return ToolMessage(
                content=payload,
                name=tool_name,
                tool_call_id=tool_call_id,
                status="error",
            )
