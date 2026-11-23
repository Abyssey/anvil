"""Secret-safe structured logging with ContextVar correlation (100% Python stdlib)."""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import traceback
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime

_REQUEST_ID: ContextVar[str | None] = ContextVar("request_id", default=None)
_SESSION_ID: ContextVar[str | None] = ContextVar("session_id", default=None)
_RUN_MODE: ContextVar[str | None] = ContextVar("run_mode", default=None)
_ACTIVE_SECRETS: ContextVar[tuple[str, ...]] = ContextVar("active_secrets", default=())

_MIN_SECRET_LEN = 6
_REDACTED = "[REDACTED]"

_AUTH_BEARER_RE = re.compile(r"(?i)(authorization\s*:\s*bearer\s+)\S+")
_BEARER_RE = re.compile(r"(?i)(\bbearer\s+)(?!\[REDACTED\])[A-Za-z0-9._-]{6,}")
_KEY_VALUE_RE = re.compile(
    r"(?i)(['\"]?(?:x-llm-api-key|api[_-]?key|authorization|access_token|secret|password)"
    r"['\"]?\s*[:=]\s*['\"]?)(?!bearer\s+\[REDACTED\])(?!\[REDACTED\])[^\s,'\"&};]+"
)
_TOKEN_PREFIX_RE = re.compile(r"\b(?:sk-|AIza|ghp_|gho_|xox[baprs]-)[A-Za-z0-9._-]{4,}")

_SENSITIVE_KEY_NAMES: frozenset[str] = frozenset(
    {
        "x-llm-api-key",
        "x_llm_api_key",
        "api_key",
        "api-key",
        "apikey",
        "authorization",
        "access_token",
        "access-token",
        "secret",
        "password",
    }
)

_STANDARD_RECORD_ATTRS: frozenset[str] = frozenset(
    {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "message",
        "module",
        "msecs",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "taskName",
        "thread",
        "threadName",
    }
)

_CLAMPED_THIRD_PARTY_LOGGERS: tuple[str, ...] = (
    "httpx",
    "httpcore",
    "openai",
    "aiosqlite",
    "watchfiles",
)

_CONFIGURED = False


def get_request_id() -> str | None:
    return _REQUEST_ID.get()


def get_session_id() -> str | None:
    return _SESSION_ID.get()


def get_run_mode() -> str | None:
    return _RUN_MODE.get()


def get_active_secrets() -> tuple[str, ...]:
    return _ACTIVE_SECRETS.get()


@contextmanager
def bind_log_context(
    *,
    request_id: str | None = None,
    session_id: str | None = None,
    mode: str | None = None,
    secrets: Iterable[str] = (),
) -> Iterator[None]:
    """Bind correlation fields and active secrets for the current async/sync scope."""
    tokens: list[tuple[ContextVar[object], object]] = []
    if request_id is not None:
        tokens.append((_REQUEST_ID, _REQUEST_ID.set(request_id)))  # type: ignore[arg-type]
    if session_id is not None:
        tokens.append((_SESSION_ID, _SESSION_ID.set(session_id)))  # type: ignore[arg-type]
    if mode is not None:
        tokens.append((_RUN_MODE, _RUN_MODE.set(mode)))  # type: ignore[arg-type]

    incoming = [s for s in secrets if isinstance(s, str) and len(s) >= _MIN_SECRET_LEN]
    if incoming:
        existing = _ACTIVE_SECRETS.get()
        merged = tuple(dict.fromkeys((*existing, *incoming)))
        tokens.append((_ACTIVE_SECRETS, _ACTIVE_SECRETS.set(merged)))  # type: ignore[arg-type]

    try:
        yield
    finally:
        for var, token in reversed(tokens):
            var.reset(token)  # type: ignore[arg-type]


def redact_text(text: str, extra_secrets: Iterable[str] = ()) -> str:
    """Scrub active secrets, credential headers, key/value pairs, and common token prefixes."""
    if not text:
        return text

    result = text
    candidates = [
        s
        for s in (*_ACTIVE_SECRETS.get(), *extra_secrets)
        if isinstance(s, str) and len(s) >= _MIN_SECRET_LEN
    ]
    if candidates:
        for secret in sorted(set(candidates), key=len, reverse=True):
            result = result.replace(secret, _REDACTED)

    result = _AUTH_BEARER_RE.sub(rf"\1{_REDACTED}", result)
    result = _BEARER_RE.sub(rf"\1{_REDACTED}", result)
    result = _KEY_VALUE_RE.sub(rf"\1{_REDACTED}", result)
    result = _TOKEN_PREFIX_RE.sub(_REDACTED, result)
    return result


def redact_value(value: object, extra_secrets: Iterable[str] = ()) -> object:
    """Recursively redact strings, mappings, and collections for structured log extras."""
    if isinstance(value, str):
        return redact_text(value, extra_secrets)
    if isinstance(value, Mapping):
        cleaned: dict[str, object] = {}
        for k, v in value.items():
            key_str = str(k)
            if key_str.lower() in _SENSITIVE_KEY_NAMES and v is not None:
                cleaned[key_str] = _REDACTED
            else:
                cleaned[key_str] = redact_value(v, extra_secrets)
        return cleaned
    if isinstance(value, list):
        return [redact_value(item, extra_secrets) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_value(item, extra_secrets) for item in value)
    if isinstance(value, set | frozenset):
        return [redact_value(item, extra_secrets) for item in value]
    if isinstance(value, int | float | bool) or value is None:
        return value
    return redact_text(str(value), extra_secrets)


