"""Thin FastAPI router for session CRUD and native EventSourceResponse SSE streaming."""

from __future__ import annotations

from collections.abc import AsyncIterable
from typing import Literal

from fastapi import APIRouter, status
from fastapi.sse import EventSourceResponse, ServerSentEvent
from pydantic import BaseModel, Field

from backend.app.domains.sessions.entities import ChatMessage, RunMode, Session
from backend.app.shared.dependencies import LLMCredentialsDep, SessionServiceDep

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


class CreateSessionRequest(BaseModel):
    workspace_path: str | None = None
    mode: RunMode = RunMode.ASK
    title: str = "New Session"


class UpdateSessionRequest(BaseModel):
    title: str | None = None
    workspace_path: str | None = None
    mode: RunMode | None = None


class SessionDetailResponse(BaseModel):
    session: Session
    messages: list[ChatMessage]


class StreamMessageRequest(BaseModel):
    content: str = Field(min_length=1)
    mode: RunMode | None = None


class ResumeInterruptRequest(BaseModel):
    decision: Literal["approve", "revise", "deny"]
    feedback: str | None = None


@router.get("")
async def list_sessions(service: SessionServiceDep) -> list[Session]:
    return await service.list_sessions()


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_session(
    body: CreateSessionRequest,
    service: SessionServiceDep,
) -> Session:
    return await service.create_session(
        workspace_path=body.workspace_path,
        mode=body.mode,
        title=body.title,
    )


@router.get("/{session_id}")
async def get_session(
    session_id: str,
    service: SessionServiceDep,
) -> SessionDetailResponse:
    session, messages = await service.get_session_detail(session_id)
    return SessionDetailResponse(session=session, messages=messages)


@router.patch("/{session_id}")
async def update_session(
    session_id: str,
    body: UpdateSessionRequest,
    service: SessionServiceDep,
) -> Session:
    return await service.update_session(
        session_id,
        title=body.title,
        workspace_path=body.workspace_path,
        mode=body.mode,
    )


@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(
    session_id: str,
    service: SessionServiceDep,
) -> None:
    await service.delete_session(session_id)


@router.post("/{session_id}/messages/stream", response_class=EventSourceResponse)
async def stream_session_message(
    session_id: str,
    body: StreamMessageRequest,
    creds: LLMCredentialsDep,
    service: SessionServiceDep,
) -> AsyncIterable[ServerSentEvent]:
    async for ev in service.stream_message(
        session_id=session_id,
        content=body.content,
        mode=body.mode,
        creds=creds,
    ):
        yield ServerSentEvent(event=ev.event, data=ev.data)


@router.post("/{session_id}/resume", response_class=EventSourceResponse)
async def resume_session_interrupt(
    session_id: str,
    body: ResumeInterruptRequest,
    creds: LLMCredentialsDep,
    service: SessionServiceDep,
) -> AsyncIterable[ServerSentEvent]:
    async for ev in service.stream_resume(
        session_id=session_id,
        decision=body.decision,
        feedback=body.feedback,
        creds=creds,
    ):
        yield ServerSentEvent(event=ev.event, data=ev.data)
