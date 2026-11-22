"""SessionService coordinating session lifecycle, streaming runs, and HITL resume."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

from backend.app.domains.agent.harness import DeepAgentHarness, HarnessEvent
from backend.app.domains.agent.workspace import WorkspaceInspector
from backend.app.domains.artifacts.store import ArtifactStore
from backend.app.domains.artifacts.walkthrough_diff import ParsedFileDiff, ParsedHunk
from backend.app.domains.sessions.entities import (
    ChatMessage,
    PendingInterrupt,
    Project,
    RunMode,
    Session,
    SessionStatus,
    StreamEvent,
    ToolCallRecord,
)
from backend.app.domains.sessions.repository import SessionRepository
from backend.app.shared.config import AppConfig
from backend.app.shared.errors import InvalidInterruptResumeError, InvalidWorkspaceError
from backend.app.shared.llm_credentials import LLMCredentials


class SessionService:
    """Orchestrates session persistence, workspace validation, and agent SSE streaming."""

    def __init__(
        self,
        *,
        config: AppConfig,
        repo: SessionRepository,
        artifact_store: ArtifactStore,
        workspace_inspector: WorkspaceInspector,
        harness: DeepAgentHarness,
    ) -> None:
        self._config = config
        self._repo = repo
        self._artifact_store = artifact_store
        self._workspace_inspector = workspace_inspector
        self._harness = harness

    async def list_projects(self) -> list[Project]:
        projects = await self._repo.list_projects()
        if not projects:
            default_proj = await self.create_project(str(self._config.default_workspace_path))
            return [default_proj]
        enriched: list[Project] = []
        for proj in projects:
            enriched.append(await self._enrich_project(proj))
        return enriched

    async def create_project(self, workspace_path: str) -> Project:
        info = await self._workspace_inspector.validate_workspace(workspace_path)
        existing = await self._repo.get_project_by_path(info.path)
        if existing is not None:
            return existing.model_copy(
                update={"is_git_repo": info.is_git_repo, "branch": info.branch}
            )
        now = datetime.now(UTC)
        project = Project(
            id=uuid.uuid4().hex[:12],
            name=info.name,
            path=info.path,
            is_git_repo=info.is_git_repo,
            branch=info.branch,
            created_at=now,
            updated_at=now,
        )
        await self._repo.create_project(project)
        return project

    async def delete_project(self, project_id: str) -> None:
        deleted_sessions = await self._repo.delete_project(project_id)
        for sid in deleted_sessions:
            self._artifact_store.delete_session_artifacts(sid)

    async def _enrich_project(self, proj: Project) -> Project:
        try:
            info = await self._workspace_inspector.validate_workspace(proj.path)
            return proj.model_copy(
                update={"name": info.name, "is_git_repo": info.is_git_repo, "branch": info.branch}
            )
        except InvalidWorkspaceError:
            return proj

    async def list_sessions(self) -> list[Session]:
        return await self._repo.list_sessions()

    async def create_session(
        self,
        *,
        project_id: str | None = None,
        workspace_path: str | None = None,
        mode: RunMode = RunMode.ASK,
        title: str = "New Session",
    ) -> Session:
        if project_id is not None:
            project = await self._repo.get_project(project_id)
            resolved_path = project.path
            resolved_project_id = project.id
        else:
            target = workspace_path or str(self._config.default_workspace_path)
            project = await self.create_project(target)
            resolved_path = project.path
            resolved_project_id = project.id
        now = datetime.now(UTC)
        session = Session(
            id=uuid.uuid4().hex[:12],
            project_id=resolved_project_id,
            title=title.strip() or "New Session",
            workspace_path=resolved_path,
            mode=mode,
            status=SessionStatus.IDLE,
            created_at=now,
            updated_at=now,
        )
        return await self._repo.create_session(session)

    async def get_session_detail(self, session_id: str) -> tuple[Session, list[ChatMessage]]:
        session = await self._repo.get_session(session_id)
        messages = await self._repo.list_messages(session_id)
        return session, messages

    async def update_session(
        self,
        session_id: str,
        *,
        title: str | None = None,
        workspace_path: str | None = None,
        mode: RunMode | None = None,
    ) -> Session:
        session = await self._repo.get_session(session_id)
        resolved_workspace = session.workspace_path
        resolved_project_id = session.project_id
        if workspace_path is not None:
            project = await self.create_project(workspace_path)
            resolved_workspace = project.path
            resolved_project_id = project.id
        updated = session.model_copy(
            update={
                "project_id": resolved_project_id,
                "title": title.strip() if title and title.strip() else session.title,
                "workspace_path": resolved_workspace,
                "mode": mode or session.mode,
            }
        )
        return await self._repo.update_session(updated)

    async def delete_session(self, session_id: str) -> None:
        await self._repo.delete_session(session_id)
        self._artifact_store.delete_session_artifacts(session_id)

    async def stream_message(
        self,
        *,
        session_id: str,
        content: str,
        mode: RunMode | None,
        creds: LLMCredentials,
    ) -> AsyncIterator[StreamEvent]:
        session = await self._repo.get_session(session_id)
        active_mode = mode or session.mode
        next_title = (
            content.strip().splitlines()[0][:60]
            if session.title == "New Session" and content.strip()
            else session.title
        )
        session = await self._repo.update_session(
            session.model_copy(
                update={
                    "title": next_title,
                    "mode": active_mode,
                    "status": SessionStatus.RUNNING,
                    "pending_interrupt": None,
                }
            )
        )
        await self._repo.append_message(
            ChatMessage(
                id=uuid.uuid4().hex[:12],
                session_id=session.id,
                role="user",
                content=content,
                created_at=datetime.now(UTC),
            )
        )

        raw_stream = self._harness.stream_run(
            session_id=session.id,
            workspace_path=Path(session.workspace_path),
            mode=active_mode.value,
            user_message=content,
            creds=creds,
        )
        async for ev in self._consume_and_persist_stream(session, raw_stream):
            yield ev

    async def stream_resume(
        self,
        *,
        session_id: str,
        decision: str,
        feedback: str | None,
        creds: LLMCredentials,
    ) -> AsyncIterator[StreamEvent]:
        session = await self._repo.get_session(session_id)
        if session.pending_interrupt is None:
            raise InvalidInterruptResumeError()

        session = await self._repo.update_session(
            session.model_copy(
                update={
                    "status": SessionStatus.RUNNING,
                    "pending_interrupt": None,
                }
            )
        )
        raw_stream = self._harness.stream_resume(
            session_id=session.id,
            workspace_path=Path(session.workspace_path),
            mode=session.mode.value,
            decision=decision,
            feedback=feedback,
            creds=creds,
        )
        async for ev in self._consume_and_persist_stream(session, raw_stream):
            yield ev

    async def _consume_and_persist_stream(
        self,
        session: Session,
        raw_stream: AsyncIterator[HarnessEvent],
    ) -> AsyncIterator[StreamEvent]:
        thinking_parts: list[str] = []
        token_parts: list[str] = []
        tool_map: dict[str, ToolCallRecord] = {}
        tool_order: list[str] = []
        pending_interrupt: PendingInterrupt | None = None
        had_error = False
        was_cancelled = False

        try:
            async for h_event in raw_stream:
                if h_event.event == "thinking":
                    text = str(h_event.data.get("text") or "")
                    thinking_parts.append(text)
                elif h_event.event == "token":
                    text = str(h_event.data.get("text") or "")
                    token_parts.append(text)
                elif h_event.event == "tool_start":
                    tc_id = str(h_event.data.get("id") or uuid.uuid4().hex[:8])
                    record = ToolCallRecord(
                        id=tc_id,
                        name=str(h_event.data.get("name") or ""),
                        args=dict(h_event.data.get("args") or {})
                        if isinstance(h_event.data.get("args"), dict)
                        else {},
                        status="running",
                        subagent=h_event.data.get("subagent")
                        if isinstance(h_event.data.get("subagent"), str)
                        else None,
                    )
                    tool_map[tc_id] = record
                    tool_order.append(tc_id)
                elif h_event.event == "tool_end":
                    tc_id = str(h_event.data.get("id") or "")
                    if tc_id in tool_map:
                        tool_map[tc_id] = tool_map[tc_id].model_copy(
                            update={
                                "output": str(h_event.data.get("output") or ""),
                                "status": str(h_event.data.get("status") or "success"),
                            }
                        )
                elif h_event.event == "interrupt":
                    pending_interrupt = PendingInterrupt.model_validate(h_event.data)
                elif h_event.event == "diff_changed":
                    await self._refresh_walkthrough_plan(session.id, session.workspace_path)
                elif h_event.event == "error":
                    had_error = True

                yield StreamEvent(event=h_event.event, data=dict(h_event.data))
        except (asyncio.CancelledError, GeneratorExit):
            was_cancelled = True
            raise
        except Exception:
            had_error = True
            raise
        finally:
            await asyncio.shield(
                self._finalize_stream(
                    session=session,
                    thinking_parts=thinking_parts,
                    token_parts=token_parts,
                    tool_map=tool_map,
                    tool_order=tool_order,
                    pending_interrupt=pending_interrupt,
                    had_error=had_error,
                    was_cancelled=was_cancelled,
                )
            )

    async def _finalize_stream(
        self,
        *,
        session: Session,
        thinking_parts: list[str],
        token_parts: list[str],
        tool_map: dict[str, ToolCallRecord],
        tool_order: list[str],
        pending_interrupt: PendingInterrupt | None,
        had_error: bool,
        was_cancelled: bool,
    ) -> None:
        content_text = "".join(token_parts).strip()
        thinking_text = "".join(thinking_parts).strip()
        terminal_tool_status = (
            "cancelled" if was_cancelled else ("error" if had_error else "success")
        )
        tools = [
            tool_map[tid].model_copy(update={"status": terminal_tool_status})
            if tool_map[tid].status == "running"
            else tool_map[tid]
            for tid in tool_order
        ]
        if content_text or thinking_text or tools:
            await self._repo.append_message(
                ChatMessage(
                    id=uuid.uuid4().hex[:12],
                    session_id=session.id,
                    role="assistant",
                    content=content_text,
                    thinking=thinking_text,
                    tool_calls=tools,
                    created_at=datetime.now(UTC),
                )
            )

        next_status = (
            SessionStatus.AWAITING_APPROVAL
            if pending_interrupt is not None and not was_cancelled
            else (SessionStatus.ERROR if had_error and not was_cancelled else SessionStatus.IDLE)
        )
        await self._repo.update_session(
            session.model_copy(
                update={
                    "status": next_status,
                    "pending_interrupt": pending_interrupt if not was_cancelled else None,
                }
            )
        )

    async def _refresh_walkthrough_plan(self, session_id: str, workspace_path: str) -> None:
        diff_resp = await self._workspace_inspector.get_diff(workspace_path)
        parsed = [
            ParsedFileDiff(
                path=f.path,
                status=f.status,
                hunks=[
                    ParsedHunk(
                        path=h.path,
                        name=h.name,
                        old_start=h.old_start,
                        old_count=h.old_count,
                        new_start=h.new_start,
                        new_count=h.new_count,
                        header=h.header,
                        lines=tuple(h.lines),
                    )
                    for h in f.hunks
                ],
            )
            for f in diff_resp.files
        ]
        if parsed:
            self._artifact_store.build_and_save_walkthrough_plan(
                session_id,
                parsed,
                git_head=diff_resp.git_head,
            )
        else:
            self._artifact_store.clear_walkthrough_plan(session_id)
