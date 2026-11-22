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


async def test_stream_cancellation_recovers_session_status_and_saves_partial_message(
    tmp_path: Path,
) -> None:
    import asyncio
    from collections.abc import AsyncIterator

    import pytest

    from backend.app.domains.agent.harness import DeepAgentHarness, HarnessEvent
    from backend.app.domains.agent.workspace import WorkspaceInspector
    from backend.app.domains.artifacts.store import ArtifactStore
    from backend.app.domains.sessions.entities import RunMode, SessionStatus
    from backend.app.domains.sessions.repository import SQLiteSessionRepository
    from backend.app.domains.sessions.service import SessionService

    workspace = tmp_path / "repo"
    _init_workspace(workspace)
    cfg = AppConfig.for_data_dir(tmp_path / ".abyss-data", default_workspace_path=workspace)
    cfg.ensure_directories()

    repo = SQLiteSessionRepository(cfg.sqlite_path)
    await repo.initialize()
    store = ArtifactStore(cfg.artifacts_root)
    inspector = WorkspaceInspector()

    class _CancellingHarness(DeepAgentHarness):
        async def stream_run(  # type: ignore[override]
            self,
            *,
            session_id: str,
            workspace_path: Path,
            mode: str,
            user_message: str,
            creds: LLMCredentials,
        ) -> AsyncIterator[HarnessEvent]:
            yield HarnessEvent("thinking", {"text": "Inspecting tree..."})
            yield HarnessEvent(
                "tool_start",
                {"id": "tc-1", "name": "ls", "args": {"path": "/"}},
            )
            yield HarnessEvent("token", {"text": "Partial answer before stop."})
            raise asyncio.CancelledError()

    service = SessionService(
        config=cfg,
        repo=repo,
        artifact_store=store,
        workspace_inspector=inspector,
        harness=_CancellingHarness(config=cfg, artifact_store=store),
    )
    created = await service.create_session(workspace_path=str(workspace), mode=RunMode.ASK)
    creds = LLMCredentials(
        base_url="http://localhost:8001/v1",
        model="qwen3.8-flash",
        api_key="sk-test-123456",
    )

    received: list[str] = []
    with pytest.raises(asyncio.CancelledError):
        async for ev in service.stream_message(
            session_id=created.id,
            content="Explain project",
            mode=RunMode.ASK,
            creds=creds,
        ):
            received.append(ev.event)

    assert received == ["thinking", "tool_start", "token"]
    reloaded, messages = await service.get_session_detail(created.id)
    assert reloaded.status == SessionStatus.IDLE
    assert reloaded.pending_interrupt is None
    assert len(messages) == 2
    assert messages[1].role == "assistant"
    assert messages[1].content == "Partial answer before stop."
    assert messages[1].thinking == "Inspecting tree..."
    assert messages[1].tool_calls[0].status == "cancelled"


def test_plan_deny_cancels_cleanly_and_delete_session_removes_artifacts(
    tmp_path: Path,
) -> None:
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
                            "content": "# Plan\nDraft plan",
                        },
                    }
                ]
            ),
            ScriptedTurn(content="Plan ready."),
        ]
    )
    cfg = AppConfig.for_data_dir(tmp_path / ".abyss-data", default_workspace_path=workspace)
    app = create_app(config=cfg, model_factory=_FakeModelFactory(model))
    with TestClient(app) as client:
        sid = client.post(
            "/api/sessions",
            json={"workspace_path": str(workspace), "mode": "plan"},
        ).json()["id"]

        r1 = client.post(
            f"/api/sessions/{sid}/messages/stream",
            json={"content": "Draft plan", "mode": "plan"},
            headers=_headers(),
        )
        assert any(
            name == "interrupt" and p["kind"] == "plan_review"
            for name, p in _parse_sse(r1.text)
        )
        assert (cfg.artifacts_root / sid / "implementation_plan.md").is_file()

        # Denying plan_review must cancel cleanly without calling the LLM again or re-interrupting
        r2 = client.post(
            f"/api/sessions/{sid}/resume",
            json={"decision": "deny"},
            headers=_headers(),
        )
        events2 = _parse_sse(r2.text)
        assert not any(name == "interrupt" for name, _ in events2)
        assert any(name == "done" for name, _ in events2)

        detail = client.get(f"/api/sessions/{sid}").json()
        assert detail["session"]["status"] == "idle"
        assert detail["session"]["pending_interrupt"] is None

        # Deleting the session must also delete its artifact directory on disk
        del_resp = client.delete(f"/api/sessions/{sid}")
        assert del_resp.status_code == 204
        assert not (cfg.artifacts_root / sid).exists()


