"""Integration tests for FastAPI SSE streaming, Ask/Plan/Shell HITL, and SQLite reload."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Iterator
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.domains.agent.model_adapter import ModelFactory
from backend.app.main import create_app
from backend.app.shared.config import AppConfig
from backend.app.shared.llm_credentials import LLMCredentials
from tests.fakes.fake_chat_model import ScriptedReasoningChatModel, ScriptedTurn


class _FakeModelFactory(ModelFactory):
    def __init__(self, model: ScriptedReasoningChatModel) -> None:
        self._model = model

    def build(self, creds: LLMCredentials) -> ScriptedReasoningChatModel:
        return self._model


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
            payload = json.loads("\n".join(data_lines))
            events.append((current_event, payload))
            current_event = "message"
            data_lines = []
    if data_lines:
        events.append((current_event, json.loads("\n".join(data_lines))))
    return events


def _init_workspace(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Tester"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    (repo / "AGENTS.md").write_text("Repo convention: keep modules focused.", encoding="utf-8")
    (repo / "app.py").write_text("def greet() -> str:\n    return 'hi'\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=repo, check=True, capture_output=True)


def _headers(api_key: str = "sk-live-secret-key-007") -> dict[str, str]:
    return {
        "X-LLM-Base-Url": "http://localhost:8001/v1",
        "X-LLM-Model": "qwen3.8-flash",
        "X-LLM-Api-Key": api_key,
        "X-LLM-Enable-Thinking": "true",
    }


def _client(tmp_path: Path, model: ScriptedReasoningChatModel) -> Iterator[TestClient]:
    cfg = AppConfig.for_data_dir(tmp_path / ".abyss-data", default_workspace_path=tmp_path / "repo")
    app = create_app(config=cfg, model_factory=_FakeModelFactory(model))
    with TestClient(app) as client:
        yield client


def test_missing_llm_credentials_returns_428_and_keys_never_leak(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    _init_workspace(workspace)
    model = ScriptedReasoningChatModel(turns=[ScriptedTurn(content="ok")])
    cfg = AppConfig.for_data_dir(tmp_path / ".abyss-data", default_workspace_path=workspace)
    app = create_app(config=cfg, model_factory=_FakeModelFactory(model))

    with TestClient(app) as client:
        created = client.post(
            "/api/sessions",
            json={"workspace_path": str(workspace), "mode": "ask"},
        )
        assert created.status_code == 201
        session_id = created.json()["id"]

        resp = client.post(
            f"/api/sessions/{session_id}/messages/stream",
            json={"content": "Explain codebase", "mode": "ask"},
        )
        assert resp.status_code == 428
        assert resp.json()["error"]["code"] == "LLM_SETTINGS_REQUIRED"


def test_ask_mode_onboarding_stream_and_save_to_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    _init_workspace(workspace)
    secret_key = "sk-never-leak-this-key-999"
    model = ScriptedReasoningChatModel(
        turns=[
            ScriptedTurn(
                reasoning="Let me inspect app.py first.",
                tool_calls=[{"name": "read_file", "args": {"file_path": "/app.py"}}],
            ),
            ScriptedTurn(
                reasoning="Synthesizing onboarding doc.",
                tool_calls=[
                    {
                        "name": "write_file",
                        "args": {
                            "file_path": "/artifacts/walkthrough.md",
                            "content": "# Code Onboarding\n- Entrypoint: `app.py` (`greet`)",
                        },
                    }
                ],
            ),
            ScriptedTurn(
                content="This repo defines `greet()` in `app.py`.",
            ),
        ]
    )
    cfg = AppConfig.for_data_dir(tmp_path / ".abyss-data", default_workspace_path=workspace)
    app = create_app(config=cfg, model_factory=_FakeModelFactory(model))

    with TestClient(app) as client:
        sess = client.post(
            "/api/sessions",
            json={"workspace_path": str(workspace), "mode": "ask"},
        ).json()
        sid = sess["id"]

        stream_resp = client.post(
            f"/api/sessions/{sid}/messages/stream",
            json={"content": "이 프로젝트 구조와 의존성을 설명해줘", "mode": "ask"},
            headers=_headers(secret_key),
        )
        assert stream_resp.status_code == 200
        assert secret_key not in stream_resp.text

        events = _parse_sse(stream_resp.text)
        event_names = [name for name, _ in events]
        assert "thinking" in event_names
        assert "tool_start" in event_names
        assert "tool_end" in event_names
        assert "token" in event_names
        assert "artifact_updated" in event_names
        assert "done" in event_names

        # Check SubdirAgentsReadMiddleware injected root AGENTS.md during read_file
        tool_messages = [
            msg
            for call in model.recorded_calls
            for msg in call
            if getattr(msg, "name", "") == "read_file"
        ]
        assert any(
            "Repo convention: keep modules focused." in str(m.content) for m in tool_messages
        )

        artifacts = client.get(f"/api/sessions/{sid}/artifacts").json()["artifacts"]
        by_kind = {a["kind"]: a for a in artifacts}
        assert "walkthrough" in by_kind
        assert "app.py" in by_kind["walkthrough"]["content"]

        saved = client.post(
            f"/api/sessions/{sid}/artifacts/walkthrough/save",
            json={"relative_path": "docs/ONBOARDING.md", "workspace_path": str(workspace)},
        )
        assert saved.status_code == 200
        assert (workspace / "docs" / "ONBOARDING.md").is_file()

        escaped = client.post(
            f"/api/sessions/{sid}/artifacts/walkthrough/save",
            json={"relative_path": "../escape.md", "workspace_path": str(workspace)},
        )
        assert escaped.status_code == 403

        # Confirm secret key was never persisted into SQLite
        db_bytes = cfg.sqlite_path.read_bytes()
        assert secret_key.encode() not in db_bytes


def test_plan_mode_revise_approve_and_shell_hitl_and_sqlite_restart(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    _init_workspace(workspace)
    model = ScriptedReasoningChatModel(
        turns=[
            ScriptedTurn(
                tool_calls=[
                    {
                        "name": "write_file",
                        "args": {
                            "file_path": "/artifacts/implementation_plan.md",
                            "content": "# Plan v1\nEdit app.py",
                        },
                    }
                ]
            ),
            ScriptedTurn(content="Plan v1 written."),
            ScriptedTurn(
                tool_calls=[
                    {
                        "name": "edit_file",
                        "args": {
                            "file_path": "/artifacts/implementation_plan.md",
                            "old_string": "Edit app.py",
                            "new_string": "Edit app.py and run echo ok",
                        },
                    }
                ]
            ),
            ScriptedTurn(content="Plan v2 written."),
            ScriptedTurn(
                tool_calls=[
                    {
                        "name": "write_todos",
                        "args": {
                            "todos": [
                                {"content": "Update greet()", "status": "in_progress"},
                                {"content": "Run echo check", "status": "pending"},
                            ]
                        },
                    }
                ]
            ),
            ScriptedTurn(
                tool_calls=[
                    {
                        "name": "edit_file",
                        "args": {
                            "file_path": "/app.py",
                            "old_string": "return 'hi'",
                            "new_string": "return 'hello v2'",
                        },
                    }
                ]
            ),
            ScriptedTurn(
                tool_calls=[
                    {
                        "name": "execute",
                        "args": {"command": "echo verify-ok"},
                    }
                ]
            ),
            ScriptedTurn(
                tool_calls=[
                    {
                        "name": "write_file",
                        "args": {
                            "file_path": "/artifacts/walkthrough.md",
                            "content": "# Walkthrough\nUpdated greet() and verified echo check.",
                        },
                    }
                ]
            ),
            ScriptedTurn(content="All changes implemented and verified."),
        ]
    )
    cfg = AppConfig.for_data_dir(tmp_path / ".abyss-data", default_workspace_path=workspace)

    # First app lifecycle: create session, hit plan_review interrupt, revise, then restart server!
    app1 = create_app(config=cfg, model_factory=_FakeModelFactory(model))
    with TestClient(app1) as client1:
        sid = client1.post(
            "/api/sessions",
            json={"workspace_path": str(workspace), "mode": "plan"},
        ).json()["id"]

        run_resp = client1.post(
            f"/api/sessions/{sid}/messages/stream",
            json={"content": "Update greet() to return hello v2", "mode": "plan"},
            headers=_headers(),
        )
        events1 = _parse_sse(run_resp.text)
        assert any(name == "interrupt" and p["kind"] == "plan_review" for name, p in events1)

        rev_resp = client1.post(
            f"/api/sessions/{sid}/resume",
            json={"decision": "revise", "feedback": "include echo check"},
            headers=_headers(),
        )
        events2 = _parse_sse(rev_resp.text)
        assert any(name == "interrupt" and p["kind"] == "plan_review" for name, p in events2)

    # Second app lifecycle (simulates server restart against same SQLite DB)
    app2 = create_app(config=cfg, model_factory=_FakeModelFactory(model))
    with TestClient(app2) as client2:
        detail = client2.get(f"/api/sessions/{sid}").json()
        assert detail["session"]["status"] == "awaiting_approval"
        assert detail["session"]["pending_interrupt"]["kind"] == "plan_review"

        approve_plan = client2.post(
            f"/api/sessions/{sid}/resume",
            json={"decision": "approve"},
            headers=_headers(),
        )
        events3 = _parse_sse(approve_plan.text)
        assert any(
            name == "interrupt"
            and p["kind"] == "shell_approval"
            and p.get("command") == "echo verify-ok"
            for name, p in events3
        )

        approve_shell = client2.post(
            f"/api/sessions/{sid}/resume",
            json={"decision": "approve"},
            headers=_headers(),
        )
        events4 = _parse_sse(approve_shell.text)
        names4 = [name for name, _ in events4]
        assert "diff_changed" in names4
        assert "done" in names4
        assert "hello v2" in (workspace / "app.py").read_text(encoding="utf-8")

        artifacts_payload = client2.get(f"/api/sessions/{sid}/artifacts").json()
        kinds = {a["kind"]: a for a in artifacts_payload["artifacts"]}
        assert "task" in kinds
        assert "Update greet()" in kinds["task"]["content"]
        assert artifacts_payload["walkthrough_plan"] is not None
        assert len(artifacts_payload["walkthrough_plan"]["chunks"]) >= 1


def test_shell_deny_in_plan_mode_skips_execution(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    _init_workspace(workspace)
    sentinel = workspace / "should_not_exist.txt"
    model = ScriptedReasoningChatModel(
        turns=[
            ScriptedTurn(
                tool_calls=[
                    {
                        "name": "write_file",
                        "args": {
                            "file_path": "/artifacts/implementation_plan.md",
                            "content": "# Plan\nRun touch command",
                        },
                    }
                ]
            ),
            ScriptedTurn(content="Plan ready."),
            ScriptedTurn(
                tool_calls=[
                    {"name": "execute", "args": {"command": f"touch {sentinel.name}"}}
                ]
            ),
            ScriptedTurn(content="Understood, skipped shell command."),
        ]
    )
    cfg = AppConfig.for_data_dir(tmp_path / ".abyss-data", default_workspace_path=workspace)
    app = create_app(config=cfg, model_factory=_FakeModelFactory(model))
    with TestClient(app) as client:
        sid = client.post(
            "/api/sessions",
            json={"workspace_path": str(workspace), "mode": "plan"},
        ).json()["id"]

        client.post(
            f"/api/sessions/{sid}/messages/stream",
            json={"content": "Create file via shell", "mode": "plan"},
            headers=_headers(),
        )
        res1 = client.post(
            f"/api/sessions/{sid}/resume",
            json={"decision": "approve"},
            headers=_headers(),
        )
        assert "shell_approval" in res1.text

        res2 = client.post(
            f"/api/sessions/{sid}/resume",
            json={"decision": "deny"},
            headers=_headers(),
        )
        events = _parse_sse(res2.text)
        assert any(name == "done" for name, _ in events)
        assert not sentinel.exists()
