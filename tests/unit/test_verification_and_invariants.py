"""Property/invariant tests for checkpoints, ShellGuard, verification loop, and legacy SQLite."""

from __future__ import annotations

import logging
import os
import random
import sqlite3
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import ToolMessage

from backend.app.domains.agent.checkpoints import TurnCheckpointStore
from backend.app.domains.agent.middleware.verification import (
    VerificationLoopMiddleware,
    VerificationTracker,
)
from backend.app.domains.agent.run_context import RunCatalog, RunContext
from backend.app.domains.agent.shell_guard import ShellAction, ShellGuard
from backend.app.domains.artifacts.walkthrough_diff import parse_unified_diff
from backend.app.domains.sessions.repository import SQLiteSessionRepository
from backend.app.domains.skills.store import SkillStore
from backend.app.shared.config import AppConfig
from backend.app.shared.errors import PathEscapeError


def _read_workspace_tree(root: Path) -> dict[str, bytes]:
    state: dict[str, bytes] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d != ".git")
        for fname in sorted(filenames):
            full = Path(dirpath) / fname
            state[full.relative_to(root).as_posix()] = full.read_bytes()
    return state


def _list_workspace_dirs(root: Path) -> set[str]:
    dirs: set[str] = set()
    for dirpath, dirnames, _ in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d != ".git")
        cur = Path(dirpath)
        if cur != root:
            dirs.add(cur.relative_to(root).as_posix())
    return dirs


@pytest.mark.parametrize("seed", [11, 29, 47, 83, 101])
async def test_checkpoint_store_state_machine_roundtrip_invariant(
    tmp_path: Path,
    seed: int,
) -> None:
    """Property invariant: arbitrary multi-turn create/modify/delete sequences revert losslessly."""
    rng = random.Random(seed)
    workspace = tmp_path / f"ws_{seed}"
    workspace.mkdir(parents=True)
    (workspace / "core.py").write_bytes(b"v0 = 0\n")
    (workspace / "pkg" / "init.py").parent.mkdir(parents=True)
    (workspace / "pkg" / "init.py").write_bytes(b"PKG = True\n")

    store = TurnCheckpointStore(tmp_path / f"cp_{seed}")
    session_id = f"sess-{seed}"
    candidate_paths = [
        "core.py",
        "pkg/init.py",
        "pkg/sub/deep.py",
        "docs/guide.md",
        "tests/sub/a/b/test_gen.py",
        "scripts/run.sh",
    ]

    snapshots: list[dict[str, bytes]] = [_read_workspace_tree(workspace)]
    dir_snapshots: list[set[str]] = [_list_workspace_dirs(workspace)]
    checkpoint_ids: list[str] = []

    for turn_idx in range(4):
        cap = store.new_capture(session_id, workspace)
        await cap.capture_baseline()

        chosen = rng.sample(candidate_paths, k=rng.randint(2, 4))
        for rel in chosen:
            target = workspace / rel
            op = rng.choice(["write", "write", "delete"])
            if op == "delete" and target.is_file():
                target.unlink()
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                payload = f"turn={turn_idx};seed={seed};salt={rng.randint(1, 99999)}\n".encode()
                target.write_bytes(payload)

        cid, _ = await cap.finalize()
        assert cid is not None
        checkpoint_ids.append(cid)
        snapshots.append(_read_workspace_tree(workspace))
        dir_snapshots.append(_list_workspace_dirs(workspace))

    # Stepwise reverse-chronological rollback must match every intermediate file tree
    # and restore initial directories with zero orphan empty dirs.
    for step in range(4, 0, -1):
        await store.revert_checkpoints(session_id, workspace, [checkpoint_ids[step - 1]])
        assert _read_workspace_tree(workspace) == snapshots[step - 1]
    assert _list_workspace_dirs(workspace) == dir_snapshots[0]


