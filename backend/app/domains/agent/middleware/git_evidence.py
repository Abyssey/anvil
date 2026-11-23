"""Pre-turn and post-mutation Git snapshot tracker."""

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
        raw = await run_git(
            self.workspace_path,
            "status",
            "--porcelain=v2",
            "--branch",
            "-z",
            strip_output=False,
        )
        if not raw:
            return GitSnapshot(is_git_repo=False)

        branch = ""
        head = ""
        entries: list[str] = []
        tokens = raw.split("\0")
        idx = 0
        total = len(tokens)
        while idx < total:
            token = tokens[idx]
            idx += 1
            if not token:
                continue
            if token.startswith("# "):
                if token.startswith("# branch.oid "):
                    oid = token.removeprefix("# branch.oid ").strip()
                    head = "" if oid == "(initial)" else oid
                elif token.startswith("# branch.head "):
                    bname = token.removeprefix("# branch.head ").strip()
                    branch = "HEAD" if bname == "(detached)" else bname
                continue
            if token.startswith("1 "):
                parts = token.split(" ", 8)
                if len(parts) == 9:
                    xy = parts[1][:2].ljust(2)
                    entries.append(f"{xy} {parts[8]}")
            elif token.startswith("2 "):
                parts = token.split(" ", 9)
                if len(parts) == 10:
                    xy = parts[1][:2].ljust(2)
                    entries.append(f"{xy} {parts[9]}")
                # Consume the trailing NUL-delimited <origPath> record
                if idx < total:
                    idx += 1
            elif token.startswith("u "):
                parts = token.split(" ", 10)
                if len(parts) == 11:
                    xy = parts[1][:2].ljust(2)
                    entries.append(f"{xy} {parts[10]}")
            elif token.startswith("? "):
                entries.append(f"?? {token[2:]}")

        return GitSnapshot(
            is_git_repo=True,
            branch=branch,
            head=head,
            dirty_entries=tuple(entries),
        )


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
