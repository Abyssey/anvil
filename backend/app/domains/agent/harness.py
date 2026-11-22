"""DeepAgentHarness: Bounded LRU graph cache + AsyncSqliteSaver + typed event stream."""

from __future__ import annotations

import inspect
import logging
from collections import OrderedDict
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from deepagents import create_deep_agent
from deepagents.middleware.filesystem import FilesystemPermission
from langchain.agents.middleware import TodoListMiddleware
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command

from backend.app.domains.agent.middleware.git_evidence import (
    GitEvidenceMiddleware,
    GitEvidenceTracker,
)
from backend.app.domains.agent.middleware.memory import (
    PROJECT_MEMORY_SOURCES,
    DynamicMemoryMiddleware,
)
from backend.app.domains.agent.middleware.model_retries import ModelRetryMiddleware
from backend.app.domains.agent.middleware.run_prompt import RunPromptMiddleware
from backend.app.domains.agent.middleware.subdir_agents import SubdirAgentsReadMiddleware
from backend.app.domains.agent.middleware.tool_errors import ToolErrorMiddleware
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
from backend.app.shared.config import AppConfig
from backend.app.shared.errors import PathEscapeError
from backend.app.shared.llm_credentials import LLMCredentials

logger = logging.getLogger(__name__)

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

    async def delete_thread(self, session_id: str) -> None:
        """Delete LangGraph checkpoints and in-memory session middleware caches for a session."""
        for mw in self._subdir_agents_by_key.values():
            mw.clear_session(session_id)
        if self._checkpointer is not None and hasattr(self._checkpointer, "adelete_thread"):
            setup_fn = getattr(self._checkpointer, "setup", None)
            if callable(setup_fn):
                maybe_coro = setup_fn()
                if inspect.isawaitable(maybe_coro):
                    await maybe_coro
            await self._checkpointer.adelete_thread(session_id)

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
                RunPromptMiddleware(mode=mode),
                memory_mw,
                subdir_mw,
                ToolErrorMiddleware(),
                ModelRetryMiddleware(),
                GitEvidenceMiddleware(),
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
        tracker = GitEvidenceTracker(workspace_path=workspace_path)
        return RunCatalog(
            session_id=session_id,
            project_id=project_id or "",
            workspace_path=workspace_path.resolve(),
            mode=mode,
            artifacts_dir=artifacts_dir,
            project_memory_dir=self._project_memory_dir(project_id),
            shell_guard=self._shell_guard,
            secret_values=(creds.api_key,),
            extra_skills_dirs=(),
            git_tracker=tracker,
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
    ) -> AsyncIterator[HarnessEvent]:
        graph = self._get_or_build_graph(creds, mode)
        catalog = self._build_catalog(
            session_id=session_id,
            workspace_path=workspace_path,
            mode=mode,
            creds=creds,
            project_id=project_id,
        )
        run_input: dict[str, Any] = {
            "messages": [("user", user_message)],
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

        async with RunContext.activate(catalog):
            try:
                async for item in graph.astream(
                    graph_input,
                    config=config,  # type: ignore[arg-type]
                    stream_mode=["messages", "updates"],
                    subgraphs=True,
                ):
                    if not isinstance(item, tuple) or len(item) != 3:
                        continue
                    namespace, stream_mode_name, payload = item
                    subagent_label = _subagent_name(namespace)

                    if stream_mode_name == "messages" and isinstance(payload, tuple):
                        chunk = payload[0]
                        for ev in self._events_from_message_chunk(chunk, subagent_label):
                            yield ev
                        continue

                    if stream_mode_name == "updates" and isinstance(payload, Mapping):
                        for ev in self._events_from_update(payload, subagent_label):
                            if ev.event == "interrupt":
                                interrupted = True
                            yield ev

                        prev_mtimes, art_events = self._collect_artifact_changes(
                            catalog.session_id,
                            prev_mtimes,
                        )
                        for art_ev in art_events:
                            yield art_ev

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
                yield HarnessEvent(
                    event="error",
                    data={"code": "AGENT_EXECUTION_ERROR", "message": safe_msg},
                )
                return

        _, final_art_events = self._collect_artifact_changes(catalog.session_id, prev_mtimes)
        for art_ev in final_art_events:
            yield art_ev

        if catalog.git_tracker is not None and catalog.git_tracker.has_changes:
            yield HarnessEvent(
                event="diff_changed",
                data={"changed_files": catalog.git_tracker.changed_files},
            )

        if not interrupted:
            yield HarnessEvent(event="done", data={"session_id": catalog.session_id})

    def _events_from_message_chunk(
        self,
        chunk: object,
        subagent: str | None,
    ) -> list[HarnessEvent]:
        events: list[HarnessEvent] = []
        sub_extra = {"subagent": subagent} if subagent else {}
        additional = getattr(chunk, "additional_kwargs", None)
        if isinstance(additional, Mapping):
            reasoning = additional.get("reasoning_content")
            if isinstance(reasoning, str) and reasoning:
                events.append(
                    HarnessEvent(
                        event="thinking",
                        data={"text": reasoning, **sub_extra},
                    )
                )
        content = getattr(chunk, "content", None)
        is_ai_chunk = getattr(chunk, "type", "") == "AIMessageChunk"
        if isinstance(content, str):
            if content and is_ai_chunk and subagent is None:
                events.append(HarnessEvent(event="token", data={"text": content}))
        elif isinstance(content, list) and is_ai_chunk:
            for block in content:
                if not isinstance(block, Mapping):
                    continue
                btype = str(block.get("type") or "")
                if btype in {"thinking", "reasoning"}:
                    think_text = block.get("thinking") or block.get("text")
                    if isinstance(think_text, str) and think_text:
                        events.append(
                            HarnessEvent(
                                event="thinking",
                                data={"text": think_text, **sub_extra},
                            )
                        )
                elif btype == "text" and subagent is None:
                    text_val = block.get("text")
                    if isinstance(text_val, str) and text_val:
                        events.append(HarnessEvent(event="token", data={"text": text_val}))
        return events

    def _events_from_update(
        self,
        payload: Mapping[str, Any],
        subagent: str | None,
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
                                    **({"subagent": subagent} if subagent else {}),
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
                events.append(
                    HarnessEvent(
                        event="tool_end",
                        data={
                            "id": tc_id,
                            "name": name,
                            "status": status,
                            "output": text[:4000],
                            **({"subagent": subagent} if subagent else {}),
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


def _subagent_name(namespace: tuple[Any, ...]) -> str | None:
    if not namespace:
        return None
    raw = str(namespace[0])
    return raw.split(":", 1)[0] or raw
