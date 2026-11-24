"""LanguageGuardMiddleware: Pre-flight tool CJK/regex guard and history decontamination."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from pathlib import Path

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langgraph.errors import GraphBubbleUp, NodeCancelledError
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

from backend.app.domains.agent.cjk_guard import (
    CjkAllowance,
    detect_broken_grep_pattern,
    detect_broken_shell_cjk_regex,
    extract_cjk_han_chars,
)
from backend.app.domains.agent.run_context import RunCatalog, RunContext

logger = logging.getLogger(__name__)

_WORKSPACE_OBSERVATION_TOOLS = frozenset({"read_file", "grep", "ls", "glob"})
_CHECKED_STRUCTURED_TOOLS = frozenset({"ask_user", "write_todos", "task"})
_MAX_EXISTING_FILE_SCAN_BYTES = 512_000


def _message_text(msg: object) -> str:
    content = getattr(msg, "content", None)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, Mapping):
                txt = item.get("text") or item.get("thinking")
                if isinstance(txt, str):
                    parts.append(txt)
        return "\n".join(parts)
    return ""


def _sync_allowance_from_messages(
    allowance: CjkAllowance,
    messages: Sequence[object],
) -> None:
    for msg in messages:
        if isinstance(msg, HumanMessage):
            allowance.absorb_user_text(_message_text(msg))
        elif (
            isinstance(msg, ToolMessage)
            and getattr(msg, "status", None) != "error"
            and str(getattr(msg, "name", "") or "") in _WORKSPACE_OBSERVATION_TOOLS
        ):
            allowance.absorb_workspace_text(_message_text(msg))


def _sanitize_ai_message(msg: AIMessage, allowance: CjkAllowance) -> AIMessage:
    if allowance.allow_all:
        return msg

    changed = False
    new_content: str | list[str | dict[str, object]] = msg.content  # type: ignore[assignment]
    if isinstance(msg.content, str):
        cleaned = allowance.strip_unallowed_cjk(msg.content)
        if cleaned != msg.content:
            new_content = cleaned
            changed = True
    elif isinstance(msg.content, list):
        updated_blocks: list[str | dict[str, object]] = []
        for block in msg.content:
            if isinstance(block, str):
                cleaned_str = allowance.strip_unallowed_cjk(block)
                if cleaned_str != block:
                    changed = True
                updated_blocks.append(cleaned_str)
            elif isinstance(block, Mapping):
                bcopy: dict[str, object] = dict(block)
                for key in ("text", "thinking"):
                    val = bcopy.get(key)
                    if isinstance(val, str):
                        cleaned_val = allowance.strip_unallowed_cjk(val)
                        if cleaned_val != val:
                            bcopy[key] = cleaned_val
                            changed = True
                updated_blocks.append(bcopy)
            else:
                updated_blocks.append(str(block))
        if changed:
            new_content = updated_blocks

    new_kwargs = dict(msg.additional_kwargs)
    reasoning = new_kwargs.get("reasoning_content")
    if isinstance(reasoning, str) and reasoning:
        cleaned_reasoning = allowance.strip_unallowed_cjk(reasoning)
        if cleaned_reasoning != reasoning:
            new_kwargs["reasoning_content"] = cleaned_reasoning
            changed = True

    if not changed:
        return msg
    return msg.model_copy(update={"content": new_content, "additional_kwargs": new_kwargs})


def _resolve_target_file_for_cjk_scan(catalog: RunCatalog, raw_virtual_path: str) -> Path | None:
    clean = "/" + raw_virtual_path.strip().lstrip("/")
    if clean.startswith("/artifacts/"):
        root = catalog.artifacts_dir.resolve()
        rel = clean.removeprefix("/artifacts/")
    elif clean.startswith("/memories/project/"):
        if catalog.project_memory_dir is None:
            return None
        root = catalog.project_memory_dir.resolve()
        rel = clean.removeprefix("/memories/project/")
    elif clean.startswith("/skills/"):
        return None
    else:
        root = catalog.workspace_path.resolve()
        rel = clean.lstrip("/")

    if not rel:
        return None
    candidate = (root / rel).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate if candidate.is_file() else None


def _read_existing_file_cjk(catalog: RunCatalog, raw_virtual_path: str) -> set[str]:
    target = _resolve_target_file_for_cjk_scan(catalog, raw_virtual_path)
    if target is None:
        return set()
    try:
        raw = target.read_bytes()[:_MAX_EXISTING_FILE_SCAN_BYTES]
    except OSError:
        return set()
    if b"\x00" in raw:
        return set()
    return extract_cjk_han_chars(raw.decode("utf-8", errors="ignore"))


def _collect_nested_strings(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        return "\n".join(_collect_nested_strings(v) for v in value.values())
    if isinstance(value, list | tuple):
        return "\n".join(_collect_nested_strings(v) for v in value)
    return ""


def _sample_preview(chars: list[str], limit: int = 8) -> str:
    preview = "".join(chars[:limit])
    return f"{preview}..." if len(chars) > limit else preview


class LanguageGuardMiddleware(AgentMiddleware):
    """Prevents unexpected CJK Han leakage in tool arguments and model message history."""

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        catalog = RunContext.current_or_none()
        allowance = (
            catalog.cjk_allowance
            if catalog is not None and catalog.cjk_allowance is not None
            else CjkAllowance()
        )
        _sync_allowance_from_messages(allowance, request.messages)

        if not allowance.allow_all:
            changed = False
            sanitized: list[BaseMessage] = []
            for msg in request.messages:
                if isinstance(msg, AIMessage):
                    cleaned = _sanitize_ai_message(msg, allowance)
                    if cleaned is not msg:
                        changed = True
                    sanitized.append(cleaned)
                else:
                    sanitized.append(msg)
            if changed:
                request = request.override(messages=sanitized)

        return await handler(request)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        catalog = RunContext.current_or_none()
        if catalog is None or catalog.cjk_allowance is None:
            return await handler(request)

        allowance = catalog.cjk_allowance
        state = getattr(request, "state", None)
        if isinstance(state, Mapping):
            raw_msgs = state.get("messages")
            if isinstance(raw_msgs, Sequence):
                _sync_allowance_from_messages(allowance, raw_msgs)

        tool_name = str(request.tool_call.get("name") or "")
        tool_call_id = str(request.tool_call.get("id") or "")
        raw_args = request.tool_call.get("args")
        args: Mapping[str, object] = raw_args if isinstance(raw_args, Mapping) else {}

        rejection = await self._validate_tool_args(
            catalog=catalog,
            allowance=allowance,
            tool_name=tool_name,
            args=args,
        )
        if rejection is not None:
            logger.info(
                "agent.language_guard_blocked",
                extra={
                    "event": "agent.language_guard_blocked",
                    "session_id": catalog.session_id,
                    "tool": tool_name,
                },
            )
            return ToolMessage(
                content=rejection,
                name=tool_name or "unknown",
                tool_call_id=tool_call_id,
                status="error",
            )

        try:
            result = await handler(request)
        except (GraphBubbleUp, NodeCancelledError, asyncio.CancelledError):
            raise

        if isinstance(result, ToolMessage):
            if (
                tool_name in _WORKSPACE_OBSERVATION_TOOLS
                and getattr(result, "status", None) != "error"
            ):
                allowance.absorb_workspace_text(_message_text(result))
            elif (
                tool_name == "task"
                and isinstance(result.content, str)
                and not allowance.allow_all
            ):
                cleaned_sub = allowance.strip_unallowed_cjk(result.content)
                if cleaned_sub != result.content:
                    return result.model_copy(update={"content": cleaned_sub})
        return result

    async def _validate_tool_args(
        self,
        *,
        catalog: RunCatalog,
        allowance: CjkAllowance,
        tool_name: str,
        args: Mapping[str, object],
    ) -> str | None:
        if tool_name == "grep":
            pattern = str(args.get("pattern") or "")
            if broken_grep := detect_broken_grep_pattern(pattern):
                return broken_grep
            bad = allowance.find_unallowed_chars(pattern)
            if bad:
                sample = _sample_preview(bad)
                return (
                    f"Tool `grep` rejected: pattern contains unexpected Chinese (CJK Han) "
                    f"characters (`{sample}`) not present in the user prompt or inspected "
                    "workspace files. Re-run `grep` using exact English code symbols/identifiers "
                    "or Korean literals."
                )
            return None

        if tool_name == "execute":
            command = str(args.get("command") or "")
            if broken_shell := detect_broken_shell_cjk_regex(command):
                return broken_shell
            bad = allowance.find_unallowed_chars(command)
            if bad:
                sample = _sample_preview(bad)
                return (
                    f"Tool `execute` rejected: shell command contains unexpected Chinese (CJK Han) "
                    f"characters (`{sample}`). Rewrite the command strictly in English/ASCII "
                    "(or Korean only where required)."
                )
            return None

        if tool_name in {"write_file", "edit_file"}:
            file_path = str(args.get("file_path") or "")
            payload_field = "content" if tool_name == "write_file" else "new_string"
            text_to_write = str(args.get(payload_field) or "")
            if not text_to_write or allowance.allow_all:
                return None
            existing_cjk = await asyncio.to_thread(_read_existing_file_cjk, catalog, file_path)
            bad = allowance.find_unallowed_chars(text_to_write, extra_allowed=existing_cjk)
            if bad:
                sample = _sample_preview(bad)
                return (
                    f"Tool `{tool_name}` rejected for `{file_path}`: `{payload_field}` contains "
                    f"unexpected Chinese (CJK Han) characters (`{sample}`). Rewrite strictly in "
                    "English (for code, identifiers, and technical comments) or Korean (`한국어`, "
                    "for user-facing copy and markdown artifacts) and retry."
                )
            return None

        if tool_name in _CHECKED_STRUCTURED_TOOLS:
            combined = _collect_nested_strings(args)
            bad = allowance.find_unallowed_chars(combined)
            if bad:
                sample = _sample_preview(bad)
                return (
                    f"Tool `{tool_name}` rejected: arguments contain unexpected Chinese (CJK Han) "
                    f"characters (`{sample}`). Rewrite strictly in Korean (`한국어`) and English "
                    "and retry."
                )
            return None

        return None
