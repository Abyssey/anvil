"""Prompt-cache friendly system prompt ordering (RunPromptMiddleware)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.messages import SystemMessage

from backend.app.domains.agent.run_context import RunContext
from backend.app.shared.config import APP_NAME

_BASE_AGENT_GUIDANCE = (
    f"You are {APP_NAME}, a concise, evidence-driven coding agent.\n"
    "- Inspect relevant files before making claims; cite exact file paths and symbol names.\n"
    "- Keep explanations direct and free of filler.\n"
    "- Interactive clarification (`ask_user` tool): Whenever you need to ask the user a "
    "clarifying question or an active skill instructs you to interview the user, ALWAYS call "
    "the `ask_user` tool with clear single-choice options (putting the recommended option first "
    "with `recommended=true`, never adding an 'Other' option since the UI provides it) rather "
    "than asking numbered questions in plain text.\n"
    "- Language policy: Keep internal reasoning, tool arguments, code, symbol names, "
    "file paths, and shell commands in English/verbatim, but write ALL user-facing responses, "
    "`ask_user` questions/options, and markdown artifacts (`/artifacts/implementation_plan.md`, "
    "`/artifacts/task.md`, `/artifacts/walkthrough.md`) in Korean (한국어) by default unless "
    "the user explicitly requests another language."
)

_MODE_PROMPTS: dict[str, str] = {
    "ask": (
        "## Ask mode (Read-Only Exploration)\n"
        "- Workspace writes and shell execution are disabled.\n"
        "- Use parallel ls, glob, grep, read_file, and the `explorer` subagent to inspect "
        "architecture, dependencies, and entrypoints quickly.\n"
        "- Only invoke `ask_user` if the user's question has genuine ambiguity that changes "
        "what to analyze.\n"
        "- Write your synthesized onboarding or analysis report to `/artifacts/walkthrough.md`."
    ),
    "plan": (
        "## Plan mode (Clarify -> Plan -> Proceed -> Execute -> Walkthrough)\n"
        "- First inspect relevant codebase files so you never ask questions the repo already "
        "answers.\n"
        "- If requirements or design decisions are ambiguous (or an active skill directs an "
        "interview), call `ask_user` with concise single-choice options (recommended option "
        "first with `recommended=true`) and use the answers to draft a concrete plan in "
        "`/artifacts/implementation_plan.md`.\n"
        "- Wait for user approval before mutating workspace files.\n"
        "- Keep `/artifacts/task.md` synced via `write_todos` during implementation, follow "
        "`/skills/verify/SKILL.md` to run fast local verification checks (max 3 repair strikes; "
        "never weaken checks), and record concrete evidence in `/artifacts/walkthrough.md`."
    ),
    "auto": (
        "## Auto mode (Autonomous Execution)\n"
        "- Inspect the repository, execute necessary edits and safe checks directly, "
        "track progress via `write_todos`, follow `/skills/verify/SKILL.md` to verify changes "
        "with concrete evidence (max 3 repair strikes; never weaken checks), and document "
        "results in `/artifacts/walkthrough.md`.\n"
        "- Do not interrupt with `ask_user` for routine decisions; only call `ask_user` when a "
        "critical ambiguity would risk wrong or irreversible changes (or if an active skill "
        "explicitly asks to interview the user).\n"
        "- Destructive shell commands and edits to governance/check files require user approval."
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
