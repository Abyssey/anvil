"""Pure Pydantic entities for Sessions, ChatMessages, RunMode, and StreamEvents."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


class RunMode(StrEnum):
    ASK = "ask"
    PLAN = "plan"
    AUTO = "auto"


class SessionStatus(StrEnum):
    IDLE = "idle"
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    ERROR = "error"


class ToolCallRecord(BaseModel):
    id: str = ""
    name: str
    args: dict[str, object] = Field(default_factory=dict)
    output: str = ""
    status: str = "running"
    subagent: str | None = None


class ChatMessage(BaseModel):
    id: str
    session_id: str
    role: Literal["user", "assistant"]
    content: str
    thinking: str = ""
    tool_calls: list[ToolCallRecord] = Field(default_factory=list)
    created_at: datetime


class PendingInterrupt(BaseModel):
    kind: Literal["plan_review", "shell_approval"]
    summary: str
    artifact_kind: str | None = None
    command: str | None = None
    workspace: str | None = None
    reason: str | None = None


class Project(BaseModel):
    id: str
    name: str
    path: str
    is_git_repo: bool = False
    branch: str = ""
    created_at: datetime
    updated_at: datetime


class ProjectMemory(BaseModel):
    project_id: str
    content: str = ""
    updated_at: datetime | None = None
    has_repo_agents_md: bool = False
    max_bytes: int = 32 * 1024


class Session(BaseModel):
    id: str
    project_id: str | None = None
    title: str
    workspace_path: str
    mode: RunMode = RunMode.ASK
    status: SessionStatus = SessionStatus.IDLE
    pending_interrupt: PendingInterrupt | None = None
    created_at: datetime
    updated_at: datetime


class StreamEvent(BaseModel):
    event: str
    data: dict[str, object] = Field(default_factory=dict)
