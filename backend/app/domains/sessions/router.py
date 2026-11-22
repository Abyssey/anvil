"""Thin FastAPI router for session CRUD and native EventSourceResponse SSE streaming."""

from __future__ import annotations

from collections.abc import AsyncIterable
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, status
from fastapi.sse import EventSourceResponse, ServerSentEvent
from pydantic import BaseModel, Field

from backend.app.domains.sessions.entities import (
    ChatMessage,
    Project,
    ProjectMemory,
    QuestionAnswer,
    RunMode,
    Session,
)
from backend.app.shared.dependencies import LLMCredentialsDep, SessionServiceDep

router = APIRouter(prefix="/api/sessions", tags=["sessions"])
projects_router = APIRouter(prefix="/api/projects", tags=["projects"])


class CreateProjectRequest(BaseModel):
    path: str = Field(min_length=1)


class UpdateProjectMemoryRequest(BaseModel):
    content: str


class CreateSessionRequest(BaseModel):
    project_id: str | None = None
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
    has_more_messages: bool = False


class MessagePageResponse(BaseModel):
    messages: list[ChatMessage]
    has_more: bool = False


class StreamMessageRequest(BaseModel):
    content: str = Field(min_length=1)
    mode: RunMode | None = None
    pinned_skills: list[str] = Field(default_factory=list)


class ResumeInterruptRequest(BaseModel):
    decision: Literal["approve", "revise", "deny", "answer", "skip"]
    feedback: str | None = None
    answers: list[QuestionAnswer] = Field(default_factory=list)


@projects_router.get("")
async def list_projects(service: SessionServiceDep) -> list[Project]:
    return await service.list_projects()


@projects_router.post("", status_code=status.HTTP_201_CREATED)
async def create_project(
    body: CreateProjectRequest,
    service: SessionServiceDep,
) -> Project:
    return await service.create_project(body.path)


@projects_router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(
    project_id: str,
    service: SessionServiceDep,
) -> None:
    await service.delete_project(project_id)


@projects_router.get("/{project_id}/memory")
async def get_project_memory(
    project_id: str,
    service: SessionServiceDep,
) -> ProjectMemory:
    return await service.get_project_memory(project_id)


@projects_router.put("/{project_id}/memory")
async def update_project_memory(
    project_id: str,
    body: UpdateProjectMemoryRequest,
    service: SessionServiceDep,
) -> ProjectMemory:
    return await service.update_project_memory(project_id, body.content)


@projects_router.delete("/{project_id}/memory", status_code=status.HTTP_204_NO_CONTENT)
async def clear_project_memory(
    project_id: str,
    service: SessionServiceDep,
) -> None:
    await service.clear_project_memory(project_id)


@router.get("")
async def list_sessions(service: SessionServiceDep) -> list[Session]:
    return await service.list_sessions()


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_session(
    body: CreateSessionRequest,
    service: SessionServiceDep,
) -> Session:
    return await service.create_session(
        project_id=body.project_id,
        workspace_path=body.workspace_path,
        mode=body.mode,
        title=body.title,
    )


@router.get("/{session_id}")
async def get_session(
    session_id: str,
    service: SessionServiceDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 20,
) -> SessionDetailResponse:
    session, messages, has_more = await service.get_session_detail_page(session_id, limit=limit)
    return SessionDetailResponse(
        session=session,
        messages=messages,
        has_more_messages=has_more,
    )


@router.get("/{session_id}/messages")
async def list_session_messages(
    session_id: str,
    service: SessionServiceDep,
    before_id: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 20,
) -> MessagePageResponse:
    messages, has_more = await service.list_messages_page(
        session_id,
        before_id=before_id,
        limit=limit,
    )
    return MessagePageResponse(messages=messages, has_more=has_more)


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
        pinned_skills=body.pinned_skills,
    ):
        yield ServerSentEvent(event=ev.event, data=ev.data)


async def _validate_resume_dep(
    session_id: str,
    body: ResumeInterruptRequest,
    service: SessionServiceDep,
) -> Session:
    return await service.validate_resume(session_id, body.decision)


@router.post("/{session_id}/resume", response_class=EventSourceResponse)
async def resume_session_interrupt(
    session_id: str,
    body: ResumeInterruptRequest,
    creds: LLMCredentialsDep,
    service: SessionServiceDep,
    _validated: Annotated[Session, Depends(_validate_resume_dep)],
) -> AsyncIterable[ServerSentEvent]:
    async for ev in service.stream_resume(
        session_id=session_id,
        decision=body.decision,
        feedback=body.feedback,
        creds=creds,
        answers=body.answers,
    ):
        yield ServerSentEvent(event=ev.event, data=ev.data)
