"""Read-only explorer and reviewer subagent specifications (Open SWE pattern)."""

from __future__ import annotations

from deepagents.middleware.subagents import SubAgent
from langchain_core.language_models.chat_models import BaseChatModel

from backend.app.domains.agent.middleware.model_retries import ModelRetryMiddleware
from backend.app.domains.agent.middleware.read_only_guard import ReadOnlyToolGuardMiddleware
from backend.app.domains.agent.middleware.subdir_agents import SubdirAgentsReadMiddleware
from backend.app.domains.agent.middleware.tool_errors import ToolErrorMiddleware

_EXPLORER_PROMPT = (
    "You are a fast read-only codebase explorer.\n"
    "Map directory structure, entrypoints, dependencies, and control flow using "
    "`ls`, `glob`, `grep`, and `read_file` in parallel.\n"
    "Cite exact file paths and symbol names. Do not fabricate anything you did not read.\n"
    "Write user-facing summaries in Korean (한국어) by default while preserving file paths, "
    "symbol names, and code verbatim."
)

_REVIEWER_PROMPT = (
    "You are an independent read-only code reviewer.\n"
    "Inspect modified files and git changes for bugs, edge cases, typing errors, "
    "and DDD boundary violations using `ls`, `glob`, `grep`, and `read_file`.\n"
    "Return concise, actionable findings in Korean (한국어) by default with exact file paths "
    "and line references."
)


def build_subagents(
    *,
    mode: str,
    model: BaseChatModel,
) -> list[SubAgent]:
    """Build mode-appropriate read-only subagent specs with zero HITL conflict."""
    shared_middleware = [
        ReadOnlyToolGuardMiddleware(),
        SubdirAgentsReadMiddleware(),
        ToolErrorMiddleware(),
        ModelRetryMiddleware(),
    ]
    subagents: list[SubAgent] = [
        {
            "name": "explorer",
            "description": (
                "Parallel read-only researcher that maps subsystems, dependencies, "
                "and symbols across the codebase without modifying files."
            ),
            "system_prompt": _EXPLORER_PROMPT,
            "model": model,
            "middleware": list(shared_middleware),
            "permissions": None,  # type: ignore[typeddict-item]
        }
    ]
    if mode in {"plan", "auto"}:
        subagents.append(
            {
                "name": "reviewer",
                "description": (
                    "Independent read-only reviewer inspecting code changes for bugs, "
                    "edge cases, and architecture compliance."
                ),
                "system_prompt": _REVIEWER_PROMPT,
                "model": model,
                "middleware": list(shared_middleware),
                "permissions": None,  # type: ignore[typeddict-item]
            }
        )
    return subagents
