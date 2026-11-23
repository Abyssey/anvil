"""ArtifactWorkflowMiddleware: Plan/Shell HITL interrupts and session artifact sync."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any, NotRequired

from langchain.agents.middleware import AgentMiddleware, hook_config
from langchain.agents.middleware.types import AgentState
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.runtime import Runtime
from langgraph.types import Command, interrupt

from backend.app.domains.agent.run_context import RunContext
from backend.app.domains.agent.shell_guard import ShellAction
from backend.app.domains.artifacts.entities import ArtifactKind
from backend.app.domains.artifacts.store import ArtifactStore

_MUTATING_WORKSPACE_TOOLS = frozenset({"write_file", "edit_file", "delete"})


class ArtifactWorkflowState(AgentState[Any]):
    """Extended graph state tracking durable plan approval across checkpoints."""

    plan_approved: NotRequired[bool]


class ArtifactWorkflowMiddleware(AgentMiddleware[ArtifactWorkflowState, Any, Any]):
    """Coordinates session artifact persistence, plan review, and shell approval interrupts."""

    state_schema = ArtifactWorkflowState

    def __init__(self, *, artifact_store: ArtifactStore) -> None:
        self._store = artifact_store

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        catalog = RunContext.current_or_none()
        if catalog is None:
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
                self._store.write_artifact(catalog.session_id, ArtifactKind.TASK, md)
            return result

        if tool_name in _MUTATING_WORKSPACE_TOOLS:
            file_path = str(args.get("file_path") or "")
            normalized_path = "/" + file_path.strip().lstrip("/")
            is_artifact_write = normalized_path == "/artifacts" or normalized_path.startswith(
                "/artifacts/"
            )
            is_memory_write = (
                normalized_path == "/memories/project"
                or normalized_path.startswith("/memories/project/")
            )
            if catalog.mode == "ask" and is_memory_write:
                return ToolMessage(
                    content="Project memory is read-only in Ask mode.",
                    name=tool_name,
                    tool_call_id=tool_call_id,
                    status="error",
                )
            if (
                catalog.mode == "plan"
                and not is_artifact_write
                and not request.state.get("plan_approved", False)
            ):
                blocked_msg = (
                    "Plan approval required before updating project memory. "
                    "Finish writing /artifacts/implementation_plan.md first."
                    if is_memory_write
                    else (
                        "Plan approval required before editing workspace files. "
                        "Finish writing /artifacts/implementation_plan.md first."
                    )
                )
                return ToolMessage(
                    content=blocked_msg,
                    name=tool_name,
                    tool_call_id=tool_call_id,
                    status="error",
                )
            if (
                not is_artifact_write
                and not is_memory_write
                and catalog.shell_guard.is_verifier_rule_path(normalized_path)
            ):
                decision = interrupt(
                    {
                        "kind": "shell_approval",
                        "command": f"{tool_name} {normalized_path}",
                        "workspace": str(catalog.workspace_path),
                        "reason": (
                            "Modifying verification, architecture boundary, or review rule files "
                            "requires explicit user approval so checks stay independent."
                        ),
                        "summary": f"Approve modifying governance/check file: {normalized_path}",
                    }
                )
                approved = isinstance(decision, Mapping) and decision.get("decision") == "approve"
                if not approved:
                    return ToolMessage(
                        content=(
                            f"Modification of governance/check file denied by user: "
                            f"{normalized_path}"
                        ),
                        name=tool_name,
                        tool_call_id=tool_call_id,
                        status="error",
                    )
            return await handler(request)

        if tool_name == "execute":
            command = str(args.get("command") or "")
            plan_approved = bool(request.state.get("plan_approved", False))
            if catalog.mode == "plan" and not plan_approved:
                return ToolMessage(
                    content="Plan approval required before executing shell commands.",
                    name="execute",
                    tool_call_id=tool_call_id,
                    status="error",
                )
            verdict = catalog.shell_guard.evaluate(
                command,
                catalog.mode,
                plan_approved=plan_approved,
            )
            if verdict.action == ShellAction.BLOCK:
                return ToolMessage(
                    content=f"Command blocked: {verdict.reason}",
                    name="execute",
                    tool_call_id=tool_call_id,
                    status="error",
                )
            if verdict.action == ShellAction.REQUIRE_APPROVAL:
                decision = interrupt(
                    {
                        "kind": "shell_approval",
                        "command": command,
                        "workspace": str(catalog.workspace_path),
                        "reason": verdict.reason,
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
        catalog = RunContext.current_or_none()
        if catalog is None:
            return None

        if catalog.mode == "plan" and not state.get("plan_approved", False):
            plan_path = catalog.artifacts_dir / "implementation_plan.md"
            if not plan_path.is_file():
                fallback = (
                    self._last_assistant_text(state)
                    or "# Implementation Plan\nPending review."
                )
                self._store.write_artifact(
                    catalog.session_id,
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
                                "and update /artifacts/walkthrough.md. "
                                "Write all user-facing responses and artifacts in Korean "
                                "(한국어) by default."
                            )
                        )
                    ],
                    "jump_to": "model",
                }
            if isinstance(resume_val, Mapping) and resume_val.get("decision") == "deny":
                return None
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
                            "Update /artifacts/implementation_plan.md accordingly. "
                            "Write all user-facing responses and artifacts in Korean "
                            "(한국어) by default."
                        )
                    )
                ],
                "jump_to": "model",
            }

        if catalog.mode == "ask":
            walkthrough_path = catalog.artifacts_dir / "walkthrough.md"
            if not walkthrough_path.is_file():
                final_text = self._last_assistant_text(state)
                if final_text:
                    self._store.write_artifact(
                        catalog.session_id,
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
