"""TDD tests for backend hardening: NAS racy-safe checkpoints, 1-spawn git probe,
shared httpx pool lifecycle, conditional history load, HTTP 409 stream guard,
and aborted-turn checkpoint recovery.
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult

from backend.app.domains.agent import checkpoints as checkpoints_mod
from backend.app.domains.agent.checkpoints import TurnCheckpointStore
from backend.app.domains.agent.harness import DeepAgentHarness, open_checkpointer
from backend.app.domains.agent.middleware import git_evidence as git_evidence_mod
from backend.app.domains.agent.middleware.git_evidence import GitEvidenceTracker
from backend.app.domains.agent.model_adapter import ModelFactory, ReasoningChatOpenAI
from backend.app.domains.agent.workspace import WorkspaceInspector
from backend.app.domains.artifacts.store import ArtifactStore
from backend.app.domains.sessions.entities import RunMode, SessionStatus
from backend.app.domains.sessions.repository import SQLiteSessionRepository
from backend.app.domains.sessions.service import SessionService
from backend.app.main import create_app
from backend.app.shared.config import AppConfig
from backend.app.shared.llm_credentials import LLMCredentials
from tests.fakes.fake_chat_model import ScriptedReasoningChatModel, ScriptedTurn


def _creds() -> LLMCredentials:
    return LLMCredentials(
        base_url="http://localhost:8001/v1",
        model="qwen3.8-flash",
        api_key="sk-hardening-test-key",
        enable_thinking=False,
    )


def _headers() -> dict[str, str]:
    return {
        "X-LLM-Base-Url": "http://localhost:8001/v1",
        "X-LLM-Model": "qwen3.8-flash",
        "X-LLM-Api-Key": "sk-hardening-test-key",
        "X-LLM-Enable-Thinking": "false",
    }


@pytest.mark.asyncio
async def test_checkpoint_stat_fast_path_skips_old_clean_files_but_catches_racy_same_size_edits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    store = TurnCheckpointStore(tmp_path / "checkpoints")

    old_clean = workspace / "old_clean.py"
    old_clean.write_text("CLEAN = 1\n", encoding="utf-8")
    ten_sec_ago_ns = time.time_ns() - 10_000_000_000
    os.utime(old_clean, ns=(ten_sec_ago_ns, ten_sec_ago_ns))

    racy_file = workspace / "racy.py"
    racy_file.write_text("WAL\n", encoding="utf-8")
    racy_stat_before = racy_file.stat()

    capture = store.new_capture("sess-racy", workspace)
    await capture.capture_baseline()

    # Simulate a coarse-timestamp / cp -p edit within the racy window:
    # exact same byte length (4 bytes) AND exact same mtime_ns restored!
    racy_file.write_text("DEL\n", encoding="utf-8")
    os.utime(
        racy_file,
        ns=(racy_stat_before.st_atime_ns, racy_stat_before.st_mtime_ns),
    )

    read_paths: list[str] = []
    orig_read_bytes = Path.read_bytes

    def spy_read_bytes(self_path: Path) -> bytes:
        read_paths.append(self_path.name)
        return orig_read_bytes(self_path)

    monkeypatch.setattr(Path, "read_bytes", spy_read_bytes)

    cid, changed = await capture.finalize()
    monkeypatch.setattr(Path, "read_bytes", orig_read_bytes)

    # old_clean.py (10s old, untouched stat) MUST skip read_bytes during finalize!
    assert "old_clean.py" not in read_paths
    # racy.py (same size + same mtime_ns, inside racy window) MUST be hashed and caught!
    assert cid is not None
    assert changed == ["racy.py"]

    # Also verify _MAX_SCANNED_FILES cap logs a structured warning
    for idx in range(4):
        (workspace / f"extra_{idx}.py").write_text(f"X = {idx}\n", encoding="utf-8")
    monkeypatch.setattr(checkpoints_mod, "_MAX_SCANNED_FILES", 3)
    with caplog.at_level(logging.WARNING):
        store.scan_and_store_blobs("sess-cap", workspace)
    assert any("checkpoint.scan_limit_reached" in rec.message for rec in caplog.records)


@pytest.mark.asyncio
async def test_git_evidence_probes_branch_head_and_status_in_single_git_subprocess(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "git_repo"
    repo.mkdir()
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

    git_calls: list[tuple[str, ...]] = []
    orig_run_git = git_evidence_mod.run_git

    async def counting_run_git(cwd: Path, *args: str, **kwargs: Any) -> str:
        git_calls.append(args)
        return await orig_run_git(cwd, *args, **kwargs)

    monkeypatch.setattr(git_evidence_mod, "run_git", counting_run_git)

    # 1. Unborn initial branch (zero commits yet) + untracked file with spaces
    (repo / "hello world.py").write_text("X = 1\n", encoding="utf-8")
    tracker = GitEvidenceTracker(workspace_path=repo)
    snap_unborn = await tracker.capture_baseline()
    assert len(git_calls) == 1
    assert snap_unborn.is_git_repo is True
    assert snap_unborn.head == ""
    assert snap_unborn.branch != ""
    assert tracker.changed_files == ["hello world.py"]

    # 2. Commit and modify + rename
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)
    expected_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    git_calls.clear()
    snap_clean = await tracker.capture_baseline()
    assert len(git_calls) == 1
    assert snap_clean.head == expected_sha
    assert tracker.changed_files == []

    (repo / "hello world.py").write_text("X = 2\n", encoding="utf-8")
    (repo / "new_mod.py").write_text("Y = 3\n", encoding="utf-8")
    git_calls.clear()
    await tracker.refresh()
    assert len(git_calls) == 1
    assert tracker.has_changes is True
    assert sorted(tracker.changed_files) == ["hello world.py", "new_mod.py"]


@pytest.mark.asyncio
async def test_model_factory_shares_httpx_pool_and_closes_cleanly_on_aclose() -> None:
    factory = ModelFactory()
    m1 = factory.build(
        LLMCredentials(
            base_url="http://host-a:8000/v1",
            model="qwen3.8-flash",
            api_key="sk-key-a",
        )
    )
    m2 = factory.build(
        LLMCredentials(
            base_url="http://host-b:9000/v1",
            model="gpt-4.1-mini",
            api_key="sk-key-b",
        )
    )
    assert isinstance(m1, ReasoningChatOpenAI)
    assert isinstance(m2, ReasoningChatOpenAI)
    assert m1.http_client is m2.http_client
    assert m1.http_async_client is m2.http_async_client
    assert str(m1.root_async_client.base_url) == "http://host-a:8000/v1/"
    assert str(m2.root_async_client.base_url) == "http://host-b:9000/v1/"
    assert m1.root_async_client.api_key == "sk-key-a"
    assert m2.root_async_client.api_key == "sk-key-b"

    await factory.aclose()
    assert m1.http_async_client.is_closed is True
    assert m1.http_client.is_closed is True


@pytest.mark.asyncio
async def test_stream_message_skips_list_messages_when_langgraph_checkpoint_exists(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    cfg = AppConfig.for_data_dir(tmp_path / ".data", default_workspace_path=workspace)
    cfg.ensure_directories()

    model = ScriptedReasoningChatModel(
        turns=[
            ScriptedTurn(content="Turn 1 reply."),
            ScriptedTurn(content="Turn 2 reply."),
            ScriptedTurn(content="Turn 3 post-revert reply."),
        ]
    )

    class _Factory(ModelFactory):
        def build(self, creds: LLMCredentials) -> BaseChatModel:
            return model

    async with open_checkpointer(cfg.sqlite_path) as checkpointer:
        repo = SQLiteSessionRepository(cfg.sqlite_path)
        await repo.initialize()
        artifacts = ArtifactStore(cfg.artifacts_root)
        harness = DeepAgentHarness(
            config=cfg,
            artifact_store=artifacts,
            model_factory=_Factory(),
            checkpointer=checkpointer,
        )
        service = SessionService(
            config=cfg,
            repo=repo,
            artifact_store=artifacts,
            workspace_inspector=WorkspaceInspector(),
            harness=harness,
            memories_root=cfg.memories_root,
            on_delete_session_thread=harness.delete_thread,
        )
        sess = await service.create_session(workspace_path=str(workspace), mode=RunMode.ASK)

        list_calls = 0
        orig_list = repo.list_messages

        async def spy_list_messages(session_id: str) -> Any:
            nonlocal list_calls
            list_calls += 1
            return await orig_list(session_id)

        monkeypatch.setattr(repo, "list_messages", spy_list_messages)

        # Turn 1: no LangGraph thread checkpoint yet -> allowed to check list_messages
        async for _ in service.stream_message(
            session_id=sess.id,
            content="Hello 1",
            mode=RunMode.ASK,
            creds=_creds(),
        ):
            pass

        # Turn 2: LangGraph checkpoint NOW exists -> stream_message MUST skip list_messages!
        list_calls = 0
        async for _ in service.stream_message(
            session_id=sess.id,
            content="Hello 2",
            mode=RunMode.ASK,
            creds=_creds(),
        ):
            pass
        assert list_calls == 0

        # Revert Turn 2 -> resets LangGraph thread, so Turn 3 re-hydrates via list_messages!
        msgs = await orig_list(sess.id)
        await service.revert_from_message(sess.id, msgs[-1].id)
        list_calls = 0
        async for _ in service.stream_message(
            session_id=sess.id,
            content="Hello 3",
            mode=RunMode.ASK,
            creds=_creds(),
        ):
            pass
        assert list_calls == 1


class _GateChatModel(BaseChatModel):
    """Deterministic chat model that signals entry and waits on an asyncio.Event."""

    entered: asyncio.Event
    release: asyncio.Event

    @property
    def _llm_type(self) -> str:
        return "gate-chat-model"

    def bind_tools(self, tools: Any, **kwargs: Any) -> BaseChatModel:
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="ok"))])

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        self.entered.set()
        await self.release.wait()
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="done"))])

    async def _astream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatGenerationChunk]:
        self.entered.set()
        await self.release.wait()
        yield ChatGenerationChunk(message=AIMessageChunk(content="gate done"))


@pytest.mark.asyncio
async def test_concurrent_same_session_stream_returns_http_409_and_startup_recovers_stale_running(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    cfg = AppConfig.for_data_dir(tmp_path / ".data", default_workspace_path=workspace)
    cfg.ensure_directories()

    # Pre-seed a crashed session stuck in RUNNING status before app startup
    pre_repo = SQLiteSessionRepository(cfg.sqlite_path)
    await pre_repo.initialize()
    from datetime import UTC, datetime

    from backend.app.domains.sessions.entities import Session

    now = datetime.now(UTC)
    stuck_session = Session(
        id="stucksession1",
        title="Crashed Run",
        workspace_path=str(workspace),
        mode=RunMode.ASK,
        status=SessionStatus.RUNNING,
        created_at=now,
        updated_at=now,
    )
    await pre_repo.create_session(stuck_session)

    entered = asyncio.Event()
    release = asyncio.Event()
    gate_model = _GateChatModel(entered=entered, release=release)

    class _GateFactory(ModelFactory):
        def build(self, creds: LLMCredentials) -> BaseChatModel:
            return gate_model

    app = create_app(config=cfg, model_factory=_GateFactory())
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # 1. Startup must have recovered stale RUNNING -> IDLE so session is NOT bricked
            recovered = (await client.get("/api/sessions/stucksession1")).json()
            assert recovered["session"]["status"] == "idle"

            # 2. Launch Stream 1 and wait until it is actively running inside the graph
            s2 = (
                await client.post(
                    "/api/sessions",
                    json={"workspace_path": str(workspace), "mode": "ask"},
                )
            ).json()["id"]

            async def run_first_stream() -> httpx.Response:
                return await client.post(
                    f"/api/sessions/{s2}/messages/stream",
                    headers=_headers(),
                    json={"content": "First stream", "mode": "ask"},
                )

            first_task = asyncio.create_task(run_first_stream())
            await asyncio.wait_for(entered.wait(), timeout=5.0)

            # 3. Concurrent stream to the SAME session MUST return HTTP 409 JSON (not 200 SSE!)
            dup_resp = await client.post(
                f"/api/sessions/{s2}/messages/stream",
                headers=_headers(),
                json={"content": "Duplicate concurrent stream", "mode": "ask"},
            )
            assert dup_resp.status_code == 409
            assert dup_resp.json()["error"]["code"] == "SESSION_RUNNING"

            # 4. Release Stream 1 -> completes cleanly, and subsequent stream succeeds with 200
            release.set()
            r1 = await first_task
            assert r1.status_code == 200

            followup = await client.post(
                f"/api/sessions/{s2}/messages/stream",
                headers=_headers(),
                json={"content": "Sequential follow-up", "mode": "ask"},
            )
            assert followup.status_code == 200


@pytest.mark.asyncio
async def test_aborted_stream_recovers_file_checkpoint_for_clean_revert(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "target.py").write_text("ORIG = 1\n", encoding="utf-8")

    cfg = AppConfig.for_data_dir(tmp_path / ".data", default_workspace_path=workspace)
    cfg.ensure_directories()

    model = ScriptedReasoningChatModel(
        turns=[
            ScriptedTurn(
                tool_calls=[
                    {
                        "name": "write_file",
                        "args": {
                            "file_path": "/target.py",
                            "content": "MUTATED_BEFORE_ABORT = 2\n",
                        },
                    }
                ]
            ),
            ScriptedTurn(content="Final answer that gets aborted mid-turn."),
        ]
    )

    class _Factory(ModelFactory):
        def build(self, creds: LLMCredentials) -> BaseChatModel:
            return model

    async with open_checkpointer(cfg.sqlite_path) as checkpointer:
        repo = SQLiteSessionRepository(cfg.sqlite_path)
        await repo.initialize()
        artifacts = ArtifactStore(cfg.artifacts_root)
        harness = DeepAgentHarness(
            config=cfg,
            artifact_store=artifacts,
            model_factory=_Factory(),
            checkpointer=checkpointer,
        )
        service = SessionService(
            config=cfg,
            repo=repo,
            artifact_store=artifacts,
            workspace_inspector=WorkspaceInspector(),
            harness=harness,
            memories_root=cfg.memories_root,
            on_delete_session_thread=harness.delete_thread,
        )
        sess = await service.create_session(workspace_path=str(workspace), mode=RunMode.AUTO)

        stream_gen = service.stream_message(
            session_id=sess.id,
            content="Mutate target.py then abort",
            mode=RunMode.AUTO,
            creds=_creds(),
        )
        async for ev in stream_gen:
            if ev.event == "tool_end" and ev.data.get("name") == "write_file":
                # Client clicks Stop / switches project immediately after write_file!
                await stream_gen.aclose()
                break

        assert (workspace / "target.py").read_text(encoding="utf-8") == "MUTATED_BEFORE_ABORT = 2\n"

        # Aborted assistant message MUST carry the recovered checkpoint_id for clean Revert!
        msgs = await repo.list_messages(sess.id)
        assistant_msgs = [m for m in msgs if m.role == "assistant"]
        assert len(assistant_msgs) == 1
        assert assistant_msgs[0].checkpoint_id is not None
        assert assistant_msgs[0].changed_files == ["target.py"]

        rev = await service.revert_from_message(sess.id, assistant_msgs[0].id)
        assert rev.reverted_files == ["target.py"]
        assert (workspace / "target.py").read_text(encoding="utf-8") == "ORIG = 1\n"
