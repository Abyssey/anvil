"""Domain error hierarchy and centralized FastAPI HTTP serialization."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel


class ErrorDetail(BaseModel):
    code: str
    message: str
    details: dict[str, object] | None = None


class ErrorResponse(BaseModel):
    error: ErrorDetail


class DomainError(Exception):
    """Base domain error mapped cleanly to structured JSON HTTP errors."""

    status_code: int = 400
    code: str = "DOMAIN_ERROR"

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        code: str | None = None,
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        if status_code is not None:
            self.status_code = status_code
        if code is not None:
            self.code = code
        self.message = message
        self.details = details

    def to_payload(self) -> dict[str, object]:
        return ErrorResponse(
            error=ErrorDetail(code=self.code, message=self.message, details=self.details)
        ).model_dump()


class LLMSettingsRequiredError(DomainError):
    status_code = 428
    code = "LLM_SETTINGS_REQUIRED"

    def __init__(
        self,
        message: str = "Configure Endpoint URL, Model ID, and API Key in Settings.",
    ) -> None:
        super().__init__(message)


class LLMUpstreamError(DomainError):
    status_code = 502
    code = "LLM_UPSTREAM_ERROR"


class SessionNotFoundError(DomainError):
    status_code = 404
    code = "SESSION_NOT_FOUND"

    def __init__(self, session_id: str) -> None:
        super().__init__(f"Session not found: {session_id}", details={"session_id": session_id})


class ProjectNotFoundError(DomainError):
    status_code = 404
    code = "PROJECT_NOT_FOUND"

    def __init__(self, project_id: str) -> None:
        super().__init__(f"Project not found: {project_id}", details={"project_id": project_id})


class ArtifactNotFoundError(DomainError):
    status_code = 404
    code = "ARTIFACT_NOT_FOUND"

    def __init__(self, kind: str) -> None:
        super().__init__(f"Artifact not found: {kind}", details={"kind": kind})


class InvalidWorkspaceError(DomainError):
    status_code = 400
    code = "INVALID_WORKSPACE"

    def __init__(self, path: str, reason: str = "Workspace directory does not exist") -> None:
        super().__init__(f"{reason}: {path}", details={"workspace_path": path})


class PathEscapeError(DomainError):
    status_code = 403
    code = "PATH_ESCAPE"

    def __init__(self, path: str) -> None:
        super().__init__(f"Path escapes workspace boundary: {path}", details={"path": path})


class CommandBlockedError(DomainError):
    status_code = 403
    code = "COMMAND_BLOCKED"

    def __init__(self, reason: str) -> None:
        super().__init__(reason)


class InvalidInterruptResumeError(DomainError):
    status_code = 409
    code = "INVALID_INTERRUPT_RESUME"

    def __init__(self, reason: str = "No pending approval interrupt for this session") -> None:
        super().__init__(reason)


def register_exception_handlers(app: FastAPI) -> None:
    """Register centralized handlers converting DomainError into ErrorResponse."""

    @app.exception_handler(DomainError)
    async def _handle_domain_error(_request: Request, exc: DomainError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=exc.to_payload())
