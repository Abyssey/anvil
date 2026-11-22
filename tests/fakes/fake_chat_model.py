"""Deterministic scripted ChatModel test double supporting reasoning_content and tool calls."""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator, Callable, Iterator, Sequence
from dataclasses import dataclass, field
from typing import Any

from langchain_core.callbacks import AsyncCallbackManagerForLLMRun, CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage, ToolCall
from langchain_core.messages.tool import ToolCallChunk
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool
from pydantic import Field, PrivateAttr


@dataclass
class ScriptedTurn:
    """Single deterministic response turn produced by ScriptedReasoningChatModel."""

    reasoning: str = ""
    content: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)


class ScriptedReasoningChatModel(BaseChatModel):
    """Offline chat model test double preserving reasoning_content and tool calls."""

    turns: list[ScriptedTurn] = Field(default_factory=list)
    model_name: str = "qwen3.8-flash-scripted"
    bound_tools: list[Any] = Field(default_factory=list)
    _cursor: list[int] = PrivateAttr(default_factory=lambda: [0])
    _recorded_messages: list[list[BaseMessage]] = PrivateAttr(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "scripted-reasoning-chat"

    @property
    def recorded_calls(self) -> list[list[BaseMessage]]:
        return self._recorded_messages

    def bind_tools(
        self,
        tools: Sequence[dict[str, Any] | type | Callable[..., Any] | BaseTool],
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ) -> Runnable[Any, AIMessage]:
        clone = self.model_copy(update={"bound_tools": list(tools)})
        clone._cursor = self._cursor
        clone._recorded_messages = self._recorded_messages
        return clone

    def _next_turn(self, messages: list[BaseMessage]) -> ScriptedTurn:
        self._recorded_messages.append(list(messages))
        idx = self._cursor[0]
        self._cursor[0] += 1
        if idx < len(self.turns):
            return self.turns[idx]
        return ScriptedTurn(content="Done.")

    def _build_ai_message(self, turn: ScriptedTurn) -> AIMessage:
        additional: dict[str, Any] = {}
        if turn.reasoning:
            additional["reasoning_content"] = turn.reasoning
        tool_calls: list[ToolCall] = []
        for idx, tc in enumerate(turn.tool_calls):
            tool_calls.append(
                ToolCall(
                    name=str(tc["name"]),
                    args=dict(tc.get("args") or {}),
                    id=str(tc.get("id") or f"call_{uuid.uuid4().hex[:8]}_{idx}"),
                )
            )
        return AIMessage(
            content=turn.content,
            additional_kwargs=additional,
            tool_calls=tool_calls,
        )

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        turn = self._next_turn(messages)
        msg = self._build_ai_message(turn)
        return ChatResult(generations=[ChatGeneration(message=msg)])

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        return self._generate(messages, stop=stop, **kwargs)

    def _stream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> Iterator[ChatGenerationChunk]:
        turn = self._next_turn(messages)
        if turn.reasoning:
            yield ChatGenerationChunk(
                message=AIMessageChunk(
                    content="",
                    additional_kwargs={"reasoning_content": turn.reasoning},
                )
            )
        if turn.content:
            yield ChatGenerationChunk(message=AIMessageChunk(content=turn.content))
        for idx, tc in enumerate(turn.tool_calls):
            tc_id = str(tc.get("id") or f"call_{uuid.uuid4().hex[:8]}_{idx}")
            yield ChatGenerationChunk(
                message=AIMessageChunk(
                    content="",
                    tool_call_chunks=[
                        ToolCallChunk(
                            name=str(tc["name"]),
                            args=json.dumps(tc.get("args") or {}),
                            id=tc_id,
                            index=idx,
                        )
                    ],
                )
            )

    async def _astream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatGenerationChunk]:
        for chunk in self._stream(messages, stop=stop, **kwargs):
            yield chunk
