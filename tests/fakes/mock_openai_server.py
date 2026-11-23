"""Real HTTP OpenAI-compatible SSE server for wire-level E2E tests and live browser verification."""

from __future__ import annotations

import argparse
import asyncio
import json
import socket
import threading
import time
import uuid
from collections import deque
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route


@dataclass
class WireTurn:
    """Single OpenAI chat completion response turn (streamed or non-streamed)."""

    reasoning: str = ""
    content: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)


class MockOpenAIState:
    """Holds queued scripted turns, recorded requests, and smart fallback behavior."""

    def __init__(self, turns: list[WireTurn] | None = None) -> None:
        self.turns: deque[WireTurn] = deque(turns or [])
        self.requests: list[dict[str, Any]] = []

    def next_turn(self, payload: dict[str, Any]) -> WireTurn:
        if self.turns:
            return self.turns.popleft()
        return _smart_demo_turn(payload)


def _smart_demo_turn(payload: dict[str, Any]) -> WireTurn:
    """Stateful realistic agent turn generator for interactive browser E2E flows."""
    messages: list[dict[str, Any]] = payload.get("messages") or []
    tools: list[dict[str, Any]] = payload.get("tools") or []
    tool_names = {
        str(t.get("function", {}).get("name") or "")
        for t in tools
        if isinstance(t, dict)
    }
    has_execute = "execute" in tool_names

    # Inspect conversation tail since the most recent user message
    last_user_idx = -1
    for idx in range(len(messages) - 1, -1, -1):
        if messages[idx].get("role") == "user":
            last_user_idx = idx
            break

    last_user_text = ""
    if last_user_idx >= 0:
        raw = messages[last_user_idx].get("content")
        last_user_text = raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False)

    tail = messages[last_user_idx + 1 :] if last_user_idx >= 0 else messages
    called_tools = [
        tc.get("function", {}).get("name")
        for m in tail
        if m.get("role") == "assistant"
        for tc in (m.get("tool_calls") or [])
    ]

    # Case 1: Read-only Ask mode (no `execute` tool) -> Code Onboarding flow
    if not has_execute:
        if "ls" not in called_tools:
            return WireTurn(
                reasoning="먼저 루트 디렉터리 구조를 스캔해 핵심 모듈 경계를 파악합니다.",
                tool_calls=[{"name": "ls", "args": {"path": "/"}}],
            )
        if "write_file" not in called_tools:
            return WireTurn(
                reasoning=(
                    "파악한 구조와 의존성을 바탕으로 "
                    "온보딩 walkthrough 아티팩트를 작성합니다."
                ),
                tool_calls=[
                    {
                        "name": "write_file",
                        "args": {
                            "file_path": "/artifacts/walkthrough.md",
                            "content": (
                                "# 코드베이스 온보딩 가이드\n\n"
                                "## 1. 프로젝트 아키텍처\n"
                                "- **DDD Bounded Contexts**: `sessions`, `agent`, `artifacts`\n"
                                "- **Backend**: FastAPI + DeepAgents + SQLite 체크포인터\n"
                                "- **Frontend**: React 19 + Astryx 디자인 시스템\n"
                            ),
                        },
                    }
                ],
            )
        return WireTurn(
            reasoning="온보딩 문서 작성이 완료되어 핵심 요약을 전달합니다.",
            content=(
                "프로젝트 구조와 핵심 의존성 분석을 완료하고 "
                "**Walkthrough** 온보딩 문서를 생성했습니다.\n\n"
                "- 우측 **Artifacts** 패널에서 전체 문서를 확인하거나 "
                "`docs/ONBOARDING.md`로 저장할 수 있습니다."
            ),
        )

    # Case 2: Plan mode AFTER user clicked Proceed ("Plan approved...")
    if "Plan approved." in last_user_text:
        if "write_todos" not in called_tools:
            return WireTurn(
                reasoning="승인된 구현 계획에 따라 작업 체크리스트를 갱신합니다.",
                tool_calls=[
                    {
                        "name": "write_todos",
                        "args": {
                            "todos": [
                                {
                                    "content": "Create onboarding_note.py module",
                                    "status": "completed",
                                },
                                {
                                    "content": "Run verification shell check",
                                    "status": "in_progress",
                                },
                            ]
                        },
                    },
                    {
                        "name": "write_file",
                        "args": {
                            "file_path": "/onboarding_note.py",
                            "content": '"""Generated note module."""\n\nSTATUS = "verified"\n',
                        },
                    },
                ],
            )
        if "execute" not in called_tools:
            return WireTurn(
                reasoning="변경 사항 검증을 위해 셸 명령 실행 승인을 요청합니다.",
                tool_calls=[
                    {
                        "name": "execute",
                        "args": {"command": "echo verification-passed"},
                    }
                ],
            )
        if called_tools.count("write_file") < 2:
            return WireTurn(
                reasoning="검증 결과를 walkthrough 아티팩트에 기록합니다.",
                tool_calls=[
                    {
                        "name": "write_file",
                        "args": {
                            "file_path": "/artifacts/walkthrough.md",
                            "content": (
                                "# Implementation Walkthrough\n\n"
                                "- Added `onboarding_note.py` (`STATUS = \"verified\"`)\n"
                                "- Verified via `echo verification-passed`\n"
                            ),
                        },
                    }
                ],
            )
        return WireTurn(
            reasoning="모든 구현 및 검증 단계가 완료되었습니다.",
            content=(
                "승인된 계획을 구현하고 검증까지 완료했습니다. "
                "**Diff** 및 **Walkthrough** 탭에서 변경 내역을 확인하세요."
            ),
        )

    # Case 3: Plan revision requested
    if "Plan revision requested:" in last_user_text:
        if "write_file" not in called_tools:
            return WireTurn(
                reasoning="피드백을 반영하여 implementation_plan.md를 업데이트합니다.",
                tool_calls=[
                    {
                        "name": "write_file",
                        "args": {
                            "file_path": "/artifacts/implementation_plan.md",
                            "content": (
                                "# Implementation Plan (Revised)\n\n"
                                "1. Add `onboarding_note.py` with `STATUS = \"verified\"`.\n"
                                "2. Run `echo verification-passed` to confirm.\n"
                            ),
                        },
                    }
                ],
            )
        return WireTurn(content="요청하신 피드백을 반영해 구현 계획서를 수정했습니다.")

    # Case 4: Initial Plan / Auto prompt
    if "write_file" not in called_tools:
        return WireTurn(
            reasoning="작업 범위를 분석하고 구현 계획서(implementation_plan.md)를 작성합니다.",
            tool_calls=[
                {
                    "name": "write_file",
                    "args": {
                        "file_path": "/artifacts/implementation_plan.md",
                        "content": (
                            "# Implementation Plan\n\n"
                            "1. Create `onboarding_note.py` in workspace root.\n"
                            "2. Verify execution via `echo verification-passed`.\n"
                        ),
                    },
                }
            ],
        )
    return WireTurn(
        reasoning="구현 계획서 작성을 마쳤으므로 사용자 검토를 대기합니다.",
        content=(
            "구현 계획서(`implementation_plan.md`)를 작성했습니다. "
            "검토 후 **Proceed**를 눌러주세요."
        ),
    )


