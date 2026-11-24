"""OpenAI-compatible ChatModel adapter preserving reasoning_content for Qwen 3.8 Flash."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Mapping
from typing import Any

import httpx
import openai
from langchain_core.callbacks import AsyncCallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage
from langchain_core.outputs import ChatGenerationChunk, ChatResult
from langchain_openai import ChatOpenAI

from backend.app.shared.errors import LLMUpstreamError
from backend.app.shared.llm_credentials import LLMCredentials

logger = logging.getLogger(__name__)


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

    def _disable_rejected_params(self, exc: BaseException) -> bool:
        lowered = str(exc).lower()
        changed = False

        if (
            isinstance(self.extra_body, Mapping)
            and "enable_thinking" in self.extra_body
            and ("enable_thinking" in lowered or "extra inputs" in lowered)
        ):
            remaining = {k: v for k, v in self.extra_body.items() if k != "enable_thinking"}
            self.extra_body = remaining or None
            logger.info(
                "agent.enable_thinking_fallback",
                extra={
                    "event": "agent.enable_thinking_fallback",
                    "model": self.model_name,
                },
            )
            changed = True

        rejection_hint = any(
            token in lowered
            for token in ("unsupported", "not supported", "unrecognized", "extra inputs", "invalid")
        )
        if rejection_hint and self.temperature is not None and "temperature" in lowered:
            self.temperature = None
            logger.info(
                "agent.sampling_param_fallback",
                extra={
                    "event": "agent.sampling_param_fallback",
                    "model": self.model_name,
                    "param": "temperature",
                },
            )
            changed = True

        if rejection_hint and self.top_p is not None and "top_p" in lowered:
            self.top_p = None
            logger.info(
                "agent.sampling_param_fallback",
                extra={
                    "event": "agent.sampling_param_fallback",
                    "model": self.model_name,
                    "param": "top_p",
                },
            )
            changed = True

        return changed

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        for _ in range(4):
            try:
                return await super()._agenerate(
                    messages, stop=stop, run_manager=run_manager, **kwargs
                )
            except openai.BadRequestError as exc:
                if not self._disable_rejected_params(exc):
                    raise
        return await super()._agenerate(messages, stop=stop, run_manager=run_manager, **kwargs)

    async def _astream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        *,
        stream_usage: bool | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatGenerationChunk]:
        for _ in range(4):
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
                return
            except openai.BadRequestError as exc:
                if emitted or not self._disable_rejected_params(exc):
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
    """Constructs OpenAI-compatible chat models sharing one managed httpx pool."""

    def __init__(self, *, http_transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._http_transport = http_transport
        self._sync_client: httpx.Client | None = None
        self._async_client: httpx.AsyncClient | None = None

    def _get_sync_client(self) -> httpx.Client:
        existing: httpx.Client | None = getattr(self, "_sync_client", None)
        if existing is None or existing.is_closed:
            existing = httpx.Client(trust_env=False, timeout=120.0)
            self._sync_client = existing
        return existing

    def _get_async_client(self) -> httpx.AsyncClient:
        existing: httpx.AsyncClient | None = getattr(self, "_async_client", None)
        if existing is None or existing.is_closed:
            transport: httpx.AsyncBaseTransport | None = getattr(self, "_http_transport", None)
            existing = httpx.AsyncClient(
                transport=transport,
                trust_env=False,
                timeout=120.0,
            )
            self._async_client = existing
        return existing

    async def aclose(self) -> None:
        """Close pooled HTTP clients cleanly during application shutdown."""
        async_client: httpx.AsyncClient | None = getattr(self, "_async_client", None)
        if async_client is not None and not async_client.is_closed:
            await async_client.aclose()
        sync_client: httpx.Client | None = getattr(self, "_sync_client", None)
        if sync_client is not None and not sync_client.is_closed:
            sync_client.close()

    def build(self, creds: LLMCredentials) -> BaseChatModel:
        extra_body: dict[str, Any] | None = (
            {"enable_thinking": True} if creds.enable_thinking else None
        )
        return ReasoningChatOpenAI(
            model=creds.model,
            base_url=creds.base_url,
            api_key=creds.api_key,  # type: ignore[arg-type]
            temperature=0.2,
            top_p=0.85,
            max_retries=0,
            streaming=True,
            extra_body=extra_body,
            http_client=self._get_sync_client(),
            http_async_client=self._get_async_client(),
        )

    async def ping_llm(self, creds: LLMCredentials) -> dict[str, object]:
        """Verify OpenAI-compatible endpoint reachability without logging credentials."""
        models_url = f"{creds.base_url}/models"
        headers = {"Authorization": f"Bearer {creds.api_key}"}
        client = self._get_async_client()
        try:
            resp = await client.get(models_url, headers=headers, timeout=10.0)
            if resp.status_code in {404, 405}:
                resp = await client.post(
                    f"{creds.base_url}/chat/completions",
                    headers=headers,
                    json={
                        "model": creds.model,
                        "messages": [{"role": "user", "content": "ping"}],
                        "max_tokens": 1,
                    },
                    timeout=10.0,
                )
        except httpx.HTTPError as exc:
            msg = creds.redact_secrets(
                f"Failed to connect to {creds.base_url}: {type(exc).__name__}"
            )
            logger.warning(
                "llm.ping_failed",
                extra={
                    "event": "llm.ping_failed",
                    "base_url": creds.base_url,
                    "model": creds.model,
                    "error_type": type(exc).__name__,
                },
            )
            raise LLMUpstreamError(msg) from exc

        if resp.status_code >= 400:
            snippet = creds.redact_secrets(resp.text[:240].strip())
            detail_suffix = f": {snippet}" if snippet else ""
            logger.warning(
                "llm.ping_http_error",
                extra={
                    "event": "llm.ping_http_error",
                    "base_url": creds.base_url,
                    "model": creds.model,
                    "status_code": resp.status_code,
                },
            )
            raise LLMUpstreamError(
                f"LLM endpoint returned HTTP {resp.status_code}{detail_suffix}",
                details={"status_code": resp.status_code},
            )
        return {"ok": True, "base_url": creds.base_url, "model": creds.model}
