"""DeepAgentHarness: Bounded LRU graph cache + AsyncSqliteSaver + typed event stream."""

from __future__ import annotations

import inspect
import logging
from collections import OrderedDict
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import aiosqlite
from deepagents import create_deep_agent
from deepagents.middleware.filesystem import FilesystemPermission
from langchain.agents.middleware import TodoListMiddleware
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command

from backend.app.domains.agent.checkpoints import CheckpointCapture, TurnCheckpointStore
from backend.app.domains.agent.cjk_guard import CjkAllowance, CjkStreamSanitizer
from backend.app.domains.agent.middleware.git_evidence import (
    GitEvidenceMiddleware,
    GitEvidenceTracker,
)
from backend.app.domains.agent.middleware.language_guard import LanguageGuardMiddleware
from backend.app.domains.agent.middleware.memory import (
    PROJECT_MEMORY_SOURCES,
    DynamicMemoryMiddleware,
)
from backend.app.domains.agent.middleware.model_retries import ModelRetryMiddleware
from backend.app.domains.agent.middleware.run_prompt import RunPromptMiddleware
from backend.app.domains.agent.middleware.steering import (
    SteeringInbox,
    SteeringQueueMiddleware,
    SteerItem,
)
from backend.app.domains.agent.middleware.subdir_agents import SubdirAgentsReadMiddleware
from backend.app.domains.agent.middleware.tool_errors import ToolErrorMiddleware
from backend.app.domains.agent.middleware.verification import (
    VerificationLoopMiddleware,
    VerificationTracker,
)
from backend.app.domains.agent.middleware.workflow import ArtifactWorkflowMiddleware
from backend.app.domains.agent.model_adapter import ModelFactory
from backend.app.domains.agent.run_backends import (
    DynamicGuardedShellBackend,
    DynamicRunBackend,
)
from backend.app.domains.agent.run_context import RunCatalog, RunContext
from backend.app.domains.agent.shell_guard import ShellGuard
from backend.app.domains.agent.subagents import build_subagents
from backend.app.domains.agent.tools.ask_user import build_ask_user_tool
from backend.app.domains.artifacts.entities import ArtifactKind
from backend.app.domains.artifacts.store import ArtifactStore
from backend.app.shared.config import AppConfig, normalize_sqlite_journal_mode
from backend.app.shared.errors import PathEscapeError
from backend.app.shared.llm_credentials import LLMCredentials

logger = logging.getLogger(__name__)


@asynccontextmanager
async def open_checkpointer(
    sqlite_path: Path,
    *,
    journal_mode: str = "WAL",
    busy_timeout_ms: int = 15_000,
) -> AsyncIterator[AsyncSqliteSaver]:
    """Open AsyncSqliteSaver and enforce journal_mode after setup() (avoids WAL override)."""
    mode = normalize_sqlite_journal_mode(journal_mode)
    sync_mode = "FULL" if mode == "DELETE" else "NORMAL"
    timeout_sec = max(5.0, busy_timeout_ms / 1000.0)
    async with aiosqlite.connect(sqlite_path, timeout=timeout_sec) as conn:
        await conn.execute(f"PRAGMA busy_timeout={busy_timeout_ms};")
        saver = AsyncSqliteSaver(conn)
        await saver.setup()
        await conn.execute(f"PRAGMA busy_timeout={busy_timeout_ms};")
        async with conn.execute(f"PRAGMA journal_mode={mode};") as cur:
            row = await cur.fetchone()
            actual = str(row[0]).upper() if row and row[0] else ""
        if actual != mode:
            raise RuntimeError(
                f"Failed to set LangGraph checkpointer journal_mode='{mode}' "
                f"(effective='{actual}') at '{sqlite_path}'."
            )
        await conn.execute(f"PRAGMA synchronous={sync_mode};")
        await conn.commit()
        yield saver

