"""Antigravity ArtifactWorkflowMiddleware: Plan/Shell HITL interrupts and artifact sync."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NotRequired

from langchain.agents.middleware import AgentMiddleware, hook_config
from langchain.agents.middleware.types import AgentState
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.runtime import Runtime
from langgraph.types import Command, interrupt

from backend.app.domains.artifacts.entities import ArtifactKind
from backend.app.domains.artifacts.store import ArtifactStore

_MUTATING_WORKSPACE_TOOLS = frozenset({"write_file", "edit_file", "delete"})


@dataclass(frozen=True)
class ArtifactRunInfo:
    session_id: str
    workspace_path: Path
    mode: str
    artifacts_dir: Path


class ArtifactWorkflowState(AgentState[Any]):
    """Extended graph state tracking durable plan approval across checkpoints."""

    plan_approved: NotRequired[bool]


class ArtifactWorkflowMiddleware(AgentMiddleware[ArtifactWorkflowState, Any, Any]):
    """Coordinates Antigravity artifact persistence, plan HITL, and shell HITL interrupts."""

    state_schema = ArtifactWorkflowState

    def __init__(
        self,
        *,
        artifact_store: ArtifactStore,
        get_run_info: Callable[[], ArtifactRunInfo | None],
        evaluate_shell: Callable[[str, str], tuple[str, str]],
    ) -> None:
        self._store = artifact_store
        self._get_run_info = get_run_info
        self._evaluate_shell = evaluate_shell

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        info = self._get_run_info()
        if info is None:
            return await handler(request)

        tool_name = str(request.tool_call.get("name") or "")
        args = request.tool_call.get("args") or {}
        tool_call_id = str(request.tool_call.get("id") or "")

        if tool_name == "write_todos":
            result = await handler(request)
            todos = args.get("todos")
            if isinstance(todos, list):
                md = self._store.render_todos_markdown(
                    [t for t in todos if isinstance(t, Mapping)]
                )
                self._store.write_artifact(info.session_id, ArtifactKind.TASK, md)
            return result

        if tool_name in _MUTATING_WORKSPACE_TOOLS:
            file_path = str(args.get("file_path") or "")
            is_artifact_write = file_path.startswith("/artifacts/")
            if (
                info.mode == "plan"
                and not is_artifact_write
                and not request.state.get("plan_approved", False)
            ):
                return ToolMessage(
                    content=(
                        "Plan approval required before editing workspace files. "
                        "Finish writing /artifacts/implementation_plan.md first."
                    ),
                    name=tool_name,
                    tool_call_id=tool_call_id,
                    status="error",
                )
            return await handler(request)

        if tool_name == "execute":
            command = str(args.get("command") or "")
            if info.mode == "plan" and not request.state.get("plan_approved", False):
                return ToolMessage(
                    content="Plan approval required before executing shell commands.",
                    name="execute",
                    tool_call_id=tool_call_id,
                    status="error",
                )
            action, reason = self._evaluate_shell(command, info.mode)
            if action == "block":
                return ToolMessage(
                    content=f"Command blocked: {reason}",
                    name="execute",
                    tool_call_id=tool_call_id,
                    status="error",
                )
            if action == "require_approval":
                decision = interrupt(
                    {
                        "kind": "shell_approval",
                        "command": command,
                        "workspace": str(info.workspace_path),
                        "reason": reason,
                        "summary": f"Approve shell command: {command}",
                    }
                )
                approved = isinstance(decision, Mapping) and decision.get("decision") == "approve"
                if not approved:
                    return ToolMessage(
                        content=f"Shell command denied by user: {command}",
                        name="execute",
                        tool_call_id=tool_call_id,
                        status="error",
                    )
            return await handler(request)

        return await handler(request)

    @hook_config(can_jump_to=["model"])
    async def aafter_agent(
        self,
        state: ArtifactWorkflowState,
        runtime: Runtime[Any],
    ) -> dict[str, Any] | None:
        info = self._get_run_info()
        if info is None:
            return None

        if info.mode == "plan" and not state.get("plan_approved", False):
            plan_path = info.artifacts_dir / "implementation_plan.md"
            if not plan_path.is_file():
                fallback = (
                    self._last_assistant_text(state)
                    or "# Implementation Plan\nPending review."
                )
                self._store.write_artifact(
                    info.session_id,
                    ArtifactKind.IMPLEMENTATION_PLAN,
                    fallback,
                )

            resume_val = interrupt(
                {
                    "kind": "plan_review",
                    "artifact_kind": ArtifactKind.IMPLEMENTATION_PLAN.value,
                    "summary": (
                        "Review implementation_plan.md and click Proceed or request changes."
                    ),
                }
            )
            if isinstance(resume_val, Mapping) and resume_val.get("decision") == "approve":
                return {
                    "plan_approved": True,
                    "messages": [
                        HumanMessage(
                            content=(
                                "Plan approved. Implement the plan, verify changes, "
                                "and update /artifacts/walkthrough.md."
                            )
                        )
                    ],
                    "jump_to": "model",
                }
            feedback = (
                str(resume_val.get("feedback") or "Please revise the implementation plan.")
                if isinstance(resume_val, Mapping)
                else "Please revise the implementation plan."
            )
            return {
                "plan_approved": False,
                "messages": [
                    HumanMessage(
                        content=(
                            f"Plan revision requested: {feedback}. "
                            "Update /artifacts/implementation_plan.md accordingly."
                        )
                    )
                ],
                "jump_to": "model",
            }

        if info.mode == "ask":
            walkthrough_path = info.artifacts_dir / "walkthrough.md"
            if not walkthrough_path.is_file():
                final_text = self._last_assistant_text(state)
                if final_text:
                    self._store.write_artifact(
                        info.session_id,
                        ArtifactKind.WALKTHROUGH,
                        final_text,
                    )

        # Terminal completion MUST return None to exit cleanly without GraphRecursionError.
        return None

    @staticmethod
    def _last_assistant_text(state: ArtifactWorkflowState) -> str:
        for msg in reversed(state.get("messages", [])):
            if isinstance(msg, AIMessage) and isinstance(msg.content, str) and msg.content.strip():
                return msg.content.strip()
        return ""
