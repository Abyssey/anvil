"""Unit tests for Open SWE-inspired agent middlewares."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import pytest
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.errors import GraphInterrupt
from langgraph.prebuilt.tool_node import ToolCallRequest

from backend.app.domains.agent.middleware.model_retries import ModelRetryMiddleware
from backend.app.domains.agent.middleware.read_only_guard import ReadOnlyToolGuardMiddleware
from backend.app.domains.agent.middleware.run_prompt import RunPromptMiddleware
from backend.app.domains.agent.middleware.subdir_agents import SubdirAgentsReadMiddleware
from backend.app.domains.agent.middleware.tool_errors import ToolErrorMiddleware
from backend.app.domains.agent.run_context import RunCatalog, RunContext
from backend.app.domains.agent.shell_guard import ShellGuard


@tool
def ls(path: str = "/") -> str:
    """List directory."""
    return path


@tool
def write_file(file_path: str, content: str) -> str:
    """Write file."""
    return file_path


@tool
def execute(command: str) -> str:
    """Run command."""
    return command


@dataclass
class _StubRuntime:
    state: dict[str, object] = field(default_factory=dict)


def _dummy_catalog(workspace: Path) -> RunCatalog:
    return RunCatalog(
        session_id="sess-1",
        workspace_path=workspace,
        mode="ask",
        artifacts_dir=workspace / ".artifacts",
        shell_guard=ShellGuard(),
        secret_values=(),
    )


@pytest.mark.asyncio
async def test_run_prompt_middleware_orders_cache_friendly_sections(tmp_path: Path) -> None:
    middleware = RunPromptMiddleware(mode="ask")
    req = ModelRequest(
        model=None,  # type: ignore[arg-type]
        messages=[HumanMessage(content="Explain repo")],
        system_message=SystemMessage(content="Base instructions"),
        state={"messages": []},
    )
    captured: list[ModelRequest] = []

    async def handler(updated: ModelRequest) -> ModelResponse:
        captured.append(updated)
        return ModelResponse(result=[AIMessage(content="ok")])

    async with RunContext.activate(_dummy_catalog(tmp_path)):
        await middleware.awrap_model_call(req, handler)

    assert len(captured) == 1
    sys_msg = captured[0].system_message
    assert sys_msg is not None
    blocks = sys_msg.content if isinstance(sys_msg.content, list) else [{"text": sys_msg.content}]
    texts = [b["text"] if isinstance(b, dict) else str(b) for b in blocks]
    joined = "\n".join(texts)
    assert "Base instructions" in joined
    assert "Ask mode" in joined
    assert str(tmp_path) in joined


@pytest.mark.asyncio
async def test_prompt_and_subagents_enforce_english_control_and_korean_output(
    tmp_path: Path,
) -> None:
    from backend.app.domains.agent.subagents import build_subagents
    from tests.fakes.fake_chat_model import ScriptedReasoningChatModel

    for mode in ("ask", "plan", "auto"):
        middleware = RunPromptMiddleware(mode=mode)
        req = ModelRequest(
            model=None,  # type: ignore[arg-type]
            messages=[HumanMessage(content="Explain repo")],
            system_message=SystemMessage(content="Base"),
            state={"messages": []},
        )
        captured: list[ModelRequest] = []

        async def handler(
            updated: ModelRequest,
            bucket: list[ModelRequest] = captured,
        ) -> ModelResponse:
            bucket.append(updated)
            return ModelResponse(result=[AIMessage(content="ok")])

        catalog = RunCatalog(
            session_id="sess-lang",
            workspace_path=tmp_path,
            mode=mode,
            artifacts_dir=tmp_path / ".artifacts",
            shell_guard=ShellGuard(),
            secret_values=(),
        )
        async with RunContext.activate(catalog):
            await middleware.awrap_model_call(req, handler)

        sys_text = str(captured[0].system_message.content)  # type: ignore[union-attr]
        assert "Korean" in sys_text
        assert "한국어" in sys_text
        assert "/artifacts/" in sys_text

    subagents = build_subagents(mode="plan", model=ScriptedReasoningChatModel(turns=[]))
    assert len(subagents) == 2
    for spec in subagents:
        prompt = str(spec["system_prompt"])
        assert "Korean" in prompt
        assert "한국어" in prompt


@pytest.mark.asyncio
async def test_subdir_agents_middleware_injects_ancestor_agents_once(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("Root rule: use async.", encoding="utf-8")
    nested = tmp_path / "pkg"
    nested.mkdir()
    (nested / "AGENTS.md").write_text("Pkg rule: keep pure.", encoding="utf-8")
    (nested / "mod.py").write_text("x = 1\n", encoding="utf-8")

    middleware = SubdirAgentsReadMiddleware()
    req = ToolCallRequest(
        tool_call={"name": "read_file", "args": {"file_path": "/pkg/mod.py"}, "id": "tc-1"},
        tool=None,
        state={"messages": []},
        runtime=_StubRuntime(),  # type: ignore[arg-type]
    )

    async def handler(_req: ToolCallRequest) -> ToolMessage:
        return ToolMessage(content="1: x = 1", tool_call_id="tc-1", name="read_file")

    async with RunContext.activate(_dummy_catalog(tmp_path)):
        first = await middleware.awrap_tool_call(req, handler)
        second = await middleware.awrap_tool_call(req, handler)

    assert isinstance(first, ToolMessage)
    assert "Root rule: use async." in str(first.content)
    assert "Pkg rule: keep pure." in str(first.content)
    assert isinstance(second, ToolMessage)
    assert "Pkg rule: keep pure." not in str(second.content)


@pytest.mark.asyncio
async def test_tool_error_middleware_recovers_errors_but_reraises_interrupts() -> None:
    middleware = ToolErrorMiddleware()
    req = ToolCallRequest(
        tool_call={"name": "read_file", "args": {"file_path": "/missing.py"}, "id": "tc-err"},
        tool=None,
        state={"messages": []},
        runtime=_StubRuntime(),  # type: ignore[arg-type]
    )

    async def failing_handler(_req: ToolCallRequest) -> ToolMessage:
        raise RuntimeError("disk hiccup")

    recovered = await middleware.awrap_tool_call(req, failing_handler)
    assert isinstance(recovered, ToolMessage)
    payload = json.loads(str(recovered.content))
    assert payload["error_type"] == "RuntimeError"
    assert "disk hiccup" in payload["error"]

    async def interrupt_handler(_req: ToolCallRequest) -> ToolMessage:
        raise GraphInterrupt(())

    with pytest.raises(GraphInterrupt):
        await middleware.awrap_tool_call(req, interrupt_handler)

    async def cancel_handler(_req: ToolCallRequest) -> ToolMessage:
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await middleware.awrap_tool_call(req, cancel_handler)


@pytest.mark.asyncio
async def test_model_retry_middleware_retries_transient_errors() -> None:
    middleware = ModelRetryMiddleware(max_retries=2, base_delay_seconds=0.0)
    req = ModelRequest(
        model=None,  # type: ignore[arg-type]
        messages=[HumanMessage(content="hi")],
        state={"messages": []},
    )
    attempts = 0

    async def flaky_handler(_req: ToolCallRequest | ModelRequest) -> ModelResponse:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise httpx.ReadTimeout("timeout")
        return ModelResponse(result=[AIMessage(content="recovered")])

    resp = await middleware.awrap_model_call(req, flaky_handler)  # type: ignore[arg-type]
    assert attempts == 3
    assert resp.result[0].content == "recovered"


@pytest.mark.asyncio
async def test_read_only_guard_middleware_strips_and_blocks_mutations() -> None:
    middleware = ReadOnlyToolGuardMiddleware()
    req = ModelRequest(
        model=None,  # type: ignore[arg-type]
        messages=[HumanMessage(content="explore")],
        tools=[ls, write_file, execute],
        state={"messages": []},
    )
    seen_tools: list[str] = []

    async def model_handler(updated: ModelRequest) -> ModelResponse:
        seen_tools.extend(getattr(t, "name", str(t)) for t in updated.tools)
        return ModelResponse(result=[AIMessage(content="ok")])

    await middleware.awrap_model_call(req, model_handler)
    assert seen_tools == ["ls"]

    bad_call = ToolCallRequest(
        tool_call={
            "name": "write_file",
            "args": {"file_path": "/a.py", "content": "x"},
            "id": "tc-w",
        },
        tool=write_file,
        state={"messages": []},
        runtime=_StubRuntime(),  # type: ignore[arg-type]
    )

    async def never_called(_req: ToolCallRequest) -> ToolMessage:
        raise AssertionError("Should not execute mutating tool")

    blocked = await middleware.awrap_tool_call(bad_call, never_called)
    assert isinstance(blocked, ToolMessage)
    assert blocked.status == "error"
    assert "read-only" in str(blocked.content).lower()