_ASK_PERMISSIONS = [
    FilesystemPermission(operations=["write"], paths=["/artifacts/**"], mode="allow"),
    FilesystemPermission(operations=["write"], paths=["/**"], mode="deny"),
]


@dataclass(frozen=True)
class HarnessEvent:
    """Internal normalized streaming event emitted by DeepAgentHarness."""

    event: str
    data: dict[str, Any] = field(default_factory=dict)


class DeepAgentHarness:
    """Manages cached DeepAgents graphs and streams normalized execution events."""

    def __init__(
        self,
        *,
        config: AppConfig,
        artifact_store: ArtifactStore,
        model_factory: ModelFactory | None = None,
        checkpointer: BaseCheckpointSaver[Any] | None = None,
        global_skills_dir: Path | None = None,
        memories_root: Path | None = None,
        max_cached_graphs: int = 8,
    ) -> None:
        self._config = config
        self._artifact_store = artifact_store
        self._model_factory = model_factory or ModelFactory()
        self._checkpointer = checkpointer
        self._global_skills_dir = (global_skills_dir or config.skills_root).resolve()
        self._memories_root = (memories_root or config.memories_root).resolve()
        self._max_cached_graphs = max_cached_graphs
        self._graphs: OrderedDict[tuple[str, str], CompiledStateGraph[Any, Any, Any, Any]] = (
            OrderedDict()
        )
        self._subdir_agents_by_key: dict[tuple[str, str], SubdirAgentsReadMiddleware] = {}
        self._shell_guard = ShellGuard()
        self._checkpoint_store = TurnCheckpointStore(config.checkpoints_root)
        self._active_captures: dict[str, CheckpointCapture] = {}
        self._active_steers: dict[str, SteeringInbox] = {}
        self._active_allowances: dict[str, CjkAllowance] = {}

    def enqueue_steer(
        self,
        session_id: str,
        content: str,
        *,
        pinned_skills: list[str] | None = None,
    ) -> SteerItem | None:
        """Enqueue a steering message onto an actively streaming session, or return None."""
        inbox = self._active_steers.get(session_id)
        if inbox is None:
            return None
        allowance = self._active_allowances.get(session_id)
        if allowance is not None:
            allowance.absorb_user_text(content)
        return inbox.enqueue(content, pinned_skills=pinned_skills)

    async def reset_thread(self, session_id: str) -> None:
        """Reset LangGraph thread state and middleware caches while keeping file checkpoints."""
        self._active_captures.pop(session_id, None)
        for mw in self._subdir_agents_by_key.values():
            mw.clear_session(session_id)
        if self._checkpointer is not None and hasattr(self._checkpointer, "adelete_thread"):
            setup_fn = getattr(self._checkpointer, "setup", None)
            if callable(setup_fn):
                maybe_coro = setup_fn()
                if inspect.isawaitable(maybe_coro):
                    await maybe_coro
            await self._checkpointer.adelete_thread(session_id)

    async def finalize_pending_capture(self, session_id: str) -> tuple[str | None, list[str]]:
        """Finalize and detach an in-flight checkpoint capture (e.g. after client stream abort)."""
        capture = self._active_captures.pop(session_id, None)
        if capture is None:
            return None, []
        return await capture.finalize()

    async def delete_thread(self, session_id: str) -> None:
        """Delete LangGraph checkpoints, caches, and turn file snapshots for a session."""
        await self.reset_thread(session_id)
        self._checkpoint_store.delete_session_checkpoints(session_id)

    async def revert_checkpoints(
        self,
        session_id: str,
        workspace_path: Path,
        checkpoint_ids_newest_first: list[str],
    ) -> list[str]:
        """Restore workspace files for checkpoints (newest first) and reset thread state."""
        reverted_files = await self._checkpoint_store.revert_checkpoints(
            session_id,
            workspace_path,
            checkpoint_ids_newest_first,
        )
        await self.reset_thread(session_id)
        return reverted_files

    async def has_thread_checkpoint(self, session_id: str) -> bool:
        if self._checkpointer is None or not hasattr(self._checkpointer, "aget_tuple"):
            return False
        setup_fn = getattr(self._checkpointer, "setup", None)
        if callable(setup_fn):
            maybe_coro = setup_fn()
            if inspect.isawaitable(maybe_coro):
                await maybe_coro
        existing = await self._checkpointer.aget_tuple({"configurable": {"thread_id": session_id}})
        return existing is not None

    _has_thread_checkpoint = has_thread_checkpoint

    def _get_or_build_graph(
        self,
        creds: LLMCredentials,
        mode: str,
    ) -> CompiledStateGraph[Any, Any, Any, Any]:
        cache_key = (creds.cache_fingerprint(), mode)
        cached = self._graphs.get(cache_key)
        if cached is not None:
            self._graphs.move_to_end(cache_key)
            return cached

        model = self._model_factory.build(creds)
        is_ask = mode == "ask"
        backend = (
            DynamicRunBackend(
                self._config.bundled_skills_dir,
                global_skills_dir=self._global_skills_dir,
                read_only_workspace=True,
            )
            if is_ask
            else DynamicGuardedShellBackend(
                self._config.bundled_skills_dir,
                global_skills_dir=self._global_skills_dir,
            )
        )
        permissions = _ASK_PERMISSIONS if is_ask else None

        subdir_mw = SubdirAgentsReadMiddleware(skip_root=True)
        memory_mw = DynamicMemoryMiddleware(
            backend=backend,
            sources=list(PROJECT_MEMORY_SOURCES),
        )
        artifact_mw = ArtifactWorkflowMiddleware(artifact_store=self._artifact_store)

        graph = create_deep_agent(
            model=model,
            tools=[build_ask_user_tool()],
            backend=backend,
            permissions=permissions,
            skills=["/skills/"],
            subagents=build_subagents(mode=mode, model=model),
            middleware=[
                TodoListMiddleware(),
                SteeringQueueMiddleware(),
                RunPromptMiddleware(mode=mode),
                memory_mw,
                subdir_mw,
                LanguageGuardMiddleware(),
                ToolErrorMiddleware(),
                ModelRetryMiddleware(),
                GitEvidenceMiddleware(),
                VerificationLoopMiddleware(),
                artifact_mw,
            ],
            checkpointer=self._checkpointer,
        )

        self._graphs[cache_key] = graph
        self._subdir_agents_by_key[cache_key] = subdir_mw
        while len(self._graphs) > self._max_cached_graphs:
            evicted_key, _ = self._graphs.popitem(last=False)
            self._subdir_agents_by_key.pop(evicted_key, None)

        logger.info(
            "agent.graph_built",
            extra={
                "event": "agent.graph_built",
                "model": creds.model,
                "base_url": creds.base_url,
                "mode": mode,
                "cache_fingerprint": creds.cache_fingerprint(),
            },
        )
        return graph

    def _project_memory_dir(self, project_id: str | None) -> Path | None:
        if not project_id or not project_id.strip():
            return None
        projects_root = (self._memories_root / "projects").resolve()
        safe_id = Path(project_id).name
        if not safe_id or safe_id in {".", ".."}:
            raise PathEscapeError(project_id)
        target = (projects_root / safe_id).resolve()
        try:
            target.relative_to(projects_root)
        except ValueError as exc:
            raise PathEscapeError(project_id) from exc
        if target == projects_root:
            raise PathEscapeError(project_id)
        return target

    def _build_catalog(
        self,
        *,
        session_id: str,
        workspace_path: Path,
        mode: str,
        creds: LLMCredentials,
        project_id: str | None = None,
    ) -> RunCatalog:
        artifacts_dir = self._artifact_store.session_dir(session_id)
        resolved_ws = workspace_path.resolve()
        tracker = GitEvidenceTracker(workspace_path=resolved_ws)
        capture = self._checkpoint_store.new_capture(session_id, resolved_ws)
        verify_tracker = VerificationTracker()
        steering_inbox = SteeringInbox()
        cjk_allowance = CjkAllowance()
        return RunCatalog(
            session_id=session_id,
            project_id=project_id or "",
            workspace_path=resolved_ws,
            mode=mode,
            artifacts_dir=artifacts_dir,
            project_memory_dir=self._project_memory_dir(project_id),
            shell_guard=self._shell_guard,
            secret_values=(creds.api_key,),
            extra_skills_dirs=(),
            git_tracker=tracker,
            checkpoint_capture=capture,
            verification_tracker=verify_tracker,
            steering_inbox=steering_inbox,
            cjk_allowance=cjk_allowance,
        )

    async def stream_run(
        self,
        *,
        session_id: str,
        workspace_path: Path,
        mode: str,
        user_message: str,
        creds: LLMCredentials,
        project_id: str | None = None,
        pinned_skills: list[str] | None = None,
        prior_messages: list[tuple[str, str]] | None = None,
    ) -> AsyncIterator[HarnessEvent]:
        graph = self._get_or_build_graph(creds, mode)
        catalog = self._build_catalog(
            session_id=session_id,
            workspace_path=workspace_path,
            mode=mode,
            creds=creds,
            project_id=project_id,
        )
        if catalog.cjk_allowance is not None:
            for role, text in prior_messages or []:
                if role == "user" and text.strip():
                    catalog.cjk_allowance.absorb_user_text(text)
            catalog.cjk_allowance.absorb_user_text(user_message)
        seed_messages: list[tuple[str, str]] = []
        if prior_messages and not await self._has_thread_checkpoint(session_id):
            seed_messages.extend(
                ("user" if role == "user" else "assistant", text)
                for role, text in prior_messages
                if text.strip()
            )
        seed_messages.append(("user", user_message))

        run_input: dict[str, Any] = {
            "messages": seed_messages,
            "skills_metadata": None,
            "plan_approved": False,
        }
        cleaned_pins = [
            s.strip() for s in (pinned_skills or []) if isinstance(s, str) and s.strip()
        ]
        if cleaned_pins:
            run_input["pinned_skills"] = cleaned_pins
        async for ev in self._execute_stream(graph, catalog, run_input, creds):
            yield ev

    async def stream_resume(
        self,
        *,
        session_id: str,
        workspace_path: Path,
        mode: str,
        decision: str,
        feedback: str | None,
        creds: LLMCredentials,
        project_id: str | None = None,
        answers: list[dict[str, object]] | None = None,
    ) -> AsyncIterator[HarnessEvent]:
        graph = self._get_or_build_graph(creds, mode)
        catalog = self._build_catalog(
            session_id=session_id,
            workspace_path=workspace_path,
            mode=mode,
            creds=creds,
            project_id=project_id,
        )
        if catalog.cjk_allowance is not None:
            if feedback:
                catalog.cjk_allowance.absorb_user_text(feedback)
            for ans in answers or []:
                for val in ans.values():
                    if isinstance(val, str):
                        catalog.cjk_allowance.absorb_user_text(val)
        resume_payload: dict[str, Any] = {"decision": decision}
        if feedback:
            resume_payload["feedback"] = feedback
        if answers:
            resume_payload["answers"] = answers
        async for ev in self._execute_stream(
            graph,
            catalog,
            Command(resume=resume_payload),
            creds,
        ):
            yield ev

    async def _execute_stream(
        self,
        graph: CompiledStateGraph[Any, Any, Any, Any],
        catalog: RunCatalog,
        graph_input: dict[str, Any] | Command,
        creds: LLMCredentials,
    ) -> AsyncIterator[HarnessEvent]:
        config: dict[str, Any] = {"configurable": {"thread_id": catalog.session_id}}
        prev_mtimes = self._artifact_store.snapshot_mtimes(catalog.session_id)
        interrupted = False
        active_tasks: dict[str, str] = {}
        ns_subagent_map: dict[tuple[Any, ...], tuple[str, str | None]] = {}

        def _resolve_subagent(ns: tuple[Any, ...]) -> tuple[str | None, str | None]:
            if not ns:
                return None, None
            cached = ns_subagent_map.get(ns)
            if cached is not None:
                return cached
            if len(active_tasks) == 1:
                only_id, only_name = next(iter(active_tasks.items()))
                return only_name, only_id
            return _subagent_name(ns), None

        if catalog.checkpoint_capture is not None:
            self._active_captures[catalog.session_id] = catalog.checkpoint_capture
        if catalog.steering_inbox is not None:
            self._active_steers[catalog.session_id] = catalog.steering_inbox
        if catalog.cjk_allowance is not None:
            self._active_allowances[catalog.session_id] = catalog.cjk_allowance

        allowance = catalog.cjk_allowance or CjkAllowance()
        thinking_sanitizer = CjkStreamSanitizer(allowance)
        token_sanitizer = CjkStreamSanitizer(allowance)

        emitted_steer_ids: set[str] = set()
        current_input: dict[str, Any] | Command = graph_input

        async def _emit_steer_batch(
            steer_entries: list[dict[str, Any]],
        ) -> HarnessEvent:
            seg_cid: str | None = None
            seg_changed: list[str] = []
            if catalog.checkpoint_capture is not None:
                seg_cid, seg_changed = await catalog.checkpoint_capture.rotate_segment()
            seg_stamp = (
                catalog.verification_tracker.stamp
                if catalog.verification_tracker is not None
                else None
            )
            return HarnessEvent(
                event="steer_applied",
                data={
                    "messages": steer_entries,
                    "segment_checkpoint_id": seg_cid,
                    "segment_changed_files": seg_changed,
                    "segment_verification_stamp": seg_stamp,
                },
            )

        try:
            async with RunContext.activate(catalog):
                try:
                    while True:
                        async for item in graph.astream(
                            current_input,
                            config=config,  # type: ignore[arg-type]
                            stream_mode=["messages", "updates"],
                            subgraphs=True,
                        ):
                            if not isinstance(item, tuple) or len(item) != 3:
                                continue
                            namespace, stream_mode_name, payload = item

                            if stream_mode_name == "messages" and isinstance(payload, tuple):
                                chunk = payload[0]
                                meta = (
                                    payload[1]
                                    if len(payload) > 1 and isinstance(payload[1], Mapping)
                                    else {}
                                )
                                if namespace:
                                    raw_parent_id = meta.get("lc_subagent_tool_call_id")
                                    parent_tc_id = (
                                        str(raw_parent_id)
                                        if isinstance(raw_parent_id, str) and raw_parent_id
                                        else None
                                    )
                                    raw_agent = meta.get("lc_agent_name")
                                    agent_name = (
                                        str(raw_agent)
                                        if isinstance(raw_agent, str) and raw_agent
                                        else (
                                            active_tasks.get(parent_tc_id, "")
                                            if parent_tc_id
                                            else ""
                                        )
                                    )
                                    if agent_name or parent_tc_id:
                                        ns_subagent_map[namespace] = (
                                            agent_name or _subagent_name(namespace) or "subagent",
                                            parent_tc_id,
                                        )
                                subagent_label, _ = _resolve_subagent(namespace)
                                for ev in self._events_from_message_chunk(
                                    chunk,
                                    subagent_label,
                                    thinking_sanitizer=thinking_sanitizer,
                                    token_sanitizer=token_sanitizer,
                                ):
                                    yield ev
                                continue

                            if stream_mode_name == "updates" and isinstance(payload, Mapping):
                                if not namespace:
                                    newly_steered = _extract_steered_entries(
                                        payload,
                                        emitted_steer_ids,
                                    )
                                    if newly_steered:
                                        yield await _emit_steer_batch(newly_steered)

                                subagent_label, parent_tc_id = _resolve_subagent(namespace)
                                for ev in self._events_from_update(
                                    payload,
                                    subagent_label,
                                    parent_id=parent_tc_id,
                                ):
                                    if not namespace:
                                        if (
                                            ev.event == "tool_start"
                                            and ev.data.get("name") == "task"
                                        ):
                                            t_id = str(ev.data.get("id") or "")
                                            t_args = ev.data.get("args")
                                            s_type = (
                                                str(t_args.get("subagent_type") or "subagent")
                                                if isinstance(t_args, Mapping)
                                                else "subagent"
                                            )
                                            if t_id:
                                                active_tasks[t_id] = s_type
                                        elif ev.event == "tool_end":
                                            active_tasks.pop(str(ev.data.get("id") or ""), None)
                                    if ev.event == "interrupt":
                                        interrupted = True
                                    yield ev

                                prev_mtimes, art_events = self._collect_artifact_changes(
                                    catalog.session_id,
                                    prev_mtimes,
                                )
                                for art_ev in art_events:
                                    yield art_ev

                        # If a steer arrived while the final answer tokens were already streaming,
                        # drain and continue seamlessly on the same thread before closing SSE.
                        if not interrupted and catalog.steering_inbox is not None:
                            leftover = catalog.steering_inbox.drain()
                            if leftover:
                                tail_entries: list[dict[str, Any]] = []
                                tail_messages: list[tuple[str, str]] = []
                                tail_pins: list[str] = []
                                for it in leftover:
                                    emitted_steer_ids.add(it.id)
                                    tail_entries.append(
                                        {
                                            "id": it.id,
                                            "content": it.raw_content,
                                            "pinned_skills": list(it.pinned_skills),
                                        }
                                    )
                                    tail_messages.append(("user", it.formatted_content))
                                    for p in it.pinned_skills:
                                        if p not in tail_pins:
                                            tail_pins.append(p)
                                yield await _emit_steer_batch(tail_entries)
                                next_input: dict[str, Any] = {"messages": tail_messages}
                                if tail_pins:
                                    next_input["pinned_skills"] = tail_pins
                                current_input = next_input
                                continue
                        break

                except Exception as exc:
                    safe_msg = creds.redact_secrets(str(exc))
                    logger.exception(
                        "agent.stream_failed",
                        extra={
                            "event": "agent.stream_failed",
                            "error_type": type(exc).__name__,
                            "model": creds.model,
                            "base_url": creds.base_url,
                            "mode": catalog.mode,
                        },
                    )
                    if catalog.checkpoint_capture is not None:
                        self._active_captures.pop(catalog.session_id, None)
                        err_cid, err_changed = await catalog.checkpoint_capture.finalize()
                        if err_cid is not None:
                            yield HarnessEvent(
                                event="turn_meta",
                                data={
                                    "checkpoint_id": err_cid,
                                    "changed_files": err_changed,
                                    "verification_stamp": (
                                        catalog.verification_tracker.stamp
                                        if catalog.verification_tracker is not None
                                        else None
                                    ),
                                },
                            )
                    yield HarnessEvent(
                        event="error",
                        data={"code": "AGENT_EXECUTION_ERROR", "message": safe_msg},
                    )
                    return
        finally:
            self._active_steers.pop(catalog.session_id, None)
            self._active_allowances.pop(catalog.session_id, None)

        _, final_art_events = self._collect_artifact_changes(catalog.session_id, prev_mtimes)
        for art_ev in final_art_events:
            yield art_ev

        checkpoint_id: str | None = None
        checkpoint_changed: list[str] = []
        if catalog.checkpoint_capture is not None:
            self._active_captures.pop(catalog.session_id, None)
            checkpoint_id, checkpoint_changed = await catalog.checkpoint_capture.finalize()

        verification_stamp = (
            catalog.verification_tracker.stamp
            if catalog.verification_tracker is not None
            else None
        )
        if checkpoint_id is not None or verification_stamp not in (None, "not_needed"):
            yield HarnessEvent(
                event="turn_meta",
                data={
                    "checkpoint_id": checkpoint_id,
                    "changed_files": checkpoint_changed,
                    "verification_stamp": verification_stamp,
                },
            )

        git_changed = (
            catalog.git_tracker.changed_files
            if catalog.git_tracker is not None and catalog.git_tracker.has_changes
            else []
        )
        combined_changed = sorted(set(git_changed) | set(checkpoint_changed))
        has_git_changes = catalog.git_tracker is not None and catalog.git_tracker.has_changes
        if combined_changed or has_git_changes:
            yield HarnessEvent(
                event="diff_changed",
                data={"changed_files": combined_changed},
            )

        if not interrupted:
            yield HarnessEvent(event="done", data={"session_id": catalog.session_id})

    def _events_from_message_chunk(
        self,
        chunk: object,
        subagent: str | None,
        *,
        thinking_sanitizer: CjkStreamSanitizer | None = None,
        token_sanitizer: CjkStreamSanitizer | None = None,
    ) -> list[HarnessEvent]:
        if subagent is not None:
            return []
        events: list[HarnessEvent] = []
        additional = getattr(chunk, "additional_kwargs", None)
        if isinstance(additional, Mapping):
            reasoning = additional.get("reasoning_content")
            if isinstance(reasoning, str) and reasoning:
                cleaned_reason = (
                    thinking_sanitizer.sanitize(reasoning) if thinking_sanitizer else reasoning
                )
                if cleaned_reason:
                    events.append(
                        HarnessEvent(
                            event="thinking",
                            data={"text": cleaned_reason},
                        )
                    )
        content = getattr(chunk, "content", None)
        is_ai_chunk = getattr(chunk, "type", "") == "AIMessageChunk"
        if isinstance(content, str):
            if content and is_ai_chunk:
                cleaned_tok = token_sanitizer.sanitize(content) if token_sanitizer else content
                if cleaned_tok:
                    events.append(HarnessEvent(event="token", data={"text": cleaned_tok}))
        elif isinstance(content, list) and is_ai_chunk:
            for block in content:
                if not isinstance(block, Mapping):
                    continue
                btype = str(block.get("type") or "")
                if btype in {"thinking", "reasoning"}:
                    think_text = block.get("thinking") or block.get("text")
                    if isinstance(think_text, str) and think_text:
                        cleaned_think = (
                            thinking_sanitizer.sanitize(think_text)
                            if thinking_sanitizer
                            else think_text
                        )
                        if cleaned_think:
                            events.append(
                                HarnessEvent(
                                    event="thinking",
                                    data={"text": cleaned_think},
                                )
                            )
                elif btype == "text":
                    text_val = block.get("text")
                    if isinstance(text_val, str) and text_val:
                        cleaned_val = (
                            token_sanitizer.sanitize(text_val) if token_sanitizer else text_val
                        )
                        if cleaned_val:
                            events.append(HarnessEvent(event="token", data={"text": cleaned_val}))
        return events

    def _events_from_update(
        self,
        payload: Mapping[str, Any],
        subagent: str | None,
        *,
        parent_id: str | None = None,
    ) -> list[HarnessEvent]:
        events: list[HarnessEvent] = []
        if "__interrupt__" in payload:
            raw_interrupts = payload["__interrupt__"]
            if isinstance(raw_interrupts, tuple | list) and raw_interrupts:
                first = raw_interrupts[0]
                value = getattr(first, "value", first)
                if isinstance(value, Mapping):
                    events.append(HarnessEvent(event="interrupt", data=dict(value)))
            return events

        extra_meta: dict[str, Any] = {}
        if subagent:
            extra_meta["subagent"] = subagent
        if parent_id:
            extra_meta["parent_id"] = parent_id

        model_upd = payload.get("model")
        if isinstance(model_upd, Mapping):
            for msg in model_upd.get("messages") or []:
                for tc in getattr(msg, "tool_calls", None) or []:
                    if isinstance(tc, Mapping):
                        tc_name = str(tc.get("name") or "")
                        tc_args = tc.get("args") or {}
                        events.append(
                            HarnessEvent(
                                event="tool_start",
                                data={
                                    "id": str(tc.get("id") or ""),
                                    "name": tc_name,
                                    "args": dict(tc_args) if isinstance(tc_args, Mapping) else {},
                                    **extra_meta,
                                },
                            )
                        )

        tools_upd = payload.get("tools")
        if isinstance(tools_upd, Mapping):
            for msg in tools_upd.get("messages") or []:
                tc_id = str(getattr(msg, "tool_call_id", "") or "")
                name = str(getattr(msg, "name", "") or "")
                status = str(getattr(msg, "status", "success") or "success")
                content = getattr(msg, "content", "")
                text = content if isinstance(content, str) else str(content)
                max_len = 16000 if name == "task" else 4000
                events.append(
                    HarnessEvent(
                        event="tool_end",
                        data={
                            "id": tc_id,
                            "name": name,
                            "status": status,
                            "output": text[:max_len],
                            **extra_meta,
                        },
                    )
                )
            todos = tools_upd.get("todos")
            if isinstance(todos, list):
                events.append(HarnessEvent(event="todo_update", data={"todos": todos}))

        return events

    def _collect_artifact_changes(
        self,
        session_id: str,
        prev_mtimes: dict[ArtifactKind, float],
    ) -> tuple[dict[ArtifactKind, float], list[HarnessEvent]]:
        current_mtimes = self._artifact_store.snapshot_mtimes(session_id)
        events: list[HarnessEvent] = []
        for kind, mtime in current_mtimes.items():
            if prev_mtimes.get(kind) != mtime:
                doc = self._artifact_store.get_artifact(session_id, kind)
                events.append(
                    HarnessEvent(
                        event="artifact_updated",
                        data=doc.model_dump(mode="json"),
                    )
                )
        return current_mtimes, events


