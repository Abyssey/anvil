"""Unit and HTTP tests for secret-safe structured logging, formatters, and redaction."""

from __future__ import annotations

import io
import json
import logging

from fastapi import FastAPI, Query
from fastapi.testclient import TestClient

from backend.app.shared.errors import DomainError, LLMUpstreamError
from backend.app.shared.http import RequestLoggingMiddleware, register_exception_handlers
from backend.app.shared.logging import (
    ConsoleFormatter,
    JsonFormatter,
    SecretRedactionFilter,
    bind_log_context,
    configure_logging,
    get_request_id,
    get_run_mode,
    get_session_id,
    redact_text,
)


def _attach_capture_handler(
    logger: logging.Logger,
    *,
    fmt: str = "json",
    level: int = logging.DEBUG,
) -> tuple[logging.Handler, io.StringIO]:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setLevel(level)
    handler.addFilter(SecretRedactionFilter())
    handler.setFormatter(JsonFormatter() if fmt == "json" else ConsoleFormatter())
    logger.addHandler(handler)
    return handler, stream


def test_redact_text_scrubs_active_secrets_headers_and_token_prefixes() -> None:
    custom_key = "custom-ollama-token-987654"
    with bind_log_context(
        request_id="req-123",
        session_id="sess-456",
        mode="plan",
        secrets=(custom_key,),
    ):
        assert get_request_id() == "req-123"
        assert get_session_id() == "sess-456"
        assert get_run_mode() == "plan"

        raw = (
            f"active={custom_key} "
            "Authorization: Bearer bearer-secret-token-xyz "
            "X-LLM-Api-Key: header-secret-abc123 "
            "api_key=inline_secret_999 "
            "openai=sk-live99887766 "
            "google=AIzaSyAbCdEfGhIjKlMn "
            "gh=ghp_1234567890abcdef "
            "gho=gho_abcdef1234567890 "
            "slack=xoxb-112233445566"
        )
        scrubbed = redact_text(raw)
        for leaked in (
            custom_key,
            "bearer-secret-token-xyz",
            "header-secret-abc123",
            "inline_secret_999",
            "sk-live99887766",
            "AIzaSyAbCdEfGhIjKlMn",
            "ghp_1234567890abcdef",
            "gho_abcdef1234567890",
            "xoxb-112233445566",
        ):
            assert leaked not in scrubbed
        assert "Authorization: Bearer [REDACTED]" in scrubbed
        assert "X-LLM-Api-Key: [REDACTED]" in scrubbed
        assert "api_key=[REDACTED]" in scrubbed

    assert get_request_id() is None
    assert get_session_id() is None
    assert get_run_mode() is None


def test_secret_redaction_filter_scrubs_messages_tracebacks_and_extra_dicts() -> None:
    sentinel = "my-custom-provider-key-424242"
    test_logger = logging.getLogger("test.structured.redaction.json")
    test_logger.setLevel(logging.DEBUG)
    test_logger.propagate = False
    handler, stream = _attach_capture_handler(test_logger, fmt="json")

    try:
        with bind_log_context(
            request_id="req-abc",
            session_id="sess-xyz",
            mode="auto",
            secrets=(sentinel,),
        ):
            try:
                raise RuntimeError(
                    f"Upstream failed with X-LLM-Api-Key: {sentinel} "
                    f"and Authorization: Bearer tok-secret-777"
                )
            except RuntimeError:
                test_logger.exception(
                    "call failed for %s",
                    f"api_key={sentinel}",
                    extra={
                        "event": "test.failure",
                        "api_key": sentinel,
                        "nested": {
                            "headers": {"Authorization": f"Bearer {sentinel}"},
                            "items": [f"token={sentinel}", "sk-nested998877"],
                        },
                    },
                )
    finally:
        test_logger.removeHandler(handler)

    output = stream.getvalue()
    assert sentinel not in output
    assert "tok-secret-777" not in output
    assert "sk-nested998877" not in output

    parsed = json.loads(output.strip())
    assert parsed["level"] == "ERROR"
    assert parsed["logger"] == "test.structured.redaction.json"
    assert parsed["request_id"] == "req-abc"
    assert parsed["session_id"] == "sess-xyz"
    assert parsed["mode"] == "auto"
    assert parsed["event"] == "test.failure"
    assert parsed["api_key"] == "[REDACTED]"
    assert parsed["nested"]["headers"]["Authorization"] == "[REDACTED]"
    assert "[REDACTED]" in parsed["msg"]
    assert "traceback" in parsed
    assert "[REDACTED]" in parsed["traceback"]


def test_console_formatter_includes_correlation_fields_and_redacts_secrets() -> None:
    sentinel = "console-secret-token-7788"
    test_logger = logging.getLogger("test.structured.redaction.console")
    test_logger.setLevel(logging.DEBUG)
    test_logger.propagate = False
    handler, stream = _attach_capture_handler(test_logger, fmt="console")

    try:
        with bind_log_context(
            request_id="req-con",
            session_id="sess-con",
            mode="ask",
            secrets=(sentinel,),
        ):
            test_logger.info(
                "sse.start key=%s",
                sentinel,
                extra={"event": "sse.start", "model": "qwen3.8-flash"},
            )
    finally:
        test_logger.removeHandler(handler)

    line = stream.getvalue().strip()
    assert sentinel not in line
    assert "INFO" in line
    assert "[test.structured.redaction.console]" in line
    assert "request_id=req-con" in line
    assert "session_id=sess-con" in line
    assert "mode=ask" in line
    assert "model=qwen3.8-flash" in line
    assert "[REDACTED]" in line


