"""Pre-turn and post-mutation Git snapshot tracker (Open SWE GitEvidence pattern)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import AgentState
from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.runtime import Runtime
from langgraph.types import Command

from backend.app.domains.agent.git_utils import run_git
from backend.app.domains.agent.run_context import RunContext

_MUTATING_TOOLS = frozenset({"write_file", "edit_file", "delete", "execute"})


@dataclass(frozen=True)
class GitSnapshot:
    is_git_repo: bool
    branch: str = ""
    head: str = ""
    dirty_entries: tuple[str, ...] = ()


@dataclass
class GitEvidenceTracker:
    """Captures baseline and latest git snapshots for one agent run."""

    workspace_path: Path
    baseline: GitSnapshot | None = None
    latest: GitSnapshot | None = None
    _changed_files: list[str] = field(default_factory=list)

    @property
    def has_changes(self) -> bool:
        if self.latest is None or not self.latest.is_git_repo:
            return False
        if self.baseline is None:
            return bool(self.latest.dirty_entries)
        return (
            self.latest.head != self.baseline.head
            or self.latest.dirty_entries != self.baseline.dirty_entries
        )

    @property
    def changed_files(self) -> list[str]:
        return list(self._changed_files)

    async def capture_baseline(self) -> GitSnapshot:
        snap = await self._probe_git()
        self.baseline = snap
        self.latest = snap
        self._changed_files = self._extract_paths(snap)
        return snap

    async def refresh(self) -> GitSnapshot:
        snap = await self._probe_git()
        self.latest = snap
        self._changed_files = self._extract_paths(snap)
        return snap

    @staticmethod
    def _extract_paths(snap: GitSnapshot) -> list[str]:
        paths: list[str] = []
        for entry in snap.dirty_entries:
            if len(entry) >= 4:
                paths.append(entry[3:].strip())
        return paths

    async def _probe_git(self) -> GitSnapshot:
        if not (self.workspace_path / ".git").exists():
            is_inside = await run_git(self.workspace_path, "rev-parse", "--is-inside-work-tree")
            if is_inside != "true":
                return GitSnapshot(is_git_repo=False)

        branch = await run_git(self.workspace_path, "rev-parse", "--abbrev-ref", "HEAD")
        head = await run_git(self.workspace_path, "rev-parse", "HEAD")
        status_raw = await run_git(self.workspace_path, "status", "--porcelain")
        entries = tuple(line for line in status_raw.splitlines() if line.strip())
        return GitSnapshot(is_git_repo=True, branch=branch, head=head, dirty_entries=entries)


class GitEvidenceMiddleware(AgentMiddleware):
    """Records baseline git/checkpoint snapshots on run start and refreshes after mutations."""

    async def abefore_agent(
        self,
        state: AgentState[Any],
        runtime: Runtime[Any],
    ) -> dict[str, Any] | None:
        del state, runtime
        catalog = RunContext.current_or_none()
        if catalog is not None:
            if catalog.git_tracker is not None:
                await catalog.git_tracker.capture_baseline()
            if catalog.checkpoint_capture is not None and catalog.mode in {"plan", "auto"}:
                await catalog.checkpoint_capture.capture_baseline()
        return None

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        tool_name = str(request.tool_call.get("name") or "")
        catalog = RunContext.current_or_none()
        if (
            catalog is not None
            and catalog.checkpoint_capture is not None
            and catalog.mode in {"plan", "auto"}
            and tool_name in {"write_file", "edit_file", "delete"}
        ):
            args = request.tool_call.get("args") or {}
            file_path = str(args.get("file_path") or "")
            if file_path:
                await catalog.checkpoint_capture.snapshot_before_write(file_path)

        result = await handler(request)
        if tool_name in _MUTATING_TOOLS and catalog is not None and catalog.git_tracker is not None:
            await catalog.git_tracker.refresh()
        return result
