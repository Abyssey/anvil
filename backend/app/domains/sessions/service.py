"""SessionService coordinating session lifecycle, streaming runs, and HITL resume."""

from __future__ import annotations

import asyncio
import inspect
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
    QuestionAnswer,
    RevertSessionResult,
    RunMode,
    Session,
    SessionStatus,
    StreamEvent,
    ToolCallRecord,
    UserQuestionItem,
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

_QUESTION_DECISIONS = frozenset({"answer", "skip"})
_APPROVAL_DECISIONS = frozenset({"approve", "revise", "deny"})


def _format_qa_user_message(
    questions: list[UserQuestionItem],
    decision: str,
    answers: list[QuestionAnswer],
    feedback: str | None,
) -> str:
    """Format answered or skipped clarifying questions into a concise timeline user message."""
    if decision == "skip" and not answers:
        if len(questions) == 1:
            return f"Q. {questions[0].question}\nA. (건너뜀 — 추천안으로 진행)"
        lines = ["질문 건너뜀 (추천안으로 진행)"]
        for idx, item in enumerate(questions, start=1):
            lines.append(f"{idx}. {item.question}")
        return "\n".join(lines)

    entries: list[tuple[str, str]] = []
    for idx, item in enumerate(questions):
        ans: QuestionAnswer | None = answers[idx] if idx < len(answers) else None
        if ans is None:
            for candidate in answers:
                if candidate.question.strip() == item.question.strip():
                    ans = candidate
                    break
        if ans is None or ans.skipped:
            ans_text = "(건너뜀 — 추천안으로 진행)"
        else:
            custom = (ans.custom_text or "").strip()
            selected = (ans.selected_option or "").strip()
            ans_text = custom or selected or "(건너뜀 — 추천안으로 진행)"
        entries.append((item.question, ans_text))

    if not entries and answers:
        for ans in answers:
            custom = (ans.custom_text or "").strip()
            selected = (ans.selected_option or "").strip()
            ans_text = (
                "(건너뜀 — 추천안으로 진행)"
                if ans.skipped
                else (custom or selected or "(건너뜀 — 추천안으로 진행)")
            )
            entries.append((ans.question, ans_text))

    if len(entries) == 1:
        q_text, a_text = entries[0]
        text = f"Q. {q_text}\nA. {a_text}"
    else:
        blocks = [
            f"{idx}. Q. {q_text}\n   A. {a_text}"
            for idx, (q_text, a_text) in enumerate(entries, start=1)
        ]
        text = "\n".join(blocks)

    if feedback and feedback.strip():
        text = f"{text}\n\n추가 메모: {feedback.strip()}"
    return text


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

    async def revert_from_message(
        self,
        session_id: str,
        message_id: str,
    ) -> RevertSessionResult:
        """Revert workspace files, transcript, and LangGraph state to before the target turn."""
        session = await self._repo.get_session(session_id)
        if session.status == SessionStatus.RUNNING:
            raise DomainError(
                "Cannot revert while the session is actively streaming. Stop the run first.",
                status_code=409,
                code="SESSION_RUNNING",
            )

        all_messages = await self._repo.list_messages(session_id)
        target_idx = next(
            (idx for idx, msg in enumerate(all_messages) if msg.id == message_id),
            None,
        )
        if target_idx is None:
            raise DomainError(
                f"Message '{message_id}' not found in session '{session_id}'.",
                status_code=404,
                code="MESSAGE_NOT_FOUND",
            )

        turn_start_idx = target_idx
        if all_messages[turn_start_idx].role == "assistant":
            while turn_start_idx > 0:
                prev_msg = all_messages[turn_start_idx - 1]
                if prev_msg.role == "assistant":
                    turn_start_idx -= 1
                    continue
                # Walk past synthesized Q&A resume messages back to the originating user prompt
                is_qa_resume = prev_msg.content.startswith(("Q. ", "1. Q. ", "질문 건너뜀"))
                if is_qa_resume and turn_start_idx >= 2:
                    turn_start_idx -= 1
                    continue
                turn_start_idx -= 1
                break

        removed_slice = all_messages[turn_start_idx:]
        rewound_user_prompt: str | None = None
        for msg in removed_slice:
            if msg.role == "user" and not msg.content.startswith(("Q. ", "1. Q. ", "질문 건너뜀")):
                rewound_user_prompt = msg.content
                break
        if rewound_user_prompt is None and removed_slice and removed_slice[0].role == "user":
            rewound_user_prompt = removed_slice[0].content

        checkpoint_ids_newest_first = [
            msg.checkpoint_id
            for msg in reversed(removed_slice)
            if msg.checkpoint_id is not None
        ]

        if hasattr(self._harness, "revert_checkpoints"):
            reverted_files = await self._harness.revert_checkpoints(
                session.id,
                Path(session.workspace_path),
                checkpoint_ids_newest_first,
            )
        else:
            reverted_files = []
            await self._delete_session_thread(session.id)

        await self._repo.truncate_messages_from(session.id, all_messages[turn_start_idx].id)
        updated_session = await self._repo.update_session(
            session.model_copy(
                update={
                    "status": SessionStatus.IDLE,
                    "pending_interrupt": None,
                }
            )
        )
        await self._refresh_walkthrough_plan(session.id, session.workspace_path)
        _, page_messages, has_more = await self.get_session_detail_page(session.id, limit=20)
        logger.info(
            "session.reverted",
            extra={
                "event": "session.reverted",
                "session_id": session.id,
                "from_message_id": all_messages[turn_start_idx].id,
                "reverted_file_count": len(reverted_files),
            },
        )
        return RevertSessionResult(
            session=updated_session,
            messages=page_messages,
            has_more_messages=has_more,
            reverted_files=reverted_files,
            rewound_user_prompt=rewound_user_prompt,
        )

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
        prior_history = [
            (m.role, m.content) for m in await self._repo.list_messages(session.id)
        ]
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
            extra_kwargs: dict[str, object] = {}
            if prior_history and "prior_messages" in inspect.signature(
                self._harness.stream_run
            ).parameters:
                extra_kwargs["prior_messages"] = prior_history
            raw_stream = self._harness.stream_run(
                session_id=session.id,
                workspace_path=Path(session.workspace_path),
                mode=active_mode.value,
                user_message=content,
                creds=creds,
                project_id=session.project_id or session.id,
                pinned_skills=pinned_skills,
                **extra_kwargs,  # type: ignore[arg-type]
            )
            async for ev in self._consume_and_persist_stream(
                session,
                raw_stream,
                model=creds.model,
            ):
                yield ev

    async def validate_resume(self, session_id: str, decision: str) -> Session:
        """Pre-flight check before opening the SSE stream so errors return HTTP 4xx."""
        session = await self._repo.get_session(session_id)
        pending = session.pending_interrupt
        if pending is None:
            raise InvalidInterruptResumeError()
        if pending.kind == "user_question" and decision not in _QUESTION_DECISIONS:
            raise InvalidInterruptResumeError(
                "Question interrupt requires decision 'answer' or 'skip'."
            )
        if pending.kind != "user_question" and decision not in _APPROVAL_DECISIONS:
            raise InvalidInterruptResumeError(
                f"Interrupt '{pending.kind}' requires decision 'approve', 'revise', or 'deny'."
            )
        return session

    async def stream_resume(
        self,
        *,
        session_id: str,
        decision: str,
        feedback: str | None,
        creds: LLMCredentials,
        answers: list[QuestionAnswer] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        session = await self.validate_resume(session_id, decision)
        pending = session.pending_interrupt
        assert pending is not None

        answer_list = list(answers or [])
        if pending.kind == "user_question":
            qa_summary = _format_qa_user_message(
                pending.questions,
                decision,
                answer_list,
                feedback,
            )
            await self._repo.append_message(
                ChatMessage(
                    id=uuid.uuid4().hex[:12],
                    session_id=session.id,
                    role="user",
                    content=qa_summary,
                    created_at=datetime.now(UTC),
                )
            )

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
                answers=[a.model_dump() for a in answer_list] if answer_list else None,
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
        turn_checkpoint_id: str | None = None
        turn_changed_files: list[str] = []
        turn_verification_stamp: str | None = None
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
                elif h_event.event == "turn_meta":
                    raw_cid = h_event.data.get("checkpoint_id")
                    if isinstance(raw_cid, str) and raw_cid:
                        turn_checkpoint_id = raw_cid
                    raw_changed = h_event.data.get("changed_files")
                    if isinstance(raw_changed, list):
                        turn_changed_files = [str(p) for p in raw_changed if isinstance(p, str)]
                    raw_stamp = h_event.data.get("verification_stamp")
                    if isinstance(raw_stamp, str) and raw_stamp in {
                        "passed",
                        "failed",
                        "none",
                        "not_needed",
                    }:
                        turn_verification_stamp = raw_stamp
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
                    checkpoint_id=turn_checkpoint_id,
                    changed_files=turn_changed_files,
                    verification_stamp=turn_verification_stamp,
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
        checkpoint_id: str | None = None,
        changed_files: list[str] | None = None,
        verification_stamp: str | None = None,
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
        valid_stamp = (
            verification_stamp
            if verification_stamp in {"passed", "failed", "none", "not_needed"}
            else None
        )
        if content_text or thinking_text or tools or checkpoint_id is not None:
            await self._repo.append_message(
                ChatMessage(
                    id=uuid.uuid4().hex[:12],
                    session_id=session.id,
                    role="assistant",
                    content=content_text,
                    thinking=thinking_text,
                    tool_calls=tools,
                    checkpoint_id=checkpoint_id,
                    changed_files=list(changed_files or []),
                    verification_stamp=valid_stamp,  # type: ignore[arg-type]
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
