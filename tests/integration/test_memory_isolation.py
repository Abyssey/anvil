"""Integration and unit tests for 2-tier Project vs. Session memory isolation."""

from __future__ import annotations

import json
import sqlite3
import subprocess
from pathlib import Path

import pytest
from deepagents.backends import CompositeBackend
from deepagents.middleware.filesystem import FilesystemMiddleware
from deepagents.middleware.summarization import SummarizationMiddleware
from fastapi.testclient import TestClient
from langchain_core.messages import SystemMessage

from backend.app.domains.agent.model_adapter import ModelFactory
from backend.app.domains.agent.run_backends import DynamicGuardedShellBackend, DynamicRunBackend
from backend.app.domains.agent.run_context import RunCatalog, RunContext
from backend.app.domains.agent.shell_guard import ShellGuard
from backend.app.main import create_app
from backend.app.shared.config import AppConfig
from backend.app.shared.llm_credentials import LLMCredentials
from tests.fakes.fake_chat_model import ScriptedReasoningChatModel, ScriptedTurn


class _FakeModelFactory(ModelFactory):
    def __init__(self, model: ScriptedReasoningChatModel) -> None:
        self._model = model

    def build(self, creds: LLMCredentials) -> ScriptedReasoningChatModel:
        return self._model


def _init_git_repo(repo: Path, *, agents_md: str | None = None) -> None:
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
    if agents_md is not None:
        (repo / "AGENTS.md").write_text(agents_md, encoding="utf-8")
    (repo / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=repo, check=True, capture_output=True)


