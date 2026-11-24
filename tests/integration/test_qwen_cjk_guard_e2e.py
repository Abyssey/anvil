"""Wire-level E2E tests for Qwen 3.8 Flash CJK Han guardrails and workspace mkdir API."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.domains.agent.cjk_guard import extract_cjk_han_chars
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
        ["git", "config", "user.email", "cjk@example.com"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "CJK Verifier"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    (repo / "service.py").write_text("def version() -> str:\n    return 'v1'\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)


def test_qwen_wire_sse_blocks_cjk_grep_edit_and_sed_and_sanitizes_stream_and_history(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "repo"
    _init_git_workspace(workspace)

    turns = [
        # Step 1: Qwen leaks Simplified Chinese in reasoning, content, grep, edit_file, and sed
        WireTurn(
            reasoning="好的，我们先搜索 配置文件 并检查 service.py。",
            content="먼저 配置文件을 확인하겠습니다。",
            tool_calls=[
                {"name": "grep", "args": {"pattern": "配置文件"}},
                {
                    "name": "edit_file",
                    "args": {
                        "file_path": "/service.py",
                        "old_string": "return 'v1'",
                        "new_string": "return '默认 v2'",
                    },
                },
                {
                    "name": "execute",
                    "args": {"command": r"sed -i 's/[\u4e00-\u9fff]//g' service.py"},
                },
            ],
        ),
        # Step 2: After pre-execution rejection hints, model retries cleanly in English
        WireTurn(
            reasoning="Retrying search and edit in English.",
            tool_calls=[
                {"name": "grep", "args": {"pattern": "def version"}},
                {
                    "name": "edit_file",
                    "args": {
                        "file_path": "/service.py",
                        "old_string": "return 'v1'",
                        "new_string": "return 'v2'",
                    },
                },
            ],
        ),
        # Step 3: Final summary with one trailing Chinese leak that stream sanitizer strips
        WireTurn(
            reasoning="Verification complete，代码正常。",
            content="버전을 `v2`로 업데이트했습니다，请确认。",
        ),
    ]

    with run_mock_openai_server(turns=turns) as (base_url, mock_state):
        headers = {
            "X-LLM-Base-Url": base_url,
            "X-LLM-Model": "qwen3.8-flash",
            "X-LLM-Api-Key": "sk-cjk-wire-key-123",
            "X-LLM-Enable-Thinking": "true",
        }
        cfg = AppConfig.for_data_dir(tmp_path / ".data", default_workspace_path=workspace)
        app = create_app(config=cfg)

        with TestClient(app) as client:
            sid = client.post(
                "/api/sessions",
                json={"workspace_path": str(workspace), "mode": "auto"},
            ).json()["id"]

            resp = client.post(
                f"/api/sessions/{sid}/messages/stream",
                json={"content": "service.py의 버전을 v2로 올려줘", "mode": "auto"},
                headers=headers,
            )
            assert resp.status_code == 200
            events = _parse_sse(resp.text)

            # 1. Sampling stabilization and anti-CJK guidance reached the wire
            req0 = mock_state.requests[0]
            assert req0.get("temperature") == 0.2
            assert req0.get("top_p") == 0.85
            assert req0.get("enable_thinking") is True
            sys_text = json.dumps(req0.get("messages", []), ensure_ascii=False)
            assert "NEVER output Chinese characters" in sys_text

            # 2. Step 2 wire request history had prior assistant Chinese stripped
            req1 = mock_state.requests[1]
            req1_msgs = req1.get("messages")
            assert isinstance(req1_msgs, list)
            prior_assistant_contents = [
                str(m.get("content") or "")
                for m in req1_msgs
                if isinstance(m, dict) and m.get("role") == "assistant"
            ]
            assert prior_assistant_contents
            for content_str in prior_assistant_contents:
                assert extract_cjk_han_chars(content_str) == set()

            # 3. All 3 bad tool calls in Step 1 were rejected with status='error'
            tool_ends = [d for n, d in events if n == "tool_end"]
            error_tools = [str(d["name"]) for d in tool_ends if d.get("status") == "error"]
            assert len(error_tools) == 3
            assert set(error_tools) == {"grep", "edit_file", "execute"}

            # 4. Workspace file was updated cleanly to v2 without sed or CJK corruption
            assert (workspace / "service.py").read_text(encoding="utf-8") == (
                "def version() -> str:\n    return 'v2'\n"
            )

            # 5. Live SSE thinking & token chunks contain zero CJK Han characters
            streamed_tokens = "".join(str(d.get("text") or "") for n, d in events if n == "token")
            streamed_thinking = "".join(
                str(d.get("text") or "") for n, d in events if n == "thinking"
            )
            assert extract_cjk_han_chars(streamed_tokens) == set()
            assert extract_cjk_han_chars(streamed_thinking) == set()
            assert "버전을 `v2`로 업데이트했습니다." in streamed_tokens

            # 6. Persisted SQLite transcript is 100% free of CJK Han characters
            detail = client.get(f"/api/sessions/{sid}").json()
            assistant_msgs = [m for m in detail["messages"] if m["role"] == "assistant"]
            assert len(assistant_msgs) == 1
            assert extract_cjk_han_chars(assistant_msgs[0]["content"]) == set()
            assert extract_cjk_han_chars(assistant_msgs[0]["thinking"]) == set()


def test_workspace_mkdir_endpoint_creates_folder_and_registers_project(tmp_path: Path) -> None:
    parent_dir = tmp_path / "projects"
    parent_dir.mkdir(parents=True)
    default_ws = parent_dir / "existing"
    _init_git_workspace(default_ws)

    cfg = AppConfig.for_data_dir(tmp_path / ".data", default_workspace_path=default_ws)
    app = create_app(config=cfg)

    with TestClient(app) as client:
        mkdir_resp = client.post(
            "/api/workspace/mkdir",
            json={"parent_path": str(parent_dir), "name": "brand-new-service"},
        )
        assert mkdir_resp.status_code == 201
        created_info = mkdir_resp.json()
        expected_path = str((parent_dir / "brand-new-service").resolve())
        assert created_info["path"] == expected_path
        assert (parent_dir / "brand-new-service").is_dir()

        proj_resp = client.post("/api/projects", json={"path": expected_path})
        assert proj_resp.status_code == 201
        assert proj_resp.json()["path"] == expected_path

        # Reject traversal via HTTP 400
        bad_resp = client.post(
            "/api/workspace/mkdir",
            json={"parent_path": str(parent_dir), "name": "../outside"},
        )
        assert bad_resp.status_code == 400