def _extract_steered_entries(
    payload: Mapping[str, Any],
    emitted_steer_ids: set[str],
) -> list[dict[str, Any]]:
    """Extract newly injected mid-run steering HumanMessages from root graph updates."""
    entries: list[dict[str, Any]] = []
    for node_upd in payload.values():
        if not isinstance(node_upd, Mapping):
            continue
        raw_msgs = node_upd.get("messages")
        if not isinstance(raw_msgs, list | tuple):
            continue
        for msg in raw_msgs:
            additional = getattr(msg, "additional_kwargs", None)
            if not isinstance(additional, Mapping):
                continue
            steer_id = additional.get("lc_steer_id")
            if not isinstance(steer_id, str) or not steer_id or steer_id in emitted_steer_ids:
                continue
            emitted_steer_ids.add(steer_id)
            raw_text = additional.get("lc_steer_raw")
            fallback_content = getattr(msg, "content", "")
            content_str = (
                raw_text
                if isinstance(raw_text, str)
                else (fallback_content if isinstance(fallback_content, str) else "")
            )
            raw_pins = additional.get("lc_steer_pins")
            pins = (
                [str(p) for p in raw_pins if isinstance(p, str)]
                if isinstance(raw_pins, list)
                else []
            )
            entries.append(
                {
                    "id": steer_id,
                    "content": content_str,
                    "pinned_skills": pins,
                }
            )
    return entries


def _subagent_name(namespace: tuple[Any, ...]) -> str | None:
    if not namespace:
        return None
    raw = str(namespace[0])
    return raw.split(":", 1)[0] or raw
