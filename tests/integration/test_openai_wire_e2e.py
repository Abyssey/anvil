"""Wire-level E2E test exercising create_app() + ReasoningChatOpenAI over real HTTP SSE."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.app.shared.config import AppConfig
from tests.fakes.mock_openai_server import WireTurn, run_mock_openai_server


def _parse_sse(raw_text: str) -> list[tuple[str, dict[str, object]]]:
    events: list[tuple[str, dict[str, object]]] = []
    current_event = "message"
    data_lines: list[str] = []
    for line in raw_text.splitlines():
        if line.startswith("event:"):
            current_event = line.removeprefix("event:").strip()
        elif line.startswith("data:"):
            data_lines.append(line.removeprefix("data:").strip())
        elif line == "" and data_lines:
            events.append((current_event, json.loads("\n".join(data_lines))))
            current_event = "message"
            data_lines = []
    if data_lines:
        events.append((current_event, json.loads("\n".join(data_lines))))
    return events


def _init_git_workspace(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "wire@example.com"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Wire Tester"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    (repo / "service.py").write_text("def version() -> str:\n    return 'v1'\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)


def test_real_openai_wire_sse_onboarding_and_plan_hitl(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    _init_git_workspace(workspace)

    turns = [
        # Ask mode onboarding turn 1: read_file
        WireTurn(
            reasoning="Reading service.py over real OpenAI SSE.",
            tool_calls=[{"name": "read_file", "args": {"file_path": "/service.py"}}],
        ),
        # Ask mode onboarding turn 2: write walkthrough + summarize
        WireTurn(
            reasoning="Writing onboarding walkthrough.",
            tool_calls=[
                {
                    "name": "write_file",
                    "args": {
                        "file_path": "/artifacts/walkthrough.md",
                        "content": "# Wire Onboarding\n`service.py` exposes `version()`.",
                    },
                }
            ],
        ),
        WireTurn(
            reasoning="Finalizing response.",
            content="온보딩 요약: `service.py`가 핵심 엔트리입니다.",
        ),
        # Plan mode turn 1: write implementation_plan.md
        WireTurn(
            reasoning="Drafting implementation plan.",
            tool_calls=[
                {
                    "name": "write_file",
                    "args": {
                        "file_path": "/artifacts/implementation_plan.md",
                        "content": "# Plan\nBump version() to v2 and run echo check.",
                    },
                }
            ],
        ),
        WireTurn(content="Plan ready for review."),
        # Plan mode after approve: edit file + execute shell command
        WireTurn(
            reasoning="Applying approved edit.",
            tool_calls=[
                {
                    "name": "edit_file",
                    "args": {
                        "file_path": "/service.py",
                        "old_string": "return 'v1'",
                        "new_string": "return 'v2'",
                    },
                },
                {
                    "name": "execute",
                    "args": {"command": "echo wire-verified"},
                },
            ],
        ),
        WireTurn(content="Upgrade to v2 verified."),
    ]

    with run_mock_openai_server(turns=turns) as (base_url, mock_state):
        # Intentionally pass trailing /chat/completions to verify full-stack URL normalization!
        headers = {
            "X-LLM-Base-Url": f"{base_url}/chat/completions",
            "X-LLM-Model": "qwen3.8-flash",
            "X-LLM-Api-Key": "sk-wire-secret-token-777",
            "X-LLM-Enable-Thinking": "true",
        }
        cfg = AppConfig.for_data_dir(tmp_path / ".abyss-data", default_workspace_path=workspace)
        # Unmodified create_app() using production ModelFactory + ReasoningChatOpenAI!
        app = create_app(config=cfg)

        with TestClient(app) as client:
            ping = client.post("/api/workspace/llm-ping", headers=headers)
            assert ping.status_code == 200
            assert ping.json()["ok"] is True
            assert ping.json()["base_url"] == base_url

            # 1. Ask mode Code Onboarding over real HTTP SSE
            ask_sid = client.post(
                "/api/sessions",
                json={"workspace_path": str(workspace), "mode": "ask"},
            ).json()["id"]
            ask_resp = client.post(
                f"/api/sessions/{ask_sid}/messages/stream",
                json={"content": "이 레포 구조를 온보딩 문서로 정리해줘", "mode": "ask"},
                headers=headers,
            )
            assert ask_resp.status_code == 200
            ask_events = _parse_sse(ask_resp.text)
            ask_names = [n for n, _ in ask_events]
            assert "thinking" in ask_names
            assert "tool_start" in ask_names
            assert "tool_end" in ask_names
            assert "artifact_updated" in ask_names
            assert "token" in ask_names
            assert "done" in ask_names

            # Verify reasoning_content survived OpenAI SDK -> ReasoningChatOpenAI -> SQLite
            detail = client.get(f"/api/sessions/{ask_sid}").json()
            assistant_msgs = [m for m in detail["messages"] if m["role"] == "assistant"]
            assert len(assistant_msgs) == 1
            assert "Reading service.py over real OpenAI SSE." in assistant_msgs[0]["thinking"]
            assert "온보딩 요약" in assistant_msgs[0]["content"]

            # Verify extra_body={"enable_thinking": True} reached the wire
            assert mock_state.requests[0].get("enable_thinking") is True

            # 2. Plan mode HITL + Shell Approval over real HTTP SSE
            plan_sid = client.post(
                "/api/sessions",
                json={"workspace_path": str(workspace), "mode": "plan"},
            ).json()["id"]
            p1 = client.post(
                f"/api/sessions/{plan_sid}/messages/stream",
                json={"content": "Bump version to v2", "mode": "plan"},
                headers=headers,
            )
            assert any(
                n == "interrupt" and d.get("kind") == "plan_review"
                for n, d in _parse_sse(p1.text)
            )

            p2 = client.post(
                f"/api/sessions/{plan_sid}/resume",
                json={"decision": "approve"},
                headers=headers,
            )
            assert any(
                n == "interrupt" and d.get("kind") == "shell_approval"
                for n, d in _parse_sse(p2.text)
            )

            p3 = client.post(
                f"/api/sessions/{plan_sid}/resume",
                json={"decision": "approve"},
                headers=headers,
            )
            p3_names = [n for n, _ in _parse_sse(p3.text)]
            assert "diff_changed" in p3_names
            assert "done" in p3_names
            assert "return 'v2'" in (workspace / "service.py").read_text(encoding="utf-8")