class SecretRedactionFilter(logging.Filter):
    """Handler-level filter injecting correlation ContextVars and redacting all secrets."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not getattr(record, "request_id", None):
            record.request_id = _REQUEST_ID.get()
        if not getattr(record, "session_id", None):
            record.session_id = _SESSION_ID.get()
        if not getattr(record, "mode", None):
            record.mode = _RUN_MODE.get()

        try:
            rendered = record.getMessage()
        except Exception:
            rendered = f"{record.msg} {record.args}"
        record.msg = redact_text(rendered)
        record.args = ()

        if record.exc_info and record.exc_info != (None, None, None):
            formatted_tb = "".join(traceback.format_exception(*record.exc_info)).rstrip()
            record.exc_text = redact_text(formatted_tb)
            record.exc_info = None
        elif record.exc_text:
            record.exc_text = redact_text(record.exc_text)

        if record.stack_info:
            record.stack_info = redact_text(record.stack_info)

        for key in list(record.__dict__.keys()):
            if key in _STANDARD_RECORD_ATTRS or key.startswith("_"):
                continue
            val = record.__dict__[key]
            if key.lower() in _SENSITIVE_KEY_NAMES and val is not None:
                record.__dict__[key] = _REDACTED
            else:
                record.__dict__[key] = redact_value(val)

        return True


def _extract_extra_fields(record: logging.LogRecord) -> dict[str, object]:
    extras: dict[str, object] = {}
    for key, val in record.__dict__.items():
        if key in _STANDARD_RECORD_ATTRS or key in {"request_id", "session_id", "mode"}:
            continue
        if key.startswith("_") or val is None:
            continue
        extras[key] = val
    return extras


class JsonFormatter(logging.Formatter):
    """Single-line JSON structured log formatter."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "request_id": getattr(record, "request_id", None),
            "session_id": getattr(record, "session_id", None),
            "mode": getattr(record, "mode", None),
        }
        payload.update(_extract_extra_fields(record))
        if record.exc_text:
            payload["traceback"] = record.exc_text
        elif record.exc_info and record.exc_info != (None, None, None):
            payload["traceback"] = redact_text(self.formatException(record.exc_info))
        if record.stack_info:
            payload["stack_info"] = record.stack_info
        return json.dumps(payload, default=str, ensure_ascii=False)


class ConsoleFormatter(logging.Formatter):
    """Concise, readable console formatter for local development."""

    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.fromtimestamp(record.created, tz=UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]
        parts = [f"{ts}Z", f"{record.levelname:<7}", f"[{record.name}]", record.getMessage()]

        ctx_fields: list[str] = []
        for field_name in ("request_id", "session_id", "mode"):
            val = getattr(record, field_name, None)
            if val is not None:
                ctx_fields.append(f"{field_name}={val}")

        for key, val in _extract_extra_fields(record).items():
            rendered = (
                json.dumps(val, default=str, ensure_ascii=False)
                if isinstance(val, dict | list)
                else str(val)
            )
            ctx_fields.append(f"{key}={rendered}")

        line = " ".join(parts)
        if ctx_fields:
            line = f"{line} | {' '.join(ctx_fields)}"

        tb = record.exc_text
        if not tb and record.exc_info and record.exc_info != (None, None, None):
            tb = redact_text(self.formatException(record.exc_info))
        if tb:
            line = f"{line}\n{tb}"
        if record.stack_info:
            line = f"{line}\n{record.stack_info}"
        return line


def _resolve_log_level(raw_level: str | None) -> int:
    name = (raw_level or os.environ.get("LOG_LEVEL") or "INFO").strip().upper()
    resolved = getattr(logging, name, None)
    return resolved if isinstance(resolved, int) else logging.INFO


def _resolve_log_format(raw_format: str | None) -> str:
    fmt = (raw_format or os.environ.get("LOG_FORMAT") or "console").strip().lower()
    return "json" if fmt == "json" else "console"


def configure_logging(
    *,
    level: str | None = None,
    log_format: str | None = None,
    force: bool = False,
) -> logging.Handler | None:
    """Configure idempotent, secret-safe structured logging."""
    global _CONFIGURED
    if _CONFIGURED and not force:
        return None

    target_level = _resolve_log_level(level)
    fmt_name = _resolve_log_format(log_format)

    root = logging.getLogger()
    for existing in list(root.handlers):
        if getattr(existing, "_structured_log_handler", False):
            root.removeHandler(existing)

    handler = logging.StreamHandler(sys.stderr)
    handler._structured_log_handler = True  # type: ignore[attr-defined]
    handler.setLevel(target_level)
    handler.addFilter(SecretRedactionFilter())
    handler.setFormatter(JsonFormatter() if fmt_name == "json" else ConsoleFormatter())
    root.addHandler(handler)
    root.setLevel(target_level)

    for noisy_name in _CLAMPED_THIRD_PARTY_LOGGERS:
        logging.getLogger(noisy_name).setLevel(logging.WARNING)

    _CONFIGURED = True
    return handler