def test_configure_logging_is_idempotent_and_clamps_third_party_loggers() -> None:
    root = logging.getLogger()
    h1 = configure_logging(level="INFO", log_format="json", force=True)
    assert h1 is not None
    handlers_after_first = [
        h for h in root.handlers if getattr(h, "_structured_log_handler", False)
    ]
    assert len(handlers_after_first) == 1

    # Second call without force is a no-op and does not duplicate handlers
    h2 = configure_logging(level="DEBUG", log_format="console")
    assert h2 is None
    handlers_after_second = [
        h for h in root.handlers if getattr(h, "_structured_log_handler", False)
    ]
    assert len(handlers_after_second) == 1

    for noisy in ("httpx", "httpcore", "openai", "aiosqlite", "watchfiles"):
        assert logging.getLogger(noisy).level == logging.WARNING


def test_http_middleware_and_exception_handlers_log_safely_without_leaking_secrets() -> None:
    sentinel = "sk-http-sentinel-secret-9999"
    app = FastAPI()
    app.add_middleware(RequestLoggingMiddleware)
    register_exception_handlers(app)

    @app.get("/ok")
    async def ok_route() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/assets/app.js")
    async def asset_route() -> dict[str, str]:
        return {"asset": "js"}

    @app.get("/domain-4xx")
    async def domain_4xx() -> None:
        raise DomainError("bad input", status_code=400, code="BAD_INPUT")

    @app.get("/domain-5xx")
    async def domain_5xx() -> None:
        raise LLMUpstreamError("upstream boom")

    @app.get("/validate")
    async def validate_route(count: int = Query(...)) -> dict[str, int]:
        return {"count": count}

    @app.get("/crash")
    async def crash_route() -> None:
        raise RuntimeError(f"unexpected crash with key {sentinel}")

    http_logger = logging.getLogger("backend.app.shared.http")
    http_logger.setLevel(logging.DEBUG)
    handler, stream = _attach_capture_handler(http_logger, fmt="json", level=logging.DEBUG)

    try:
        client = TestClient(app, raise_server_exceptions=False)

        # 1. Propagates custom X-Request-ID and logs http.request at INFO
        r_ok = client.get(
            "/ok",
            headers={"X-Request-ID": "trace-42", "X-LLM-Api-Key": sentinel},
        )
        assert r_ok.status_code == 200
        assert r_ok.headers["x-request-id"] == "trace-42"

        # 2. Static /assets/* logged at DEBUG
        r_asset = client.get("/assets/app.js")
        assert r_asset.status_code == 200
        assert r_asset.headers.get("x-request-id")

        # 3. DomainError 4xx (INFO) & 5xx (WARNING)
        r_400 = client.get("/domain-4xx")
        assert r_400.status_code == 400
        assert r_400.json()["error"]["code"] == "BAD_INPUT"

        r_502 = client.get("/domain-5xx")
        assert r_502.status_code == 502
        assert r_502.json()["error"]["code"] == "LLM_UPSTREAM_ERROR"

        # 4. RequestValidationError (422) logs loc/type only, NEVER raw input value!
        r_422 = client.get(
            "/validate",
            params={"count": sentinel},
            headers={"X-LLM-Api-Key": sentinel},
        )
        assert r_422.status_code == 422
        assert r_422.json()["error"]["code"] == "VALIDATION_ERROR"
        assert sentinel not in r_422.text

        # 5. Unhandled 500 returns structured ErrorResponse with request_id and scrubs secret
        r_500 = client.get(
            "/crash",
            headers={"X-Request-ID": "trace-500", "X-LLM-Api-Key": sentinel},
        )
        assert r_500.status_code == 500
        body_500 = r_500.json()
        assert body_500["error"]["code"] == "INTERNAL_SERVER_ERROR"
        assert body_500["error"]["details"]["request_id"] == "trace-500"
        assert sentinel not in r_500.text
    finally:
        http_logger.removeHandler(handler)

    raw_logs = stream.getvalue()
    assert sentinel not in raw_logs

    records = [json.loads(line) for line in raw_logs.splitlines() if line.strip()]
    ok_req = next(r for r in records if r.get("event") == "http.request" and r.get("path") == "/ok")
    assert ok_req["level"] == "INFO"
    assert ok_req["request_id"] == "trace-42"
    assert ok_req["status_code"] == 200
    assert isinstance(ok_req["duration_ms"], int | float)

    asset_req = next(
        r for r in records if r.get("event") == "http.request" and r.get("path") == "/assets/app.js"
    )
    assert asset_req["level"] == "DEBUG"

    dom_400 = next(
        r for r in records if r.get("event") == "http.domain_error" and r.get("status_code") == 400
    )
    assert dom_400["level"] == "INFO"

    dom_502 = next(
        r for r in records if r.get("event") == "http.domain_error" and r.get("status_code") == 502
    )
    assert dom_502["level"] == "WARNING"

    val_err = next(r for r in records if r.get("event") == "http.validation_error")
    assert val_err["level"] == "WARNING"
    assert val_err["errors"][0]["loc"] == ["query", "count"]
    assert "type" in val_err["errors"][0]
    assert "input" not in val_err["errors"][0]

    unhandled = next(r for r in records if r.get("event") == "http.unhandled_error")
    assert unhandled["level"] == "ERROR"
    assert unhandled["request_id"] == "trace-500"
    assert "traceback" in unhandled
    assert "[REDACTED]" in unhandled["traceback"]
