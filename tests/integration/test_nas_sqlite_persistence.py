"""Integration tests for DATA_DIR & SQLITE_JOURNAL_MODE (local WAL vs shared-volume DELETE)."""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import empty_checkpoint

from backend.app.domains.agent.harness import open_checkpointer
from backend.app.domains.agent.model_adapter import ModelFactory
from backend.app.domains.sessions.entities import (
    ChatMessage,
    Project,
    RunMode,
    Session,
    SessionStatus,
)
from backend.app.domains.sessions.repository import SQLiteSessionRepository
from backend.app.main import create_app
from backend.app.shared.config import AppConfig
from backend.app.shared.llm_credentials import LLMCredentials
from tests.fakes.fake_chat_model import ScriptedReasoningChatModel, ScriptedTurn


class _FakeModelFactory(ModelFactory):
    def __init__(self, model: ScriptedReasoningChatModel) -> None:
        self._model = model

    def build(self, creds: LLMCredentials) -> ScriptedReasoningChatModel:
        return self._model


def _headers() -> dict[str, str]:
    return {
        "X-LLM-Base-Url": "http://127.0.0.1:8001/v1",
        "X-LLM-Model": "qwen3.8-flash",
        "X-LLM-Api-Key": "sk-nas-test-key",
        "X-LLM-Enable-Thinking": "true",
    }


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