def test_projects_crud_browse_legacy_migration_and_cascade_cleanup(tmp_path: Path) -> None:
    import sqlite3

    repo_a = tmp_path / "repo_a"
    repo_b = tmp_path / "repo_b"
    _init_workspace(repo_a)
    _init_workspace(repo_b)

    cfg = AppConfig.for_data_dir(tmp_path / ".abyss-data", default_workspace_path=repo_a)
    cfg.ensure_directories()

    # Seed legacy SQLite database without `projects` table or `sessions.project_id`
    conn = sqlite3.connect(cfg.sqlite_path)
    conn.execute(
        """
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            workspace_path TEXT NOT NULL,
            mode TEXT NOT NULL,
            status TEXT NOT NULL,
            pending_interrupt_json TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        INSERT INTO sessions (
            id, title, workspace_path, mode, status,
            pending_interrupt_json, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, NULL, ?, ?)
        """,
        (
            "legacy-sess",
            "Legacy Session",
            str(repo_a.resolve()),
            "ask",
            "idle",
            "2026-11-22T00:00:00+00:00",
            "2026-11-22T00:00:00+00:00",
        ),
    )
    conn.commit()
    conn.close()

    model = ScriptedReasoningChatModel(turns=[ScriptedTurn(content="ok")])
    app = create_app(config=cfg, model_factory=_FakeModelFactory(model))
    with TestClient(app) as client:
        # 1. Legacy session was migrated to a Project automatically
        projects = client.get("/api/projects").json()
        assert len(projects) == 1
        proj_a = projects[0]
        assert proj_a["path"] == str(repo_a.resolve())
        assert proj_a["is_git_repo"] is True

        legacy_detail = client.get("/api/sessions/legacy-sess").json()
        assert legacy_detail["session"]["project_id"] == proj_a["id"]

        # 2. Browse endpoint lists both repos under tmp_path
        browse = client.get("/api/workspace/browse", params={"path": str(tmp_path)}).json()
        dir_names = [d["name"] for d in browse["directories"]]
        assert "repo_a" in dir_names
        assert "repo_b" in dir_names

        # 3. Register repo_b (idempotent on duplicate)
        created_b = client.post("/api/projects", json={"path": str(repo_b)})
        assert created_b.status_code == 201
        proj_b = created_b.json()
        assert proj_b["name"] == "repo_b"

        dup_b = client.post("/api/projects", json={"path": str(repo_b)})
        assert dup_b.status_code == 201
        assert dup_b.json()["id"] == proj_b["id"]

        # 4. Create session under proj_b by project_id and write an artifact
        sess_b = client.post(
            "/api/sessions",
            json={"project_id": proj_b["id"], "mode": "ask"},
        ).json()
        assert sess_b["project_id"] == proj_b["id"]
        assert sess_b["workspace_path"] == str(repo_b.resolve())

        art_dir = cfg.artifacts_root / sess_b["id"]
        art_dir.mkdir(parents=True, exist_ok=True)
        (art_dir / "walkthrough.md").write_text("# Temp", encoding="utf-8")

        # 5. Delete proj_b -> cascades session AND cleans disk artifacts
        del_proj = client.delete(f"/api/projects/{proj_b['id']}")
        assert del_proj.status_code == 204
        assert client.get(f"/api/sessions/{sess_b['id']}").status_code == 404
        assert not art_dir.exists()


def test_message_history_cursor_pagination_preserves_order_and_has_more(tmp_path: Path) -> None:
    import sqlite3
    from datetime import UTC, datetime, timedelta

    workspace = tmp_path / "repo"
    _init_workspace(workspace)
    cfg = AppConfig.for_data_dir(tmp_path / ".abyss-data", default_workspace_path=workspace)
    model = ScriptedReasoningChatModel(turns=[ScriptedTurn(content="ok")])
    app = create_app(config=cfg, model_factory=_FakeModelFactory(model))

    with TestClient(app) as client:
        sid = client.post(
            "/api/sessions",
            json={"workspace_path": str(workspace), "mode": "ask"},
        ).json()["id"]

        base_time = datetime(2026, 11, 22, 12, 0, 0, tzinfo=UTC)
        conn = sqlite3.connect(cfg.sqlite_path)
        for idx in range(1, 36):
            ts = (base_time + timedelta(seconds=idx // 2)).isoformat()
            conn.execute(
                """
                INSERT INTO messages (
                    id, session_id, role, content, thinking, tool_calls_json, created_at
                )
                VALUES (?, ?, ?, ?, '', '[]', ?)
                """,
                (
                    f"msg-{idx:02d}",
                    sid,
                    "user" if idx % 2 == 1 else "assistant",
                    f"Message #{idx:02d}",
                    ts,
                ),
            )
        conn.commit()
        conn.close()

        page1 = client.get(f"/api/sessions/{sid}", params={"limit": 20}).json()
        assert page1["has_more_messages"] is True
        ids_1 = [m["id"] for m in page1["messages"]]
        assert ids_1 == [f"msg-{i:02d}" for i in range(16, 36)]

        page2_resp = client.get(
            f"/api/sessions/{sid}/messages",
            params={"before_id": ids_1[0], "limit": 20},
        )
        assert page2_resp.status_code == 200
        page2 = page2_resp.json()
        assert page2["has_more"] is False
        ids_2 = [m["id"] for m in page2["messages"]]
        assert ids_2 == [f"msg-{i:02d}" for i in range(1, 16)]
        assert ids_2 + ids_1 == [f"msg-{i:02d}" for i in range(1, 36)]
