"""Prompt-cache friendly system prompt ordering (Open SWE RunPromptMiddleware)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.messages import SystemMessage

from backend.app.domains.agent.run_context import RunContext

_BASE_AGENT_GUIDANCE = (
    "You are Abyss, a concise, evidence-driven coding agent.\n"
    "- Inspect relevant files before making claims; cite exact file paths and symbol names.\n"
    "- Keep explanations direct and free of filler."
)

_MODE_PROMPTS: dict[str, str] = {
    "ask": (
        "## Ask mode (Read-Only Exploration)\n"
        "- Workspace writes and shell execution are disabled.\n"
        "- Use parallel ls, glob, grep, read_file, and the `explorer` subagent to inspect "
        "architecture, dependencies, and entrypoints quickly.\n"
        "- Write your synthesized onboarding or analysis report to `/artifacts/walkthrough.md`."
    ),
    "plan": (
        "## Plan mode (Plan -> Proceed -> Execute -> Walkthrough)\n"
        "- First inspect the codebase and write a concrete plan to "
        "`/artifacts/implementation_plan.md`.\n"
        "- Wait for user approval before mutating workspace files.\n"
        "- Keep `/artifacts/task.md` synced via `write_todos` during implementation, run "
        "verification checks, and record evidence in `/artifacts/walkthrough.md`."
    ),
    "auto": (
        "## Auto mode (Autonomous Execution)\n"
        "- Inspect the repository, execute necessary edits and safe checks directly, "
        "track progress via `write_todos`, and document results in `/artifacts/walkthrough.md`.\n"
        "- Destructive shell commands still require user approval."
    ),
}


def _extract_text(system_message: SystemMessage | None) -> str:
    if system_message is None or not system_message.content:
        return ""
    if isinstance(system_message.content, str):
        return system_message.content
    parts: list[str] = []
    for block in system_message.content:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict) and isinstance(block.get("text"), str):
            parts.append(block["text"])
    return "\n\n".join(p for p in parts if p)


class RunPromptMiddleware(AgentMiddleware):
    """Orders system prompt sections from most-static to most-dynamic for prefix caching."""

    def __init__(self, mode: str) -> None:
        self._mode = mode

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        catalog = RunContext.current_or_none()
        active_mode = catalog.mode if catalog is not None else self._mode
        sections = [
            _extract_text(request.system_message),
            _BASE_AGENT_GUIDANCE,
            _MODE_PROMPTS.get(active_mode, _MODE_PROMPTS["ask"]),
        ]
        if catalog is not None:
            sections.append(
                f"## Active Workspace\n"
                f"- Workspace Path: `{catalog.workspace_path}`\n"
                f"- Session ID: `{catalog.session_id}`\n"
                f"- Mode: `{active_mode}`"
            )
        merged = "\n\n".join(part for part in sections if part.strip())
        updated = request.override(system_message=SystemMessage(content=merged))
        return await handler(updated)
