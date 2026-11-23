"""Integration tests for turn-level checkpoint revert, LangGraph re-hydration, and git revert."""

from __future__ import annotations

import json
import subprocess
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
    (repo / "AGENTS.md").write_text("Keep boundaries clean.\n", encoding="utf-8")
    (repo / "app.py").write_text("def greet() -> str:\n    return 'hi'\n", encoding="utf-8")
    (repo / "test_app.py").write_text(
        "from app import greet\n\ndef test_greet() -> None:\n    assert greet() == 'v1'\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=repo, check=True, capture_output=True)


def _headers() -> dict[str, str]:
    return {
        "X-LLM-Base-Url": "http://localhost:8001/v1",
        "X-LLM-Model": "qwen3.8-flash",
        "X-LLM-Api-Key": "sk-revert-test-key",
        "X-LLM-Enable-Thinking": "true",
    }


def test_multi_turn_checkpoint_revert_preserves_prior_turn_and_rehydrates_langgraph(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "repo"
    _init_git_workspace(workspace)
    pytest_bin = str(Path(".venv/bin/pytest").resolve())

    model = ScriptedReasoningChatModel(
        turns=[
            # Turn 1: edit app.py -> 'v1', create helper.py, run pytest -> PASS
            ScriptedTurn(
                tool_calls=[
                    {
                        "name": "write_file",
                        "args": {
                            "file_path": "/app.py",
                            "content": "def greet() -> str:\n    return 'v1'\n",
                        },
                    },
                    {
                        "name": "write_file",
                        "args": {"file_path": "/helper.py", "content": "HELPER = 1\n"},
                    },
                ]
            ),
            ScriptedTurn(
                tool_calls=[
                    {
                        "name": "execute",
                        "args": {"command": f"{pytest_bin} -q test_app.py"},
                    }
                ]
            ),
            ScriptedTurn(content="Implemented v1 and verified with pytest."),
            # Turn 2: overwrite app.py -> 'v2_broken' and create nested/extra.py (unverified)
            ScriptedTurn(
                tool_calls=[
                    {
                        "name": "write_file",
                        "args": {
                            "file_path": "/app.py",
                            "content": "def greet() -> str:\n    return 'v2_broken'\n",
                        },
                    },
                    {
                        "name": "write_file",
                        "args": {"file_path": "/nested/extra.py", "content": "EXTRA = True\n"},
                    },
                ]
            ),
            ScriptedTurn(content="Applied experimental v2 refactor."),
            # Turn 3 (after reverting Turn 2): LLM should see Turn 1 history, NOT Turn 2
            ScriptedTurn(
                tool_calls=[
                    {
                        "name": "write_file",
                        "args": {"file_path": "/notes.txt", "content": "post-revert turn 3\n"},
                    }
                ]
            ),
            ScriptedTurn(content="Added notes cleanly on top of v1."),
        ]
    )

    cfg = AppConfig.for_data_dir(tmp_path / ".abyss-data", default_workspace_path=workspace)
    app = create_app(config=cfg, model_factory=_FakeModelFactory(model))

    with TestClient(app) as client:
        created = client.post(
            "/api/sessions",
            json={"workspace_path": str(workspace), "mode": "auto"},
        )
        assert created.status_code == 201
        session_id = created.json()["id"]

        # Run Turn 1
        r1 = client.post(
            f"/api/sessions/{session_id}/messages/stream",
            headers=_headers(),
            json={"content": "Update greet to v1 and run tests", "mode": "auto"},
        )
        assert r1.status_code == 200
        ev1 = dict(_parse_sse(r1.text))
        assert "done" in ev1

        # Run Turn 2
        r2 = client.post(
            f"/api/sessions/{session_id}/messages/stream",
            headers=_headers(),
            json={"content": "Try risky v2 refactor", "mode": "auto"},
        )
        assert r2.status_code == 200
        assert "v2_broken" in (workspace / "app.py").read_text(encoding="utf-8")
        assert (workspace / "nested" / "extra.py").is_file()

        detail_before = client.get(f"/api/sessions/{session_id}").json()
        msgs_before = detail_before["messages"]
        assert len(msgs_before) == 4  # u1, a1, u2, a2
        a1, a2 = msgs_before[1], msgs_before[3]
        assert a1["checkpoint_id"] is not None
        assert a1["changed_files"] == ["app.py", "helper.py"]
        assert a1["verification_stamp"] == "passed"
        assert a2["checkpoint_id"] is not None
        assert a2["changed_files"] == ["app.py", "nested/extra.py"]
        assert a2["verification_stamp"] == "none"

        # Revert Turn 2 ONLY -> must restore Turn 1's uncommitted 'v1' + keep helper.py!
        rev2 = client.post(
            f"/api/sessions/{session_id}/revert",
            json={"message_id": a2["id"]},
        )
        assert rev2.status_code == 200
        rev2_body = rev2.json()
        assert rev2_body["rewound_user_prompt"] == "Try risky v2 refactor"
        assert sorted(rev2_body["reverted_files"]) == ["app.py", "nested/extra.py"]
        assert len(rev2_body["messages"]) == 2
        assert "return 'v1'" in (workspace / "app.py").read_text(encoding="utf-8")
        assert (workspace / "helper.py").is_file()
        assert not (workspace / "nested").exists()

        # Send Turn 3 -> verify LangGraph thread was reset & re-hydrated with Turn 1 only
        call_count_before_t3 = len(model.recorded_calls)
        r3 = client.post(
            f"/api/sessions/{session_id}/messages/stream",
            headers=_headers(),
            json={"content": "Add notes on top of v1", "mode": "auto"},
        )
        assert r3.status_code == 200
        t3_prompt_messages = model.recorded_calls[call_count_before_t3]
        joined_history = "\n".join(str(m.content) for m in t3_prompt_messages)
        assert "Implemented v1 and verified with pytest." in joined_history
        assert "experimental v2 refactor" not in joined_history
        assert "Try risky v2 refactor" not in joined_history

        # Revert all the way back to Turn 1 -> reverts Turn 3 AND Turn 1 in reverse order
        rev1 = client.post(
            f"/api/sessions/{session_id}/revert",
            json={"message_id": a1["id"]},
        )
        assert rev1.status_code == 200
        rev1_body = rev1.json()
        assert rev1_body["messages"] == []
        assert rev1_body["rewound_user_prompt"] == "Update greet to v1 and run tests"
        assert "return 'hi'" in (workspace / "app.py").read_text(encoding="utf-8")
        assert not (workspace / "helper.py").exists()
        assert not (workspace / "notes.txt").exists()


def test_modifying_governance_check_file_triggers_hitl_and_git_diff_revert_works(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "repo"
    _init_git_workspace(workspace)

    model = ScriptedReasoningChatModel(
        turns=[
            ScriptedTurn(
                tool_calls=[
                    {
                        "name": "write_file",
                        "args": {
                            "file_path": "/AGENTS.md",
                            "content": "Weakened rules!\n",
                        },
                    }
                ]
            ),
            ScriptedTurn(content="Stopped as requested; kept AGENTS.md untouched."),
        ]
    )

    cfg = AppConfig.for_data_dir(tmp_path / ".abyss-data", default_workspace_path=workspace)
    app = create_app(config=cfg, model_factory=_FakeModelFactory(model))

    with TestClient(app) as client:
        created = client.post(
            "/api/sessions",
            json={"workspace_path": str(workspace), "mode": "auto"},
        )
        session_id = created.json()["id"]

        stream_resp = client.post(
            f"/api/sessions/{session_id}/messages/stream",
            headers=_headers(),
            json={"content": "Rewrite AGENTS.md", "mode": "auto"},
        )
        assert stream_resp.status_code == 200
        events = _parse_sse(stream_resp.text)
        interrupt_payloads = [data for name, data in events if name == "interrupt"]
        assert len(interrupt_payloads) == 1
        assert interrupt_payloads[0]["kind"] == "shell_approval"
        assert "/AGENTS.md" in str(interrupt_payloads[0]["command"])
        assert (workspace / "AGENTS.md").read_text(encoding="utf-8") == "Keep boundaries clean.\n"

        # Deny self-modification of governance rules
        deny_resp = client.post(
            f"/api/sessions/{session_id}/resume",
            headers=_headers(),
            json={"decision": "deny"},
        )
        assert deny_resp.status_code == 200
        assert (workspace / "AGENTS.md").read_text(encoding="utf-8") == "Keep boundaries clean.\n"

        # Exercise POST /api/workspace/diff/revert (single file + discard all)
        (workspace / "app.py").write_text(
            "def greet() -> str:\n    return 'dirty'\n",
            encoding="utf-8",
        )
        (workspace / "scratch.py").write_text("TEMP = 1\n", encoding="utf-8")
        (workspace / "test_app.py").unlink()

        diff_initial = client.get("/api/workspace/diff", params={"path": str(workspace)}).json()
        assert sorted(f["path"] for f in diff_initial["files"]) == [
            "app.py",
            "scratch.py",
            "test_app.py",
        ]

        rev_single = client.post(
            "/api/workspace/diff/revert",
            json={"path": str(workspace), "files": ["scratch.py"]},
        )
        assert rev_single.status_code == 200
        assert not (workspace / "scratch.py").exists()
        assert sorted(f["path"] for f in rev_single.json()["files"]) == ["app.py", "test_app.py"]

        rev_all = client.post(
            "/api/workspace/diff/revert",
            json={"path": str(workspace)},
        )
        assert rev_all.status_code == 200
        assert rev_all.json()["files"] == []
        assert "return 'hi'" in (workspace / "app.py").read_text(encoding="utf-8")
        assert (workspace / "test_app.py").is_file()
