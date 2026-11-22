"""Bounded middleware-owned model retries (pairs with max_retries=0 on ChatOpenAI)."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

import httpx
import openai
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelRequest, ModelResponse

logger = logging.getLogger(__name__)

_TRANSIENT_EXCEPTIONS = (
    httpx.TimeoutException,
    httpx.ConnectError,
    openai.APITimeoutError,
    openai.APIConnectionError,
    openai.RateLimitError,
    openai.InternalServerError,
)


class ModelRetryMiddleware(AgentMiddleware):
    """Retries transient provider/network failures with short exponential backoff."""

    def __init__(self, *, max_retries: int = 2, base_delay_seconds: float = 0.5) -> None:
        self._max_retries = max_retries
        self._base_delay_seconds = base_delay_seconds

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        attempt = 0
        while True:
            try:
                return await handler(request)
            except _TRANSIENT_EXCEPTIONS as exc:
                if attempt >= self._max_retries:
                    raise
                attempt += 1
                delay = self._base_delay_seconds * (2 ** (attempt - 1))
                logger.warning(
                    "Transient model error (%s); retrying attempt %d/%d",
                    type(exc).__name__,
                    attempt,
                    self._max_retries,
                )
                if delay > 0:
                    await asyncio.sleep(delay)