def create_mock_openai_app(state: MockOpenAIState | None = None) -> Starlette:
    server_state = state or MockOpenAIState()

    def _check_auth(request: Request) -> JSONResponse | None:
        auth = request.headers.get("authorization", "")
        if not auth.startswith("Bearer ") or not auth.removeprefix("Bearer ").strip():
            return JSONResponse(
                {"error": {"message": "Missing Bearer token", "type": "auth_error"}},
                status_code=401,
            )
        return None

    async def list_models(request: Request) -> JSONResponse:
        if err := _check_auth(request):
            return err
        return JSONResponse(
            {
                "object": "list",
                "data": [
                    {"id": "qwen3.8-flash", "object": "model", "owned_by": "local-mock"},
                ],
            }
        )

    async def chat_completions(request: Request) -> JSONResponse | StreamingResponse:
        if err := _check_auth(request):
            return err
        payload: dict[str, Any] = await request.json()
        server_state.requests.append(payload)
        model_id = str(payload.get("model") or "qwen3.8-flash")
        turn = server_state.next_turn(payload)
        completion_id = f"chatcmpl-{uuid.uuid4().hex[:10]}"
        created = int(time.time())

        if not payload.get("stream"):
            formatted_tcs = [
                {
                    "id": tc.get("id") or f"call_{uuid.uuid4().hex[:8]}",
                    "type": "function",
                    "function": {
                        "name": tc["name"],
                        "arguments": json.dumps(tc.get("args", {}), ensure_ascii=False),
                    },
                }
                for tc in turn.tool_calls
            ]
            msg_obj: dict[str, Any] = {
                "role": "assistant",
                "content": turn.content or None,
            }
            if turn.reasoning:
                msg_obj["reasoning_content"] = turn.reasoning
            if formatted_tcs:
                msg_obj["tool_calls"] = formatted_tcs
            return JSONResponse(
                {
                    "id": completion_id,
                    "object": "chat.completion",
                    "created": created,
                    "model": model_id,
                    "choices": [
                        {
                            "index": 0,
                            "message": msg_obj,
                            "finish_reason": "tool_calls" if formatted_tcs else "stop",
                        }
                    ],
                }
            )

        async def _sse_chunks() -> AsyncIterator[bytes]:
            def _frame(delta: dict[str, Any], finish: str | None = None) -> bytes:
                obj = {
                    "id": completion_id,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": model_id,
                    "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
                }
                return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n".encode()

            yield _frame({"role": "assistant", "content": ""})
            if turn.reasoning:
                mid = max(1, len(turn.reasoning) // 2)
                for part in (turn.reasoning[:mid], turn.reasoning[mid:]):
                    if part:
                        yield _frame({"reasoning_content": part})
                        await asyncio.sleep(0.005)

            for idx, tc in enumerate(turn.tool_calls):
                tc_id = str(tc.get("id") or f"call_{uuid.uuid4().hex[:8]}")
                args_json = json.dumps(tc.get("args", {}), ensure_ascii=False)
                half = max(1, len(args_json) // 2)
                yield _frame(
                    {
                        "tool_calls": [
                            {
                                "index": idx,
                                "id": tc_id,
                                "type": "function",
                                "function": {"name": tc["name"], "arguments": args_json[:half]},
                            }
                        ]
                    }
                )
                if args_json[half:]:
                    yield _frame(
                        {
                            "tool_calls": [
                                {
                                    "index": idx,
                                    "function": {"arguments": args_json[half:]},
                                }
                            ]
                        }
                    )

            if turn.content:
                mid = max(1, len(turn.content) // 2)
                for part in (turn.content[:mid], turn.content[mid:]):
                    if part:
                        yield _frame({"content": part})
                        await asyncio.sleep(0.005)

            yield _frame({}, finish="tool_calls" if turn.tool_calls else "stop")
            yield b"data: [DONE]\n\n"

        return StreamingResponse(_sse_chunks(), media_type="text/event-stream")

    app = Starlette(
        routes=[
            Route("/v1/models", list_models, methods=["GET"]),
            Route("/v1/chat/completions", chat_completions, methods=["POST"]),
        ]
    )
    app.state.mock_openai = server_state
    return app


@contextmanager
def run_mock_openai_server(
    turns: list[WireTurn] | None = None,
) -> Iterator[tuple[str, MockOpenAIState]]:
    """Start the mock OpenAI server on an ephemeral localhost port inside a background thread."""
    state = MockOpenAIState(turns=turns)
    app = create_mock_openai_app(state)

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]

    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)

    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5.0
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.02)

    try:
        yield f"http://127.0.0.1:{port}/v1", state
    finally:
        server.should_exit = True
        thread.join(timeout=5.0)
        sock.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run local OpenAI-compatible mock server.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8099)
    args = parser.parse_args()
    uvicorn.run(create_mock_openai_app(), host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
