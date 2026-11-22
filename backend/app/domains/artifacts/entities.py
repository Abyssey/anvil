"""Pure Pydantic entities for Antigravity artifacts and diff walkthrough chunks."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


class ArtifactKind(StrEnum):
    IMPLEMENTATION_PLAN = "implementation_plan"
    TASK = "task"
    WALKTHROUGH = "walkthrough"


class ArtifactDoc(BaseModel):
    kind: ArtifactKind
    title: str
    content: str
    updated_at: datetime


class InterruptKind(StrEnum):
    PLAN_REVIEW = "plan_review"
    SHELL_APPROVAL = "shell_approval"


class InterruptPayload(BaseModel):
    kind: InterruptKind
    summary: str
    artifact_kind: ArtifactKind | None = None
    command: str | None = None
    workspace: str | None = None
    reason: str | None = None


class DiffHunk(BaseModel):
    path: str
    name: str
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    header: str
    lines: list[str] = Field(default_factory=list)


class FileDiff(BaseModel):
    path: str
    status: Literal["modified", "added", "deleted", "renamed", "untracked"]
    hunks: list[DiffHunk] = Field(default_factory=list)
    preview_content: str | None = None


class FileHunks(BaseModel):
    path: str
    hunks: list[str] = Field(default_factory=list)


class WalkthroughChunk(BaseModel):
    id: str
    title: str
    explanation: str
    file_hunks: list[FileHunks] = Field(default_factory=list)


class WalkthroughPlan(BaseModel):
    git_head: str = ""
    chunks: list[WalkthroughChunk] = Field(default_factory=list)
    other_files: list[str] = Field(default_factory=list)
