"""Auto-inject ancestor AGENTS.md instructions when reading files (Open SWE pattern)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

from backend.app.domains.agent.run_context import RunContext

_MAX_INJECTED_BYTES = 64 * 1024


class SubdirAgentsReadMiddleware(AgentMiddleware):
    """Loads ancestor AGENTS.md files once per session when `read_file` reads a workspace file."""

    def __init__(self, *, skip_root: bool = False) -> None:
        self._skip_root = skip_root
        self._loaded_by_session: dict[str, set[str]] = {}

    def clear_session(self, session_id: str) -> None:
        """Evict cached ancestor tracking for a deleted or reset session."""
        self._loaded_by_session.pop(session_id, None)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        result = await handler(request)
        if not isinstance(result, ToolMessage) or result.status == "error":
            return result
        if request.tool_call.get("name") != "read_file":
            return result

        catalog = RunContext.current_or_none()
        if catalog is None:
            return result

        raw_path = request.tool_call.get("args", {}).get("file_path")
        if not isinstance(raw_path, str):
            return result
        clean_virtual = raw_path.strip().lstrip("/")
        if clean_virtual.startswith(("artifacts/", "skills/", "memories/")):
            return result

        workspace = catalog.workspace_path.resolve()
        target = (workspace / clean_virtual).resolve()
        try:
            rel = target.relative_to(workspace)
        except ValueError:
            return result

        memory_contents = (
            request.state.get("memory_contents")
            if isinstance(request.state, Mapping)
            else None
        )
        root_already_loaded = (
            self._skip_root
            or (isinstance(memory_contents, Mapping) and "/AGENTS.md" in memory_contents)
        )
        root_agents_file = (workspace / "AGENTS.md").resolve()

        seen = self._loaded_by_session.setdefault(catalog.session_id, set())
        candidates = self._ancestor_agents_files(workspace, workspace / rel.parent)
        additions: list[str] = []
        total_bytes = 0

        for agents_file in candidates:
            if root_already_loaded and agents_file.resolve() == root_agents_file:
                continue
            key = str(agents_file)
            if key in seen or not agents_file.is_file():
                continue
            raw_bytes = agents_file.read_bytes()
            if total_bytes + len(raw_bytes) > _MAX_INJECTED_BYTES:
                continue
            seen.add(key)
            total_bytes += len(raw_bytes)
            rel_agents = agents_file.relative_to(workspace).as_posix()
            text = raw_bytes.decode("utf-8", errors="replace").strip()
            if text:
                additions.append(f"<agents_md path=\"/{rel_agents}\">\n{text}\n</agents_md>")

        if not additions:
            return result

        suffix = "\n\n" + "\n\n".join(additions)
        updated_content = f"{result.content}{suffix}"
        return result.model_copy(update={"content": updated_content})

    @staticmethod
    def _ancestor_agents_files(workspace: Path, start_dir: Path) -> list[Path]:
        chain: list[Path] = []
        current = start_dir.resolve()
        while True:
            chain.append(current / "AGENTS.md")
            if current == workspace or current.parent == current:
                break
            current = current.parent
        chain.reverse()
        return chain