async def test_checkpoint_batch_revert_dedup_and_path_confinement(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    (workspace / "base.txt").write_bytes(b"shared-blob-content")
    (workspace / "mirror.txt").write_bytes(b"shared-blob-content")

    store = TurnCheckpointStore(tmp_path / "checkpoints")

    # Turn 1: uncommitted change we want to KEEP when reverting Turns 2 & 3
    cap1 = store.new_capture("sess-batch", workspace)
    await cap1.capture_baseline()
    (workspace / "base.txt").write_bytes(b"turn-1-preserved")
    cid1, changed1 = await cap1.finalize()
    assert cid1 is not None
    assert changed1 == ["base.txt"]

    # Content-addressed deduplication: identical initial content stored once
    blobs = list((store.session_dir("sess-batch") / "blobs").iterdir())
    assert len(blobs) == 1

    # Turn 2 & Turn 3: modify base.txt, delete mirror.txt, create nested/new.txt
    cap2 = store.new_capture("sess-batch", workspace)
    await cap2.capture_baseline()
    (workspace / "base.txt").write_bytes(b"turn-2-overwritten")
    (workspace / "mirror.txt").unlink()
    cid2, _ = await cap2.finalize()
    assert cid2 is not None

    cap3 = store.new_capture("sess-batch", workspace)
    await cap3.capture_baseline()
    (workspace / "base.txt").write_bytes(b"turn-3-latest")
    (workspace / "nested" / "new.txt").parent.mkdir(parents=True)
    (workspace / "nested" / "new.txt").write_bytes(b"ephemeral")
    cid3, _ = await cap3.finalize()
    assert cid3 is not None

    reverted = await store.revert_checkpoints("sess-batch", workspace, [cid3, cid2])
    assert sorted(reverted) == ["base.txt", "mirror.txt", "nested/new.txt"]
    assert (workspace / "base.txt").read_bytes() == b"turn-1-preserved"
    assert (workspace / "mirror.txt").read_bytes() == b"shared-blob-content"
    assert not (workspace / "nested").exists()

    with pytest.raises(PathEscapeError):
        store.session_dir("../escape")

    # Tampered manifest attempting path traversal must raise PathEscapeError
    evil_dir = store.session_dir("sess-batch") / "turns"
    evil_dir.mkdir(parents=True, exist_ok=True)
    (evil_dir / "evilcp.json").write_text(
        '{"checkpoint_id":"evilcp","session_id":"sess-batch","created_at":"now",'
        '"changed_files":["../../outside.txt"],'
        '"baseline_files":{"../../outside.txt":{"existed":false}}}',
        encoding="utf-8",
    )
    with pytest.raises(PathEscapeError):
        await store.revert_checkpoints("sess-batch", workspace, ["evilcp"])


def test_shell_guard_invariants_idempotency_and_plan_verification_gate() -> None:
    guard = ShellGuard()
    raw = (
        "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.secret "
        "OPENAI_API_KEY=sk-proj-supersecret1234567890 "
        "custom=my-runtime-token-xyz"
    )
    once = guard.scrub_output(raw, extra_secrets=("my-runtime-token-xyz",))
    twice = guard.scrub_output(once, extra_secrets=("my-runtime-token-xyz",))
    assert once == twice
    assert "sk-proj-supersecret1234567890" not in once
    assert "my-runtime-token-xyz" not in once
    assert "eyJhbGciOiJIUzI1NiJ9.secret" not in once

    verify_cmds = [
        ".venv/bin/pytest -q",
        "pytest tests/unit",
        ".venv/bin/ruff check backend tests",
        "npm test -- --run",
        "npm run typecheck",
        "npx vitest --run",
        "cargo test",
        "go test ./...",
    ]
    for cmd in verify_cmds:
        assert guard.is_verification_command(cmd), cmd
        # Ask mode NEVER allows shell commands
        assert guard.evaluate(cmd, "ask", plan_approved=True).action == ShellAction.BLOCK
        # Plan mode before approval blocks/requires approval, after approval allows pure checks
        assert (
            guard.evaluate(cmd, "plan", plan_approved=False).action
            == ShellAction.REQUIRE_APPROVAL
        )
        assert guard.evaluate(cmd, "plan", plan_approved=True).action == ShellAction.ALLOW

    # Metacharacter/chaining smuggling MUST NEVER auto-allow in Plan mode
    smuggled = [
        "pytest && rm -rf build",
        "pytest; echo pwned",
        "pytest | tee out.log",
        "pytest $(whoami)",
        "pytest `id`",
        "pytest > /tmp/out.txt",
        "echo hello",
    ]
    for cmd in smuggled:
        verdict = guard.evaluate(cmd, "plan", plan_approved=True)
        assert verdict.action != ShellAction.ALLOW, f"Unexpectedly allowed in plan mode: {cmd}"

    # Independent verifier rule paths ("Changes can't modify their own checks")
    for protected in (
        "/AGENTS.md",
        "/CLAUDE.md",
        "/REVIEW.md",
        "/.github/workflows/ci.yml",
        "/.claude/hooks/pre_commit.sh",
        "/tests/test_architecture_boundaries.py",
    ):
        assert guard.is_verifier_rule_path(protected), protected

    for regular in ("/backend/app/main.py", "/frontend/src/App.tsx", "/tests/unit/test_shared.py"):
        assert not guard.is_verifier_rule_path(regular), regular


async def test_verification_middleware_three_strike_rule_and_observe_logging(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    mw = VerificationLoopMiddleware()
    tracker = VerificationTracker()
    catalog = RunCatalog(
        session_id="sess-verify",
        project_id="proj-1",
        workspace_path=tmp_path,
        mode="auto",
        artifacts_dir=tmp_path / "artifacts",
        project_memory_dir=None,
        shell_guard=ShellGuard(),
        verification_tracker=tracker,
    )

    def _make_req(name: str, args: dict[str, Any]) -> Any:
        req = MagicMock()
        req.tool_call = {"id": "tc-1", "name": name, "args": args}
        return req

    assert tracker.stamp == "not_needed"

    async with RunContext.activate(catalog):
        # 1. Mutating workspace sets stamp -> "none"; virtual /artifacts/ does not
        await mw.awrap_tool_call(
            _make_req("write_file", {"file_path": "/artifacts/walkthrough.md"}),
            lambda _r: _async_tool_msg("written"),
        )
        assert tracker.stamp == "not_needed"

        await mw.awrap_tool_call(
            _make_req("edit_file", {"file_path": "/app.py"}),
            lambda _r: _async_tool_msg("edited"),
        )
        assert tracker.stamp == "none"

        # 2. Observe-mode logs unverified mutation without throwing
        with caplog.at_level(logging.INFO):
            assert await mw.aafter_agent({}, MagicMock()) is None
        assert any(
            getattr(rec, "event", "") == "verification.observe_unverified_mutation"
            for rec in caplog.records
        )

        # 3. Three consecutive failing pytest runs trigger Three-Strike stop notice on strike 3
        for strike in (1, 2, 3):
            res = await mw.awrap_tool_call(
                _make_req("execute", {"command": ".venv/bin/pytest -q"}),
                lambda _r: _async_tool_msg(
                    "1 failed\n[Command failed with exit code 1]",
                    exit_code=1,
                ),
            )
            assert isinstance(res, ToolMessage)
            if strike < 3:
                assert "[Verification Three-Strike Rule]" not in str(res.content)
            else:
                assert "[Verification Three-Strike Rule]" in str(res.content)
        assert tracker.stamp == "failed"

        # 4. Passing verification resets strikes & sets stamp="passed"; subsequent edit invalidates
        await mw.awrap_tool_call(
            _make_req("execute", {"command": ".venv/bin/pytest -q"}),
            lambda _r: _async_tool_msg(
                "5 passed\n[Command succeeded with exit code 0]",
                exit_code=0,
            ),
        )
        assert tracker.consecutive_failures == 0
        assert tracker.stamp == "passed"

        await mw.awrap_tool_call(
            _make_req("edit_file", {"file_path": "/app.py"}),
            lambda _r: _async_tool_msg("edited again"),
        )
        assert tracker.stamp == "none"


async def _async_tool_msg(content: str, *, exit_code: int | None = None) -> ToolMessage:
    artifact = {"exit_code": exit_code} if exit_code is not None else None
    return ToolMessage(content=content, name="tool", tool_call_id="tc-1", artifact=artifact)


async def test_legacy_sqlite_transcript_migration_and_diff_parser_invariants(
    tmp_path: Path,
) -> None:
    """Diff-triggered reliability check: pre-metadata_json SQLite DBs load without data loss."""
    db_path = tmp_path / "legacy.db"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE sessions (
                id TEXT PRIMARY KEY,
                project_id TEXT,
                title TEXT NOT NULL,
                workspace_path TEXT NOT NULL,
                mode TEXT NOT NULL,
                status TEXT NOT NULL,
                pending_interrupt_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE messages (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                thinking TEXT NOT NULL DEFAULT '',
                tool_calls_json TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL
            );
            INSERT INTO sessions VALUES (
                'legacy-s1', NULL, 'Old Chat', '/tmp', 'ask', 'idle', NULL,
                '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00'
            );
            INSERT INTO messages VALUES (
                'm-old-1', 'legacy-s1', 'user', 'Hello from old schema', '', '[]',
                '2026-01-01T00:00:01+00:00'
            );
            INSERT INTO messages VALUES (
                'm-old-2', 'legacy-s1', 'assistant', 'Legacy reply', '', '[]',
                '2026-01-01T00:00:02+00:00'
            );
            """
        )

    repo = SQLiteSessionRepository(db_path)
    await repo.initialize()
    loaded = await repo.list_messages("legacy-s1")
    assert [m.id for m in loaded] == ["m-old-1", "m-old-2"]
    assert loaded[1].content == "Legacy reply"
    assert loaded[1].checkpoint_id is None
    assert loaded[1].changed_files == []
    assert loaded[1].verification_stamp is None

    # Verify built-in progressive-disclosure `verify` skill is discoverable and protected
    cfg = AppConfig.for_data_dir(tmp_path / ".abyss-data", default_workspace_path=tmp_path)
    skill_store = SkillStore(
        bundled_dir=cfg.bundled_skills_dir,
        global_dir=cfg.skills_root,
        registry_client=MagicMock(),
    )
    installed = skill_store.list_installed(workspace_path=str(tmp_path))
    verify_skill = next((s for s in installed if s.name == "verify"), None)
    assert verify_skill is not None
    assert verify_skill.scope == "bundled"
    assert verify_skill.deletable is False

    # Unified diff parser structural invariant across multi-file multi-hunk diffs
    sample_diff = "\n".join(
        [
            "diff --git a/a.py b/a.py",
            "index 1111111..2222222 100644",
            "--- a/a.py",
            "+++ b/a.py",
            "@@ -1,2 +1,3 @@ def alpha():",
            " x = 1",
            "-y = 2",
            "+y = 3",
            "+z = 4",
            "diff --git a/b.py b/b.py",
            "new file mode 100644",
            "--- /dev/null",
            "+++ b/b.py",
            "@@ -0,0 +1,2 @@",
            "+hello = True",
            "+world = False",
        ]
    )
    parsed = parse_unified_diff(sample_diff)
    assert [f.path for f in parsed] == ["a.py", "b.py"]
    assert parsed[0].status == "modified"
    assert parsed[1].status == "added"
    assert list(parsed[0].hunks[0].lines) == [" x = 1", "-y = 2", "+y = 3", "+z = 4"]
    assert list(parsed[1].hunks[0].lines) == ["+hello = True", "+world = False"]
