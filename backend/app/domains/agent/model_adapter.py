"""OpenAI-compatible ChatModel adapter preserving reasoning_content for Qwen 3.8 Flash."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from typing import Any

import httpx
import openai
from langchain_core.callbacks import AsyncCallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage
from langchain_core.outputs import ChatGenerationChunk, ChatResult
from langchain_openai import ChatOpenAI

from backend.app.shared.llm_credentials import LLMCredentials


def _extract_reasoning(obj: object) -> str | None:
    if isinstance(obj, Mapping):
        val = obj.get("reasoning_content")
        return val if isinstance(val, str) and val else None
    val = getattr(obj, "reasoning_content", None)
    if isinstance(val, str) and val:
        return val
    extra = getattr(obj, "model_extra", None)
    if isinstance(extra, Mapping):
        extra_val = extra.get("reasoning_content")
        if isinstance(extra_val, str) and extra_val:
            return extra_val
    return None


class ReasoningChatOpenAI(ChatOpenAI):
    """ChatOpenAI subclass preserving provider delta/message `reasoning_content`."""

    def _disable_rejected_thinking(self, exc: BaseException) -> bool:
        if not (isinstance(self.extra_body, Mapping) and "enable_thinking" in self.extra_body):
            return False
        lowered = str(exc).lower()
        if "enable_thinking" not in lowered and "extra inputs" not in lowered:
            return False
        remaining = {k: v for k, v in self.extra_body.items() if k != "enable_thinking"}
        self.extra_body = remaining or None
        return True

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        try:
            return await super()._agenerate(
                messages, stop=stop, run_manager=run_manager, **kwargs
            )
        except openai.BadRequestError as exc:
            if self._disable_rejected_thinking(exc):
                return await super()._agenerate(
                    messages, stop=stop, run_manager=run_manager, **kwargs
                )
            raise

    async def _astream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        *,
        stream_usage: bool | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatGenerationChunk]:
        emitted = False
        try:
            async for chunk in super()._astream(
                messages,
                stop=stop,
                run_manager=run_manager,
                stream_usage=stream_usage,
                **kwargs,
            ):
                emitted = True
                yield chunk
        except openai.BadRequestError as exc:
            if not emitted and self._disable_rejected_thinking(exc):
                async for chunk in super()._astream(
                    messages,
                    stop=stop,
                    run_manager=run_manager,
                    stream_usage=stream_usage,
                    **kwargs,
                ):
                    yield chunk
                return
            raise

    def _convert_chunk_to_generation_chunk(
        self,
        chunk: dict[str, Any],
        default_chunk_class: type,
        base_generation_info: dict[str, Any] | None,
    ) -> ChatGenerationChunk | None:
        gen_chunk = super()._convert_chunk_to_generation_chunk(
            chunk, default_chunk_class, base_generation_info
        )
        if gen_chunk is None:
            return None
        choices = chunk.get("choices") or chunk.get("chunk", {}).get("choices", [])
        if choices and isinstance(choices[0], Mapping):
            delta = choices[0].get("delta")
            if reasoning := _extract_reasoning(delta):
                gen_chunk.message.additional_kwargs["reasoning_content"] = reasoning
        return gen_chunk

    def _create_chat_result(
        self,
        response: dict[str, Any] | openai.BaseModel,
        generation_info: dict[str, Any] | None = None,
    ) -> ChatResult:
        result = super()._create_chat_result(response, generation_info)
        if isinstance(response, Mapping):
            choices = response.get("choices") or []
            for idx, choice in enumerate(choices):
                if (
                    idx < len(result.generations)
                    and isinstance(choice, Mapping)
                    and (reasoning := _extract_reasoning(choice.get("message")))
                ):
                    result.generations[idx].message.additional_kwargs[
                        "reasoning_content"
                    ] = reasoning
        elif isinstance(response, openai.BaseModel):
            choices = getattr(response, "choices", None) or []
            for idx, choice in enumerate(choices):
                if idx < len(result.generations):
                    msg = getattr(choice, "message", None)
                    if reasoning := _extract_reasoning(msg):
                        result.generations[idx].message.additional_kwargs[
                            "reasoning_content"
                        ] = reasoning
        return result


class ModelFactory:
    """Constructs OpenAI-compatible chat models from per-request UI credentials."""

    def build(self, creds: LLMCredentials) -> BaseChatModel:
        extra_body: dict[str, Any] | None = (
            {"enable_thinking": True} if creds.enable_thinking else None
        )
        return ReasoningChatOpenAI(
            model=creds.model,
            base_url=creds.base_url,
            api_key=creds.api_key,  # type: ignore[arg-type]
            max_retries=0,
            streaming=True,
            extra_body=extra_body,
            http_client=httpx.Client(trust_env=False, timeout=120.0),
            http_async_client=httpx.AsyncClient(trust_env=False, timeout=120.0),
        )
