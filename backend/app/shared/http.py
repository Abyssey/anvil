"""FastAPI HTTP adapters: header credential extraction, exception handlers, and request logging."""

from __future__ import annotations

import logging
import re
import time
import uuid
from typing import Annotated

from fastapi import FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from backend.app.shared.errors import (
    DomainError,
    ErrorDetail,
    ErrorResponse,
    LLMSettingsRequiredError,
)
from backend.app.shared.llm_credentials import LLMCredentials, parse_bool_flag
from backend.app.shared.logging import bind_log_context, get_request_id

logger = logging.getLogger(__name__)

_SAFE_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


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
        enable_thinking=parse_bool_flag(x_llm_enable_thinking, default=True),
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Register centralized HTTP exception handlers with secret-safe structured logging."""

    @app.exception_handler(DomainError)
    async def _handle_domain_error(request: Request, exc: DomainError) -> JSONResponse:
        log_fn = logger.warning if exc.status_code >= 500 else logger.info
        log_fn(
            "http.domain_error",
            extra={
                "event": "http.domain_error",
                "error_code": exc.code,
                "status_code": exc.status_code,
                "path": request.url.path,
            },
        )
        return JSONResponse(status_code=exc.status_code, content=exc.to_payload())

    @app.exception_handler(RequestValidationError)
    async def _handle_validation_error(
        request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        # Log ONLY field loc and error type — never raw input values (could contain secrets).
        safe_errors = [
            {
                "loc": [str(part) for part in err.get("loc", ())],
                "type": str(err.get("type") or "validation_error"),
            }
            for err in exc.errors()
        ]
        logger.warning(
            "http.validation_error",
            extra={
                "event": "http.validation_error",
                "status_code": 422,
                "path": request.url.path,
                "errors": safe_errors,
            },
        )
        payload = ErrorResponse(
            error=ErrorDetail(
                code="VALIDATION_ERROR",
                message="Request validation failed.",
                details={"errors": safe_errors},
            )
        ).model_dump()
        return JSONResponse(status_code=422, content=payload)

    @app.exception_handler(Exception)
    async def _handle_unhandled_error(request: Request, exc: Exception) -> JSONResponse:
        state_req_id = getattr(request.state, "request_id", None)
        state_secrets = getattr(request.state, "_active_secrets", ())
        req_id = (
            get_request_id()
            or (state_req_id if isinstance(state_req_id, str) else None)
            or _extract_request_headers(request.scope)[0]
        )
        secrets = state_secrets if isinstance(state_secrets, tuple) else ()
        with bind_log_context(request_id=req_id, secrets=secrets):
            logger.exception(
                "http.unhandled_error",
                exc_info=(type(exc), exc, exc.__traceback__),
                extra={
                    "event": "http.unhandled_error",
                    "status_code": 500,
                    "path": request.url.path,
                    "error_type": type(exc).__name__,
                },
            )
        payload = ErrorResponse(
            error=ErrorDetail(
                code="INTERNAL_SERVER_ERROR",
                message="An unexpected internal server error occurred.",
                details={"request_id": req_id},
            )
        ).model_dump()
        return JSONResponse(
            status_code=500,
            content=payload,
            headers={"X-Request-ID": req_id},
        )


def _extract_request_headers(scope: Scope) -> tuple[str, tuple[str, ...]]:
    """Extract safe X-Request-ID and any active secret header values for redaction."""
    request_id: str | None = None
    secrets: list[str] = []
    for raw_name, raw_val in scope.get("headers") or ():
        name = raw_name.decode("latin-1").lower()
        if name == "x-request-id":
            candidate = raw_val.decode("latin-1").strip()
            if _SAFE_REQUEST_ID_RE.fullmatch(candidate):
                request_id = candidate
        elif name == "x-llm-api-key":
            secret = raw_val.decode("latin-1").strip()
            if secret:
                secrets.append(secret)
        elif name == "authorization":
            auth_val = raw_val.decode("latin-1").strip()
            if auth_val.lower().startswith("bearer "):
                token = auth_val[7:].strip()
                if token:
                    secrets.append(token)
    return request_id or uuid.uuid4().hex[:16], tuple(secrets)


class RequestLoggingMiddleware:
    """Streaming-safe ASGI middleware binding X-Request-ID and emitting http.request logs."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id, active_secrets = _extract_request_headers(scope)
        state = scope.setdefault("state", {})
        if isinstance(state, dict):
            state["request_id"] = request_id
            state["_active_secrets"] = active_secrets
        method = str(scope.get("method") or "GET")
        path = str(scope.get("path") or "/")
        status_code = 500
        start = time.perf_counter()

        async def _send_with_request_id(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = int(message.get("status", 500))
                headers = list(message.get("headers") or [])
                has_req_id = any(k.lower() == b"x-request-id" for k, _ in headers)
                if not has_req_id:
                    headers.append((b"x-request-id", request_id.encode("latin-1")))
                message = {**message, "headers": headers}
            await send(message)

        with bind_log_context(request_id=request_id, secrets=active_secrets):
            try:
                await self.app(scope, receive, _send_with_request_id)
            finally:
                duration_ms = round((time.perf_counter() - start) * 1000.0, 2)
                log_fn = logger.debug if path.startswith("/assets/") else logger.info
                log_fn(
                    "http.request",
                    extra={
                        "event": "http.request",
                        "method": method,
                        "path": path,
                        "status_code": status_code,
                        "duration_ms": duration_ms,
                    },
                )