def _init_git_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "nas-test@example.com"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "NAS Runner"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    (repo / "app.py").write_text("def version() -> str:\n    return '1.0'\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)


def _read_sqlite_journal_mode(db_path: Path) -> str:
    with sqlite3.connect(db_path) as conn:
        row = conn.execute("PRAGMA journal_mode;").fetchone()
        return str(row[0]).lower() if row and row[0] else ""


def _read_sqlite_tables(db_path: Path) -> set[str]:
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table';").fetchall()
        return {str(r[0]) for r in rows}


def test_default_env_uses_repo_dot_data_and_wal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DATA_DIR", raising=False)
    monkeypatch.delenv("SQLITE_JOURNAL_MODE", raising=False)

    cfg = AppConfig.from_repo_root(tmp_path)
    assert cfg.data_dir == (tmp_path / ".data").resolve()
    assert cfg.sqlite_path == (tmp_path / ".data" / "sessions.db").resolve()
    assert cfg.sqlite_journal_mode == "WAL"

    app = create_app(config=cfg)
    with TestClient(app):
        assert cfg.sqlite_path.is_file()
        assert _read_sqlite_journal_mode(cfg.sqlite_path) == "wal"


def test_shared_volume_delete_journal_mode_restart_and_checkpoint_recovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    nas_dir = (tmp_path / "mnt" / "shared-nas" / "agent-state").resolve()
    workspace = tmp_path / "workspace-repo"
    _init_git_repo(workspace)

    monkeypatch.setenv("DATA_DIR", str(nas_dir))
    monkeypatch.setenv("SQLITE_JOURNAL_MODE", "DELETE")

    model = ScriptedReasoningChatModel(
        turns=[
            ScriptedTurn(
                reasoning="Drafting implementation plan on NAS.",
                tool_calls=[
                    {
                        "name": "write_file",
                        "args": {
                            "file_path": "/artifacts/implementation_plan.md",
                            "content": "# NAS Plan\nUpgrade version() to 2.0 and verify.",
                        },
                    }
                ],
            ),
            ScriptedTurn(content="Plan ready for approval."),
            ScriptedTurn(
                tool_calls=[
                    {
                        "name": "edit_file",
                        "args": {
                            "file_path": "/app.py",
                            "old_string": "return '1.0'",
                            "new_string": "return '2.0'",
                        },
                    },
                    {
                        "name": "execute",
                        "args": {
                            "command": "python3 -c \"import app; assert app.version() == '2.0'\"",
                        },
                    },
                ]
            ),
            ScriptedTurn(
                tool_calls=[
                    {
                        "name": "write_file",
                        "args": {
                            "file_path": "/artifacts/walkthrough.md",
                            "content": "# Walkthrough\nUpgraded version() to 2.0 on shared NAS.",
                        },
                    }
                ]
            ),
            ScriptedTurn(content="Completed upgrade and verified without shell prompt."),
        ]
    )

    # Pod #1: omit explicit config so create_app() reads DATA_DIR & SQLITE_JOURNAL_MODE from env
    app_pod_1 = create_app(model_factory=_FakeModelFactory(model))
    db_file = nas_dir / "sessions.db"

    with TestClient(app_pod_1) as client1:
        proj_resp = client1.post("/api/projects", json={"path": str(workspace)})
        assert proj_resp.status_code == 201
        project_id = proj_resp.json()["id"]

        sess_resp = client1.post(
            "/api/sessions",
            json={
                "workspace_path": str(workspace),
                "project_id": project_id,
                "mode": "plan",
            },
        )
        assert sess_resp.status_code == 201
        session_id = sess_resp.json()["id"]

        stream1 = client1.post(
            f"/api/sessions/{session_id}/messages/stream",
            json={"content": "Upgrade version to 2.0", "mode": "plan"},
            headers=_headers(),
        )
        assert stream1.status_code == 200
        events1 = _parse_sse(stream1.text)
        assert any(
            name == "interrupt" and data.get("kind") == "plan_review"
            for name, data in events1
        )

        # Verify conversation tables & checkpointer share DELETE journal mode (no WAL)
        assert db_file.is_file()
        assert _read_sqlite_journal_mode(db_file) == "delete"
        assert not (nas_dir / "sessions.db-wal").exists()
        assert not (nas_dir / "sessions.db-shm").exists()
        tables = _read_sqlite_tables(db_file)
        assert {"projects", "sessions", "messages", "checkpoints", "writes"}.issubset(tables)

    # Simulate container replacement: fresh FastAPI app reading the same shared DATA_DIR
    app_pod_2 = create_app(model_factory=_FakeModelFactory(model))
    with TestClient(app_pod_2) as client2:
        projects = client2.get("/api/projects").json()
        assert any(p["id"] == project_id for p in projects)

        detail_before = client2.get(f"/api/sessions/{session_id}").json()
        assert detail_before["session"]["status"] == "awaiting_approval"
        assert detail_before["session"]["pending_interrupt"]["kind"] == "plan_review"
        assert len(detail_before["messages"]) >= 1

        # Resume LangGraph thread checkpoint from NAS DELETE DB:
        # routine post-approval python command executes autonomously without a second interrupt!
        resume_resp = client2.post(
            f"/api/sessions/{session_id}/resume",
            json={"decision": "approve"},
            headers=_headers(),
        )
        assert resume_resp.status_code == 200
        events2 = _parse_sse(resume_resp.text)
        event_names = [name for name, _ in events2]
        assert "interrupt" not in event_names
        assert "done" in event_names
        assert "return '2.0'" in (workspace / "app.py").read_text(encoding="utf-8")

        detail_after = client2.get(f"/api/sessions/{session_id}").json()
        assert detail_after["session"]["status"] == "idle"
        assert detail_after["session"]["pending_interrupt"] is None
        assert any(
            "Completed upgrade" in m["content"]
            for m in detail_after["messages"]
            if m["role"] == "assistant"
        )
        artifacts = client2.get(f"/api/sessions/{session_id}/artifacts").json()["artifacts"]
        assert {a["kind"] for a in artifacts} >= {"implementation_plan", "walkthrough"}

        # Confirm AsyncSqliteSaver never flipped journal_mode back to WAL during resumed writes
        assert _read_sqlite_journal_mode(db_file) == "delete"
        assert not (nas_dir / "sessions.db-wal").exists()


@pytest.mark.asyncio
async def test_single_pod_single_process_delete_mode_concurrent_locks_and_recovery(
    tmp_path: Path,
) -> None:
    nas_db = tmp_path / "nas-single-pod" / "sessions.db"
    nas_db.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC)

    async with open_checkpointer(nas_db, journal_mode="DELETE", busy_timeout_ms=15_000) as saver:
        repo = SQLiteSessionRepository(nas_db, journal_mode="DELETE", busy_timeout_ms=15_000)
        await repo.initialize()

        project = await repo.create_project(
            Project(
                id="proj-nas",
                name="nas-repo",
                path=str(tmp_path),
                created_at=now,
                updated_at=now,
            )
        )
        session = await repo.create_session(
            Session(
                id="sess-nas",
                project_id=project.id,
                title="NAS Concurrency",
                workspace_path=str(tmp_path),
                mode=RunMode.AUTO,
                status=SessionStatus.RUNNING,
                created_at=now,
                updated_at=now,
            )
        )

        async def _write_message(idx: int) -> None:
            await repo.append_message(
                ChatMessage(
                    id=f"msg-{idx:02d}",
                    session_id=session.id,
                    role="user" if idx % 2 == 0 else "assistant",
                    content=f"turn-{idx}",
                    created_at=datetime.now(UTC),
                )
            )

        async def _write_checkpoint(idx: int) -> None:
            cfg: RunnableConfig = {
                "configurable": {
                    "thread_id": session.id,
                    "checkpoint_ns": "",
                }
            }
            ckpt = empty_checkpoint()
            ckpt["channel_values"] = {"step": idx}
            await saver.aput(cfg, ckpt, {"source": "loop", "step": idx}, {})

        async def _read_state() -> None:
            _ = await repo.get_session(session.id)
            _ = await repo.list_messages(session.id)

        await asyncio.gather(
            *[_write_message(i) for i in range(12)],
            *[_write_checkpoint(i) for i in range(6)],
            *[_read_state() for _ in range(6)],
        )

        assert _read_sqlite_journal_mode(nas_db) == "delete"

    # Reopen after simulated process restart and verify zero lost writes or corrupted checkpoints
    async with open_checkpointer(nas_db, journal_mode="DELETE") as saver2:
        repo2 = SQLiteSessionRepository(nas_db, journal_mode="DELETE")
        await repo2.initialize()
        recovered_msgs = await repo2.list_messages("sess-nas")
        assert len(recovered_msgs) == 12
        recovered_tuple = await saver2.aget_tuple(
            {"configurable": {"thread_id": "sess-nas", "checkpoint_ns": ""}}
        )
        assert recovered_tuple is not None


