"""Request-scoped OpenAI-compatible credentials extracted from UI headers."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Annotated

from fastapi import Header

from backend.app.shared.errors import LLMSettingsRequiredError


@dataclass(frozen=True)
class LLMCredentials:
    """Ephemeral OpenAI-compatible credentials supplied per request by browser UI Settings."""

    base_url: str
    model: str
    api_key: str
    enable_thinking: bool = True

    def __post_init__(self) -> None:
        normalized_url = self.base_url.strip().rstrip("/")
        normalized_model = self.model.strip()
        normalized_key = self.api_key.strip()
        if not normalized_url or not normalized_model or not normalized_key:
            raise LLMSettingsRequiredError()
        if not (normalized_url.startswith("http://") or normalized_url.startswith("https://")):
            raise LLMSettingsRequiredError("Endpoint URL must begin with http:// or https://")
        object.__setattr__(self, "base_url", normalized_url)
        object.__setattr__(self, "model", normalized_model)
        object.__setattr__(self, "api_key", normalized_key)

    def __repr__(self) -> str:
        return (
            f"LLMCredentials(base_url={self.base_url!r}, model={self.model!r}, "
            f"api_key='***', enable_thinking={self.enable_thinking!r})"
        )

    def __str__(self) -> str:
        return self.__repr__()

    def cache_fingerprint(self) -> str:
        """Return a collision-resistant cache key containing NO plaintext API key."""
        key_hash = hashlib.sha256(self.api_key.encode("utf-8")).hexdigest()[:16]
        return f"{self.base_url}|{self.model}|{self.enable_thinking}|{key_hash}"

    def redact_secrets(self, text: str) -> str:
        """Scrub this credential's API key from diagnostic output."""
        if len(self.api_key) >= 6:
            return text.replace(self.api_key, "[REDACTED]")
        return text


def _parse_bool(raw: str | None, *, default: bool = True) -> bool:
    if raw is None:
        return default
    lowered = raw.strip().lower()
    if lowered in {"1", "true", "yes", "on"}:
        return True
    if lowered in {"0", "false", "no", "off"}:
        return False
    return default


def extract_llm_credentials(
    x_llm_base_url: Annotated[str | None, Header(alias="X-LLM-Base-Url")] = None,
    x_llm_model: Annotated[str | None, Header(alias="X-LLM-Model")] = None,
    x_llm_api_key: Annotated[str | None, Header(alias="X-LLM-Api-Key")] = None,
    x_llm_enable_thinking: Annotated[str | None, Header(alias="X-LLM-Enable-Thinking")] = None,
) -> LLMCredentials:
    """Parse and validate mandatory LLM settings from request headers."""
    if not x_llm_base_url or not x_llm_model or not x_llm_api_key:
        raise LLMSettingsRequiredError()
    return LLMCredentials(
        base_url=x_llm_base_url,
        model=x_llm_model,
        api_key=x_llm_api_key,
        enable_thinking=_parse_bool(x_llm_enable_thinking, default=True),
    )


def extract_optional_llm_credentials(
    x_llm_base_url: Annotated[str | None, Header(alias="X-LLM-Base-Url")] = None,
    x_llm_model: Annotated[str | None, Header(alias="X-LLM-Model")] = None,
    x_llm_api_key: Annotated[str | None, Header(alias="X-LLM-Api-Key")] = None,
    x_llm_enable_thinking: Annotated[str | None, Header(alias="X-LLM-Enable-Thinking")] = None,
) -> LLMCredentials | None:
    """Parse optional LLM settings when provided, returning None when omitted."""
    if not (x_llm_base_url or x_llm_model or x_llm_api_key):
        return None
    return extract_llm_credentials(
        x_llm_base_url=x_llm_base_url,
        x_llm_model=x_llm_model,
        x_llm_api_key=x_llm_api_key,
        x_llm_enable_thinking=x_llm_enable_thinking,
    )
