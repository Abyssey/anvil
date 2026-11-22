"""SessionService coordinating session lifecycle, streaming runs, and HITL resume."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path

from backend.app.domains.agent.harness import DeepAgentHarness, HarnessEvent
from backend.app.domains.agent.workspace import WorkspaceInspector
from backend.app.domains.artifacts.store import ArtifactStore
from backend.app.domains.sessions.entities import (
    ChatMessage,
    PendingInterrupt,
    Project,
    ProjectMemory,
    RunMode,
    Session,
    SessionStatus,
    StreamEvent,
    ToolCallRecord,
)
from backend.app.domains.sessions.memory_store import (
    MAX_PROJECT_MEMORY_BYTES,
    ProjectMemoryStore,
)
from backend.app.domains.sessions.repository import SessionRepository
from backend.app.shared.config import AppConfig
from backend.app.shared.errors import (
    DomainError,
    InvalidInterruptResumeError,
    InvalidWorkspaceError,
)
from backend.app.shared.llm_credentials import LLMCredentials
from backend.app.shared.logging import bind_log_context

logger = logging.getLogger(__name__)


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
        memories_root: Path | None = None,
        on_delete_session_thread: Callable[[str], Awaitable[None]] | None = None,
    ) -> None:
        self._config = config
        self._repo = repo
        self._artifact_store = artifact_store
        self._workspace_inspector = workspace_inspector
        self._harness = harness
        self._memory_store = ProjectMemoryStore(memories_root or config.memories_root)
        self._on_delete_session_thread = on_delete_session_thread

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

    async def _delete_session_thread(self, session_id: str) -> None:
        if self._on_delete_session_thread is not None:
            await self._on_delete_session_thread(session_id)
        elif hasattr(self._harness, "delete_thread"):
            await self._harness.delete_thread(session_id)

    async def delete_project(self, project_id: str) -> None:
        deleted_sessions = await self._repo.delete_project(project_id)
        for sid in deleted_sessions:
            self._artifact_store.delete_session_artifacts(sid)
            await self._delete_session_thread(sid)
        self._memory_store.delete_project_dir(project_id)

    async def get_project_memory(self, project_id: str) -> ProjectMemory:
        project = await self._repo.get_project(project_id)
        content, updated_at = self._memory_store.read(project.id)
        has_repo_agents_md = (Path(project.path) / "AGENTS.md").is_file()
        return ProjectMemory(
            project_id=project.id,
            content=content,
            updated_at=updated_at,
            has_repo_agents_md=has_repo_agents_md,
            max_bytes=MAX_PROJECT_MEMORY_BYTES,
        )

    async def update_project_memory(self, project_id: str, content: str) -> ProjectMemory:
        project = await self._repo.get_project(project_id)
        saved, updated_at = self._memory_store.write(project.id, content)
        has_repo_agents_md = (Path(project.path) / "AGENTS.md").is_file()
        return ProjectMemory(
            project_id=project.id,
            content=saved,
            updated_at=updated_at,
            has_repo_agents_md=has_repo_agents_md,
            max_bytes=MAX_PROJECT_MEMORY_BYTES,
        )

    async def clear_project_memory(self, project_id: str) -> None:
        project = await self._repo.get_project(project_id)
        self._memory_store.clear(project.id)

    async def _enrich_project(self, proj: Project) -> Project:
        try:
            info = await self._workspace_inspector.validate_workspace(proj.path)
            return proj.model_copy(
                update={"name": info.name, "is_git_repo": info.is_git_repo, "branch": info.branch}
            )
        except InvalidWorkspaceError as exc:
            logger.warning(
                "project.workspace_invalid",
                extra={
                    "event": "project.workspace_invalid",
                    "project_id": proj.id,
                    "workspace_path": proj.path,
                    "detail": exc.message,
                },
            )
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

    async def get_session_detail_page(
        self,
        session_id: str,
        *,
        limit: int = 20,
    ) -> tuple[Session, list[ChatMessage], bool]:
        session = await self._repo.get_session(session_id)
        messages, has_more = await self._repo.list_messages_page(session_id, limit=limit)
        return session, messages, has_more

    async def list_messages_page(
        self,
        session_id: str,
        *,
        before_id: str | None = None,
        limit: int = 20,
    ) -> tuple[list[ChatMessage], bool]:
        return await self._repo.list_messages_page(
            session_id,
            before_id=before_id,
            limit=limit,
        )

    async def update_session(
        self,
        session_id: str,
        *,
        title: str | None = None,
        workspace_path: str | None = None,
        mode: RunMode | None = None,
    ) -> Session:
        session = await self._repo.get_session(session_id)
        if workspace_path is not None:
            current_resolved = str(Path(session.workspace_path).expanduser().resolve())
            candidate_resolved = str(Path(workspace_path).expanduser().resolve())
            if candidate_resolved != current_resolved:
                raise DomainError(
                    "Cannot move an existing session to a different project workspace.",
                    status_code=400,
                    code="SESSION_PROJECT_IMMUTABLE",
                    details={
                        "session_id": session_id,
                        "current_workspace_path": current_resolved,
                        "requested_workspace_path": candidate_resolved,
                    },
                )
        updated = session.model_copy(
            update={
                "title": title.strip() if title and title.strip() else session.title,
                "mode": mode or session.mode,
            }
        )
        return await self._repo.update_session(updated)

    async def delete_session(self, session_id: str) -> None:
        await self._repo.delete_session(session_id)
        self._artifact_store.delete_session_artifacts(session_id)
        await self._delete_session_thread(session_id)

    async def stream_message(
        self,
        *,
        session_id: str,
        content: str,
        mode: RunMode | None,
        creds: LLMCredentials,
        pinned_skills: list[str] | None = None,
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

        with bind_log_context(
            session_id=session.id,
            mode=active_mode.value,
            secrets=(creds.api_key,),
        ):
            raw_stream = self._harness.stream_run(
                session_id=session.id,
                workspace_path=Path(session.workspace_path),
                mode=active_mode.value,
                user_message=content,
                creds=creds,
                project_id=session.project_id or session.id,
                pinned_skills=pinned_skills,
            )
            async for ev in self._consume_and_persist_stream(
                session,
                raw_stream,
                model=creds.model,
            ):
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
        with bind_log_context(
            session_id=session.id,
            mode=session.mode.value,
            secrets=(creds.api_key,),
        ):
            raw_stream = self._harness.stream_resume(
                session_id=session.id,
                workspace_path=Path(session.workspace_path),
                mode=session.mode.value,
                decision=decision,
                feedback=feedback,
                creds=creds,
                project_id=session.project_id or session.id,
            )
            async for ev in self._consume_and_persist_stream(
                session,
                raw_stream,
                model=creds.model,
            ):
                yield ev

    async def _consume_and_persist_stream(
        self,
        session: Session,
        raw_stream: AsyncIterator[HarnessEvent],
        *,
        model: str = "",
    ) -> AsyncIterator[StreamEvent]:
        thinking_parts: list[str] = []
        token_parts: list[str] = []
        tool_map: dict[str, ToolCallRecord] = {}
        tool_order: list[str] = []
        pending_interrupt: PendingInterrupt | None = None
        had_error = False
        was_cancelled = False
        start = time.perf_counter()

        logger.info(
            "sse.start",
            extra={"event": "sse.start", "model": model},
        )

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
                    logger.info(
                        "sse.interrupt",
                        extra={
                            "event": "sse.interrupt",
                            "kind": pending_interrupt.kind,
                        },
                    )
                elif h_event.event == "diff_changed":
                    await self._refresh_walkthrough_plan(session.id, session.workspace_path)
                elif h_event.event == "error":
                    had_error = True
                    logger.warning(
                        "sse.error",
                        extra={
                            "event": "sse.error",
                            "error_code": str(h_event.data.get("code") or "AGENT_ERROR"),
                        },
                    )

                yield StreamEvent(event=h_event.event, data=dict(h_event.data))
        except (asyncio.CancelledError, GeneratorExit):
            was_cancelled = True
            logger.info(
                "sse.cancelled",
                extra={
                    "event": "sse.cancelled",
                    "duration_ms": round((time.perf_counter() - start) * 1000.0, 2),
                    "tool_count": len(tool_order),
                },
            )
            raise
        except Exception:
            had_error = True
            logger.error(
                "sse.error",
                exc_info=True,
                extra={
                    "event": "sse.error",
                    "duration_ms": round((time.perf_counter() - start) * 1000.0, 2),
                    "tool_count": len(tool_order),
                },
            )
            raise
        finally:
            next_status = await asyncio.shield(
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
            logger.info(
                "sse.complete",
                extra={
                    "event": "sse.complete",
                    "duration_ms": round((time.perf_counter() - start) * 1000.0, 2),
                    "status": next_status.value,
                    "tool_count": len(tool_order),
                },
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
    ) -> SessionStatus:
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
        return next_status

    async def _refresh_walkthrough_plan(self, session_id: str, workspace_path: str) -> None:
        diff_resp = await self._workspace_inspector.get_diff(workspace_path)
        if diff_resp.files:
            self._artifact_store.build_and_save_walkthrough_plan(
                session_id,
                diff_resp.files,
                git_head=diff_resp.git_head,
            )
        else:
            self._artifact_store.clear_walkthrough_plan(session_id)
