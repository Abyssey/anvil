"""Pure request-scoped OpenAI-compatible credentials value object (FastAPI-free)."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from backend.app.shared.errors import LLMSettingsRequiredError


def _normalize_base_url(raw_url: str) -> str:
    url = raw_url.strip().rstrip("/")
    lowered = url.lower()
    for suffix in ("/chat/completions", "/models"):
        if lowered.endswith(suffix):
            url = url[: -len(suffix)].rstrip("/")
            break
    return url


def parse_bool_flag(raw: str | None, *, default: bool = True) -> bool:
    """Parse boolean string flags from configuration or headers."""
    if raw is None:
        return default
    lowered = raw.strip().lower()
    if lowered in {"1", "true", "yes", "on"}:
        return True
    if lowered in {"0", "false", "no", "off"}:
        return False
    return default


@dataclass(frozen=True)
class LLMCredentials:
    """Ephemeral OpenAI-compatible credentials supplied per request by browser UI Settings."""

    base_url: str
    model: str
    api_key: str
    enable_thinking: bool = True

    def __post_init__(self) -> None:
        normalized_url = _normalize_base_url(self.base_url)
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