def test_startup_aborts_immediately_on_invalid_journal_mode_or_inaccessible_data_dir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 1. Invalid SQLITE_JOURNAL_MODE fails fast
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "valid-dir"))
    monkeypatch.setenv("SQLITE_JOURNAL_MODE", "INVALID_MODE")
    with pytest.raises(RuntimeError, match="SQLITE_JOURNAL_MODE"):
        create_app()

    # 2. DATA_DIR pointing to a regular file fails fast during startup
    not_a_dir = tmp_path / "regular-file"
    not_a_dir.write_text("occupied", encoding="utf-8")
    monkeypatch.setenv("DATA_DIR", str(not_a_dir))
    monkeypatch.setenv("SQLITE_JOURNAL_MODE", "DELETE")
    app_bad_path = create_app()
    with pytest.raises(RuntimeError, match="DATA_DIR|directory"), TestClient(app_bad_path):
        pass

    # 3. Read-only DATA_DIR fails fast on verify_storage_writable()
    if hasattr(os, "geteuid") and os.geteuid() != 0:
        ro_dir = tmp_path / "readonly-nas"
        ro_dir.mkdir(parents=True)
        for sub in ("artifacts", "skills", "memories", "checkpoints"):
            (ro_dir / sub).mkdir()
        ro_dir.chmod(0o555)
        try:
            monkeypatch.setenv("DATA_DIR", str(ro_dir))
            monkeypatch.setenv("SQLITE_JOURNAL_MODE", "DELETE")
            app_ro = create_app()
            with pytest.raises(RuntimeError, match="not writable"), TestClient(app_ro):
                pass
        finally:
            ro_dir.chmod(0o755)


def test_no_web_ui_settings_exposed_for_data_dir_or_sqlite_journal_mode() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    settings_ts = (repo_root / "frontend" / "src" / "state" / "settings.ts").read_text(
        encoding="utf-8"
    )
    settings_dialog = (
        repo_root / "frontend" / "src" / "components" / "settings" / "SettingsDialog.tsx"
    ).read_text(encoding="utf-8")
    for forbidden in ("DATA_DIR", "SQLITE_JOURNAL_MODE", "journalMode", "sqliteJournalMode"):
        assert forbidden not in settings_ts
        assert forbidden not in settings_dialog