def _headers() -> dict[str, str]:
    return {
        "X-LLM-Base-Url": "http://localhost:8001/v1",
        "X-LLM-Model": "qwen3.8-flash",
        "X-LLM-Api-Key": "sk-test-memory-key-123",
        "X-LLM-Enable-Thinking": "false",
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


def _system_prompt_text(call_messages: list[object]) -> str:
    parts: list[str] = []
    for msg in call_messages:
        if isinstance(msg, SystemMessage):
            if isinstance(msg.content, str):
                parts.append(msg.content)
            elif isinstance(msg.content, list):
                for block in msg.content:
                    if isinstance(block, str):
                        parts.append(block)
                    elif isinstance(block, dict) and isinstance(block.get("text"), str):
                        parts.append(block["text"])
    return "\n".join(parts)


def test_app_config_exposes_skills_and_memories_roots(tmp_path: Path) -> None:
    cfg = AppConfig.for_data_dir(tmp_path / ".abyss-data", default_workspace_path=tmp_path)
    assert cfg.skills_root == (tmp_path / ".abyss-data" / "skills").resolve()
    assert cfg.memories_root == (tmp_path / ".abyss-data" / "memories").resolve()
    cfg.ensure_directories()
    assert cfg.skills_root.is_dir()
    assert cfg.memories_root.is_dir()


def test_project_a_and_project_b_memory_strict_isolation(tmp_path: Path) -> None:
    repo_a = tmp_path / "project_a"
    repo_b = tmp_path / "project_b"
    _init_git_repo(repo_a, agents_md="REPO_A_STATIC_RULE")
    _init_git_repo(repo_b, agents_md="REPO_B_STATIC_RULE")

    model = ScriptedReasoningChatModel(
        turns=[
            ScriptedTurn(content="Project A answered."),
            ScriptedTurn(content="Project B answered."),
        ]
    )
    cfg = AppConfig.for_data_dir(tmp_path / ".abyss-data", default_workspace_path=repo_a)
    app = create_app(config=cfg, model_factory=_FakeModelFactory(model))

    with TestClient(app) as client:
        proj_a = client.post("/api/projects", json={"path": str(repo_a)}).json()
        proj_b = client.post("/api/projects", json={"path": str(repo_b)}).json()

        put_a = client.put(
            f"/api/projects/{proj_a['id']}/memory",
            json={"content": "PROJECT_A_SECRET_CONVENTION_XYZ"},
        )
        assert put_a.status_code == 200
        assert put_a.json()["content"] == "PROJECT_A_SECRET_CONVENTION_XYZ"

        put_b = client.put(
            f"/api/projects/{proj_b['id']}/memory",
            json={"content": "PROJECT_B_ONLY_CONVENTION_999"},
        )
        assert put_b.status_code == 200

        sess_a = client.post(
            "/api/sessions",
            json={"project_id": proj_a["id"], "mode": "ask"},
        ).json()
        sess_b = client.post(
            "/api/sessions",
            json={"project_id": proj_b["id"], "mode": "ask"},
        ).json()

        r_a = client.post(
            f"/api/sessions/{sess_a['id']}/messages/stream",
            json={"content": "hello project A", "mode": "ask"},
            headers=_headers(),
        )
        assert r_a.status_code == 200

        r_b = client.post(
            f"/api/sessions/{sess_b['id']}/messages/stream",
            json={"content": "hello project B", "mode": "ask"},
            headers=_headers(),
        )
        assert r_b.status_code == 200

        sys_a = _system_prompt_text(model.recorded_calls[0])
        sys_b = _system_prompt_text(model.recorded_calls[1])

        assert "REPO_A_STATIC_RULE" in sys_a
        assert "PROJECT_A_SECRET_CONVENTION_XYZ" in sys_a
        assert "PROJECT_B_ONLY_CONVENTION_999" not in sys_a
        assert "REPO_B_STATIC_RULE" not in sys_a

        assert "REPO_B_STATIC_RULE" in sys_b
        assert "PROJECT_B_ONLY_CONVENTION_999" in sys_b
        assert "PROJECT_A_SECRET_CONVENTION_XYZ" not in sys_b
        assert "REPO_A_STATIC_RULE" not in sys_b


def test_same_project_sessions_reload_updated_memory_every_turn(tmp_path: Path) -> None:
    repo = tmp_path / "project_shared"
    _init_git_repo(repo, agents_md="SHARED_REPO_RULE")

    model = ScriptedReasoningChatModel(
        turns=[
            ScriptedTurn(content="s1 turn 1"),
            ScriptedTurn(content="s2 turn 1"),
            ScriptedTurn(content="s2 turn 2 after update"),
        ]
    )
    cfg = AppConfig.for_data_dir(tmp_path / ".abyss-data", default_workspace_path=repo)
    app = create_app(config=cfg, model_factory=_FakeModelFactory(model))

    with TestClient(app) as client:
        proj = client.post("/api/projects", json={"path": str(repo)}).json()
        sess_1 = client.post(
            "/api/sessions",
            json={"project_id": proj["id"], "mode": "auto"},
        ).json()
        sess_2 = client.post(
            "/api/sessions",
            json={"project_id": proj["id"], "mode": "auto"},
        ).json()

        # Turn 1 on session 1 with memory v1
        client.put(f"/api/projects/{proj['id']}/memory", json={"content": "MEMORY_V1_ALPHA"})
        r1 = client.post(
            f"/api/sessions/{sess_1['id']}/messages/stream",
            json={"content": "turn 1 on s1", "mode": "auto"},
            headers=_headers(),
        )
        assert r1.status_code == 200

        # Turn 1 on session 2 sees memory v1
        r2 = client.post(
            f"/api/sessions/{sess_2['id']}/messages/stream",
            json={"content": "turn 1 on s2", "mode": "auto"},
            headers=_headers(),
        )
        assert r2.status_code == 200

        # Update project memory to v2, then run Turn 2 on SAME session 2 (must reload!)
        client.put(f"/api/projects/{proj['id']}/memory", json={"content": "MEMORY_V2_BETA"})
        r3 = client.post(
            f"/api/sessions/{sess_2['id']}/messages/stream",
            json={"content": "turn 2 on s2", "mode": "auto"},
            headers=_headers(),
        )
        assert r3.status_code == 200

        sys_1 = _system_prompt_text(model.recorded_calls[0])
        sys_2_t1 = _system_prompt_text(model.recorded_calls[1])
        sys_2_t2 = _system_prompt_text(model.recorded_calls[2])

        assert "MEMORY_V1_ALPHA" in sys_1
        assert "MEMORY_V1_ALPHA" in sys_2_t1
        assert "MEMORY_V2_BETA" in sys_2_t2
        assert "MEMORY_V1_ALPHA" not in sys_2_t2


def test_ask_mode_blocks_project_memory_write_and_auto_mode_allows_it(tmp_path: Path) -> None:
    repo = tmp_path / "repo_modes"
    _init_git_repo(repo)

    model = ScriptedReasoningChatModel(
        turns=[
            # Ask mode attempt to write /memories/project/AGENTS.md -> must be denied
            ScriptedTurn(
                tool_calls=[
                    {
                        "name": "write_file",
                        "args": {
                            "file_path": "/memories/project/AGENTS.md",
                            "content": "SHOULD_NOT_BE_WRITTEN_IN_ASK",
                        },
                    }
                ]
            ),
            ScriptedTurn(content="Ask write rejected."),
            # Auto mode write to /memories/project/AGENTS.md -> must succeed
            ScriptedTurn(
                tool_calls=[
                    {
                        "name": "write_file",
                        "args": {
                            "file_path": "/memories/project/AGENTS.md",
                            "content": "WRITTEN_IN_AUTO_MODE",
                        },
                    }
                ]
            ),
            ScriptedTurn(content="Auto write succeeded."),
        ]
    )
    cfg = AppConfig.for_data_dir(tmp_path / ".abyss-data", default_workspace_path=repo)
    app = create_app(config=cfg, model_factory=_FakeModelFactory(model))

    with TestClient(app) as client:
        proj = client.post("/api/projects", json={"path": str(repo)}).json()
        ask_sess = client.post(
            "/api/sessions",
            json={"project_id": proj["id"], "mode": "ask"},
        ).json()
        auto_sess = client.post(
            "/api/sessions",
            json={"project_id": proj["id"], "mode": "auto"},
        ).json()

        r_ask = client.post(
            f"/api/sessions/{ask_sess['id']}/messages/stream",
            json={"content": "remember this in ask", "mode": "ask"},
            headers=_headers(),
        )
        assert r_ask.status_code == 200
        mem_after_ask = client.get(f"/api/projects/{proj['id']}/memory").json()
        assert mem_after_ask["content"] == ""

        r_auto = client.post(
            f"/api/sessions/{auto_sess['id']}/messages/stream",
            json={"content": "remember this in auto", "mode": "auto"},
            headers=_headers(),
        )
        assert r_auto.status_code == 200
        mem_after_auto = client.get(f"/api/projects/{proj['id']}/memory").json()
        assert mem_after_auto["content"] == "WRITTEN_IN_AUTO_MODE"


def test_plan_mode_blocks_memory_write_before_approval_and_allows_after(tmp_path: Path) -> None:
    repo = tmp_path / "repo_plan_mem"
    _init_git_repo(repo)

    model = ScriptedReasoningChatModel(
        turns=[
            # Before plan approval: write to /memories/project/AGENTS.md must be blocked
            ScriptedTurn(
                tool_calls=[
                    {
                        "name": "write_file",
                        "args": {
                            "file_path": "/memories/project/AGENTS.md",
                            "content": "BLOCKED_BEFORE_APPROVAL",
                        },
                    }
                ]
            ),
            ScriptedTurn(
                tool_calls=[
                    {
                        "name": "write_file",
                        "args": {
                            "file_path": "/artifacts/implementation_plan.md",
                            "content": "# Plan\nUpdate project memory after approval.",
                        },
                    }
                ]
            ),
            ScriptedTurn(content="Plan ready."),
            # After approval: write to /memories/project/AGENTS.md succeeds
            ScriptedTurn(
                tool_calls=[
                    {
                        "name": "write_file",
                        "args": {
                            "file_path": "/memories/project/AGENTS.md",
                            "content": "ALLOWED_AFTER_PLAN_APPROVAL",
                        },
                    }
                ]
            ),
            ScriptedTurn(content="Memory updated."),
        ]
    )
    cfg = AppConfig.for_data_dir(tmp_path / ".abyss-data", default_workspace_path=repo)
    app = create_app(config=cfg, model_factory=_FakeModelFactory(model))

    with TestClient(app) as client:
        proj = client.post("/api/projects", json={"path": str(repo)}).json()
        plan_sess = client.post(
            "/api/sessions",
            json={"project_id": proj["id"], "mode": "plan"},
        ).json()

        r1 = client.post(
            f"/api/sessions/{plan_sess['id']}/messages/stream",
            json={"content": "plan and remember", "mode": "plan"},
            headers=_headers(),
        )
        assert r1.status_code == 200
        assert any(
            name == "interrupt" and payload.get("kind") == "plan_review"
            for name, payload in _parse_sse(r1.text)
        )
        assert client.get(f"/api/projects/{proj['id']}/memory").json()["content"] == ""

        r2 = client.post(
            f"/api/sessions/{plan_sess['id']}/resume",
            json={"decision": "approve"},
            headers=_headers(),
        )
        assert r2.status_code == 200
        assert (
            client.get(f"/api/projects/{proj['id']}/memory").json()["content"]
            == "ALLOWED_AFTER_PLAN_APPROVAL"
        )


def test_patch_session_workspace_change_returns_400_immutable(tmp_path: Path) -> None:
    repo_a = tmp_path / "repo_a"
    repo_b = tmp_path / "repo_b"
    _init_git_repo(repo_a)
    _init_git_repo(repo_b)

    model = ScriptedReasoningChatModel(turns=[ScriptedTurn(content="ok")])
    cfg = AppConfig.for_data_dir(tmp_path / ".abyss-data", default_workspace_path=repo_a)
    app = create_app(config=cfg, model_factory=_FakeModelFactory(model))

    with TestClient(app) as client:
        sess = client.post(
            "/api/sessions",
            json={"workspace_path": str(repo_a), "mode": "ask"},
        ).json()

        # Rebinding to a different workspace MUST fail with 400 SESSION_PROJECT_IMMUTABLE
        bad = client.patch(
            f"/api/sessions/{sess['id']}",
            json={"workspace_path": str(repo_b)},
        )
        assert bad.status_code == 400
        assert bad.json()["error"]["code"] == "SESSION_PROJECT_IMMUTABLE"

        # Keeping the same workspace while updating title still succeeds
        same = client.patch(
            f"/api/sessions/{sess['id']}",
            json={"workspace_path": str(repo_a), "title": "Renamed"},
        )
        assert same.status_code == 200
        assert same.json()["title"] == "Renamed"


def test_delete_session_and_delete_project_clean_checkpoints_and_memory_dir(
    tmp_path: Path,
) -> None:
    repo_a = tmp_path / "repo_a"
    repo_b = tmp_path / "repo_b"
    _init_git_repo(repo_a)
    _init_git_repo(repo_b)

    model = ScriptedReasoningChatModel(
        turns=[
            ScriptedTurn(content="reply a"),
            ScriptedTurn(content="reply b"),
        ]
    )
    cfg = AppConfig.for_data_dir(tmp_path / ".abyss-data", default_workspace_path=repo_a)
    app = create_app(config=cfg, model_factory=_FakeModelFactory(model))

    def _checkpoint_count(thread_id: str) -> int:
        conn = sqlite3.connect(cfg.sqlite_path)
        try:
            row = conn.execute(
                "SELECT COUNT(*) FROM checkpoints WHERE thread_id = ?",
                (thread_id,),
            ).fetchone()
            return int(row[0]) if row else 0
        finally:
            conn.close()

    with TestClient(app) as client:
        proj_a = client.post("/api/projects", json={"path": str(repo_a)}).json()
        proj_b = client.post("/api/projects", json={"path": str(repo_b)}).json()

        sess_a = client.post(
            "/api/sessions",
            json={"project_id": proj_a["id"], "mode": "ask"},
        ).json()
        sess_b = client.post(
            "/api/sessions",
            json={"project_id": proj_b["id"], "mode": "ask"},
        ).json()

        client.put(f"/api/projects/{proj_b['id']}/memory", json={"content": "DELETE_ME_MEMORY"})

        client.post(
            f"/api/sessions/{sess_a['id']}/messages/stream",
            json={"content": "hi a", "mode": "ask"},
            headers=_headers(),
        )
        client.post(
            f"/api/sessions/{sess_b['id']}/messages/stream",
            json={"content": "hi b", "mode": "ask"},
            headers=_headers(),
        )

        assert _checkpoint_count(sess_a["id"]) > 0
        assert _checkpoint_count(sess_b["id"]) > 0
        mem_dir_b = cfg.memories_root / "projects" / proj_b["id"]
        assert (mem_dir_b / "AGENTS.md").is_file()

        # Deleting sess_a removes its checkpoints
        del_s = client.delete(f"/api/sessions/{sess_a['id']}")
        assert del_s.status_code == 204
        assert _checkpoint_count(sess_a["id"]) == 0

        # Deleting proj_b removes child checkpoints AND project memory dir
        del_p = client.delete(f"/api/projects/{proj_b['id']}")
        assert del_p.status_code == 204
        assert _checkpoint_count(sess_b["id"]) == 0
        assert not mem_dir_b.exists()


@pytest.mark.asyncio
async def test_dynamic_run_backend_offload_routes_under_artifacts_not_repo(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "clean_repo"
    _init_git_repo(repo)
    artifacts_dir = tmp_path / "artifacts" / "sess-offload"
    memory_dir = tmp_path / "memories" / "projects" / "proj-1"
    skills_dir = tmp_path / "bundled_skills"
    skills_dir.mkdir(parents=True)

    ask_backend = DynamicRunBackend(skills_dir, read_only_workspace=True)
    shell_backend = DynamicGuardedShellBackend(skills_dir)

    for backend in (ask_backend, shell_backend):
        assert isinstance(backend, CompositeBackend)
        assert backend.artifacts_root.rstrip("/") == "/artifacts"

        fs_mw = FilesystemMiddleware(backend=backend)
        assert fs_mw._large_tool_results_prefix == "/artifacts/large_tool_results"
        assert fs_mw._conversation_history_prefix == "/artifacts/conversation_history"
        assert fs_mw._blobs_prefix == "/artifacts/blobs"

        summ_mw = SummarizationMiddleware(
            model=ScriptedReasoningChatModel(turns=[]),
            backend=backend,
        )
        assert summ_mw._history_path_prefix == "/artifacts/conversation_history"

    catalog = RunCatalog(
        session_id="sess-offload",
        project_id="proj-1",
        workspace_path=repo.resolve(),
        mode="auto",
        artifacts_dir=artifacts_dir,
        project_memory_dir=memory_dir,
        shell_guard=ShellGuard(),
    )
    async with RunContext.activate(catalog):
        w1 = await shell_backend.awrite(
            "/artifacts/conversation_history/sess-offload.md",
            "history",
        )
        assert w1.error is None
        w2 = await shell_backend.awrite("/artifacts/large_tool_results/tc1", "big payload")
        assert w2.error is None

    assert (artifacts_dir / "conversation_history" / "sess-offload.md").is_file()
    assert (artifacts_dir / "large_tool_results" / "tc1").is_file()

    # Target git repo working tree must remain 100% clean
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    assert status.stdout.strip() == ""


def test_project_memory_crud_and_32kb_limit(tmp_path: Path) -> None:
    repo = tmp_path / "repo_crud"
    _init_git_repo(repo, agents_md="# Repo Instructions")
    model = ScriptedReasoningChatModel(turns=[ScriptedTurn(content="ok")])
    cfg = AppConfig.for_data_dir(tmp_path / ".abyss-data", default_workspace_path=repo)
    app = create_app(config=cfg, model_factory=_FakeModelFactory(model))

    with TestClient(app) as client:
        proj = client.post("/api/projects", json={"path": str(repo)}).json()
        pid = proj["id"]

        # Initial GET -> empty content, detects repo AGENTS.md
        r_get = client.get(f"/api/projects/{pid}/memory")
        assert r_get.status_code == 200
        body = r_get.json()
        assert body["project_id"] == pid
        assert body["content"] == ""
        assert body["updated_at"] is None
        assert body["has_repo_agents_md"] is True

        # PUT valid content
        r_put = client.put(
            f"/api/projects/{pid}/memory",
            json={"content": "# Project Notes\n- Use pytest -q\n"},
        )
        assert r_put.status_code == 200
        put_body = r_put.json()
        assert put_body["content"] == "# Project Notes\n- Use pytest -q\n"
        assert put_body["updated_at"] is not None

        # PUT > 32 KB UTF-8 -> 400 error
        oversized = "가" * (12 * 1024)  # 36 KB in UTF-8
        r_big = client.put(
            f"/api/projects/{pid}/memory",
            json={"content": oversized},
        )
        assert r_big.status_code == 400
        assert r_big.json()["error"]["code"] == "PROJECT_MEMORY_TOO_LARGE"

        # Content unchanged after rejected write
        assert (
            client.get(f"/api/projects/{pid}/memory").json()["content"]
            == "# Project Notes\n- Use pytest -q\n"
        )

        # DELETE clears memory
        r_del = client.delete(f"/api/projects/{pid}/memory")
        assert r_del.status_code == 204
        cleared = client.get(f"/api/projects/{pid}/memory").json()
        assert cleared["content"] == ""
        assert cleared["updated_at"] is None

        # Unknown project -> 404
        assert client.get("/api/projects/does-not-exist/memory").status_code == 404


def test_stream_message_forwards_pinned_skills_and_subdir_agents_dedupes_root(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo_skills_and_dedupe"
    _init_git_repo(repo, agents_md="ROOT_RULE_DEDUPE_CHECK")
    nested = repo / "pkg"
    nested.mkdir()
    (nested / "AGENTS.md").write_text("NESTED_PKG_RULE_ONLY", encoding="utf-8")
    (nested / "mod.py").write_text("x = 42\n", encoding="utf-8")

    # Add a workspace skill to test pinned_skills injection
    skill_dir = repo / ".agents" / "skills" / "demo-pin"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: demo-pin\ndescription: Demo pinned skill\n---\nPINNED_SKILL_BODY_MARKER\n",
        encoding="utf-8",
    )

    model = ScriptedReasoningChatModel(
        turns=[
            ScriptedTurn(
                tool_calls=[{"name": "read_file", "args": {"file_path": "/pkg/mod.py"}}]
            ),
            ScriptedTurn(content="Done reading."),
        ]
    )
    cfg = AppConfig.for_data_dir(tmp_path / ".abyss-data", default_workspace_path=repo)
    app = create_app(config=cfg, model_factory=_FakeModelFactory(model))

    with TestClient(app) as client:
        sess = client.post(
            "/api/sessions",
            json={"workspace_path": str(repo), "mode": "ask"},
        ).json()

        resp = client.post(
            f"/api/sessions/{sess['id']}/messages/stream",
            json={
                "content": "inspect mod.py",
                "mode": "ask",
                "pinned_skills": ["demo-pin"],
            },
            headers=_headers(),
        )
        assert resp.status_code == 200

        # Turn 1 system prompt has root AGENTS.md; pinned skill injected into messages
        first_call = model.recorded_calls[0]
        assert "ROOT_RULE_DEDUPE_CHECK" in _system_prompt_text(first_call)
        assert any("PINNED_SKILL_BODY_MARKER" in str(getattr(m, "content", "")) for m in first_call)

        # Tool message for read_file('/pkg/mod.py') has nested AGENTS.md, NOT duplicated root
        tool_msgs = [
            msg
            for call in model.recorded_calls
            for msg in call
            if getattr(msg, "name", "") == "read_file"
        ]
        assert len(tool_msgs) >= 1
        assert any("NESTED_PKG_RULE_ONLY" in str(m.content) for m in tool_msgs)
        assert not any("ROOT_RULE_DEDUPE_CHECK" in str(m.content) for m in tool_msgs)


def test_project_memory_store_rejects_path_traversal_and_subdir_clear_session(
    tmp_path: Path,
) -> None:
    from backend.app.domains.agent.middleware.subdir_agents import SubdirAgentsReadMiddleware
    from backend.app.domains.sessions.memory_store import ProjectMemoryStore
    from backend.app.shared.errors import PathEscapeError

    store = ProjectMemoryStore(tmp_path / "memories")
    for bad_id in ("", ".", "..", "../escape", "a/b", "a\\b"):
        with pytest.raises(PathEscapeError):
            store.project_dir(bad_id)

    mw = SubdirAgentsReadMiddleware()
    mw._loaded_by_session["sess-1"] = {"/tmp/AGENTS.md"}
    mw.clear_session("sess-1")
    assert "sess-1" not in mw._loaded_by_session
    mw.clear_session("sess-nonexistent")  # idempotent
