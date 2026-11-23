"""Verification feedback-loop middleware (Three-Strike rule + Observe-mode verification stamps)."""

from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import AgentState
from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.runtime import Runtime
from langgraph.types import Command

from backend.app.domains.agent.run_context import RunContext

logger = logging.getLogger(__name__)

VerificationStamp = Literal["passed", "failed", "none", "not_needed"]

_EXIT_CODE_RE = re.compile(
    r"(?:exit[_\s-]?code|returncode)(?:\s*[:=]\s*|\s+)(-?\d+)",
    re.IGNORECASE,
)
_WORKSPACE_MUTATING_FILE_TOOLS = frozenset({"write_file", "edit_file", "delete"})

_THREE_STRIKE_NOTICE = (
    "\n\n[Verification Three-Strike Rule] Automated verification has failed 3 consecutive times. "
    "Stop retrying blindly, adding sleeps, or weakening tests. Summarize the exact failure "
    "evidence and root-cause hypothesis for the user (or call `ask_user` if a design decision "
    "is needed)."
)


def _is_virtual_non_workspace_path(raw_path: str) -> bool:
    clean = "/" + raw_path.strip().lstrip("/")
    return (
        clean == "/artifacts"
        or clean.startswith("/artifacts/")
        or clean == "/memories"
        or clean.startswith("/memories/")
        or clean == "/skills"
        or clean.startswith("/skills/")
    )


def _did_shell_command_succeed(result: ToolMessage | Command) -> bool:
    if not isinstance(result, ToolMessage):
        return True
    if getattr(result, "status", None) == "error":
        return False
    artifact = getattr(result, "artifact", None)
    if isinstance(artifact, dict):
        raw_code = artifact.get("exit_code")
        if isinstance(raw_code, int) and not isinstance(raw_code, bool):
            return raw_code == 0
    text = result.content if isinstance(result.content, str) else str(result.content)
    matches = _EXIT_CODE_RE.findall(text)
    if matches:
        return int(matches[-1]) == 0
    return True


@dataclass
class VerificationTracker:
    """Per-run state tracking workspace mutations and verification outcomes."""

    max_strikes: int = 3
    mutated_workspace: bool = False
    verification_commands: list[str] = field(default_factory=list)
    last_verification_passed: bool | None = None
    consecutive_failures: int = 0

    def record_workspace_mutation(self) -> None:
        self.mutated_workspace = True
        # Subsequent code mutations invalidate an earlier passing check until re-verified.
        if self.last_verification_passed is True:
            self.last_verification_passed = None

    def record_verification(self, command: str, *, passed: bool) -> bool:
        """Record a verification run; return True if Three-Strike threshold was reached."""
        self.verification_commands.append(command.strip())
        self.last_verification_passed = passed
        if passed:
            self.consecutive_failures = 0
            return False
        self.consecutive_failures += 1
        return self.consecutive_failures >= self.max_strikes

    @property
    def stamp(self) -> VerificationStamp:
        if not self.mutated_workspace:
            if self.last_verification_passed is True:
                return "passed"
            if self.last_verification_passed is False:
                return "failed"
            return "not_needed"
        if self.last_verification_passed is True:
            return "passed"
        if self.last_verification_passed is False:
            return "failed"
        return "none"


class VerificationLoopMiddleware(AgentMiddleware):
    """Tracks verification evidence, Three-Strike stop hints, and Observe-mode stamps."""

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        result = await handler(request)
        catalog = RunContext.current_or_none()
        if catalog is None or catalog.verification_tracker is None:
            return result

        tracker = catalog.verification_tracker
        tool_name = str(request.tool_call.get("name") or "")
        args = request.tool_call.get("args") or {}

        if tool_name in _WORKSPACE_MUTATING_FILE_TOOLS:
            if isinstance(result, ToolMessage) and getattr(result, "status", None) == "error":
                return result
            file_path = str(args.get("file_path") or "")
            if not _is_virtual_non_workspace_path(file_path):
                tracker.record_workspace_mutation()
            return result

        if tool_name == "execute":
            command = str(args.get("command") or "")
            if isinstance(result, ToolMessage) and getattr(result, "status", None) == "error":
                # Blocked or denied by ShellGuard / HITL before running
                content_str = (
                    result.content if isinstance(result.content, str) else str(result.content)
                )
                if content_str.startswith(
                    ("Command blocked:", "Shell command denied", "Plan approval")
                ):
                    return result

            if catalog.shell_guard.is_verification_command(command):
                passed = _did_shell_command_succeed(result)
                hit_three_strikes = tracker.record_verification(command, passed=passed)
                if hit_three_strikes and isinstance(result, ToolMessage):
                    existing = (
                        result.content if isinstance(result.content, str) else str(result.content)
                    )
                    return ToolMessage(
                        content=f"{existing}{_THREE_STRIKE_NOTICE}",
                        name=result.name or "execute",
                        tool_call_id=result.tool_call_id,
                        status=result.status,
                        artifact=getattr(result, "artifact", None),
                    )
            return result

        return result

    async def aafter_agent(
        self,
        state: AgentState[Any],
        runtime: Runtime[Any],
    ) -> dict[str, Any] | None:
        del state, runtime
        catalog = RunContext.current_or_none()
        if catalog is None or catalog.verification_tracker is None:
            return None

        tracker = catalog.verification_tracker
        # Also detect workspace mutations made via non-test shell commands
        if (
            not tracker.mutated_workspace
            and catalog.git_tracker is not None
            and catalog.git_tracker.has_changes
            and catalog.git_tracker.baseline is not None
            and (
                catalog.git_tracker.latest != catalog.git_tracker.baseline
            )
        ):
            tracker.mutated_workspace = True

        if catalog.mode in {"plan", "auto"} and tracker.stamp == "none":
            logger.info(
                "verification.observe_unverified_mutation",
                extra={
                    "event": "verification.observe_unverified_mutation",
                    "session_id": catalog.session_id,
                    "mode": catalog.mode,
                    "verification_stamp": "none",
                },
            )
        return None
