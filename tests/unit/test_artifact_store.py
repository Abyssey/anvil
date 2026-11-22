"""Unit tests for isolated ArtifactStore and safe workspace export."""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.app.domains.artifacts.entities import (
    ArtifactKind,
    FileHunks,
    WalkthroughChunk,
    WalkthroughPlan,
)
from backend.app.domains.artifacts.store import ArtifactStore
from backend.app.shared.errors import ArtifactNotFoundError, PathEscapeError


def test_artifact_store_isolates_sessions_and_persists_plan(tmp_path: Path) -> None:
    artifacts_root = tmp_path / "artifacts"
    store = ArtifactStore(artifacts_root)

    doc = store.write_artifact("sess-a", ArtifactKind.WALKTHROUGH, "# Onboarding\nSummary")
    assert doc.kind == ArtifactKind.WALKTHROUGH
    assert "Summary" in doc.content

    loaded = store.get_artifact("sess-a", ArtifactKind.WALKTHROUGH)
    assert loaded.content == "# Onboarding\nSummary"

    with pytest.raises(ArtifactNotFoundError):
        store.get_artifact("sess-b", ArtifactKind.WALKTHROUGH)

    plan = WalkthroughPlan(
        git_head="abc123",
        chunks=[
            WalkthroughChunk(
                id="chunk-1",
                title="Core changes",
                explanation="Updated main app",
                file_hunks=[FileHunks(path="app.py", hunks=["+10"])],
            )
        ],
    )
    store.save_walkthrough_plan("sess-a", plan)
    reloaded = store.get_walkthrough_plan("sess-a")
    assert reloaded is not None
    assert reloaded.chunks[0].id == "chunk-1"


def test_save_to_workspace_enforces_confinement(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    workspace = tmp_path / "repo"
    workspace.mkdir()
    store.write_artifact("sess-a", ArtifactKind.WALKTHROUGH, "# Onboarding Guide\nDetails")

    saved = store.save_to_workspace(
        "sess-a",
        ArtifactKind.WALKTHROUGH,
        workspace_path=str(workspace),
        relative_path="docs/ONBOARDING.md",
    )
    assert saved == (workspace / "docs" / "ONBOARDING.md").resolve()
    assert saved.read_text(encoding="utf-8") == "# Onboarding Guide\nDetails"

    with pytest.raises(PathEscapeError):
        store.save_to_workspace(
            "sess-a",
            ArtifactKind.WALKTHROUGH,
            workspace_path=str(workspace),
            relative_path="../escape.md",
        )


def test_delete_session_artifacts_removes_directory_idempotently(tmp_path: Path) -> None:
    artifacts_root = tmp_path / "artifacts"
    store = ArtifactStore(artifacts_root)
    store.write_artifact("sess-del", ArtifactKind.WALKTHROUGH, "# Temp")
    assert (artifacts_root / "sess-del").is_dir()

    store.delete_session_artifacts("sess-del")
    assert not (artifacts_root / "sess-del").exists()
    store.delete_session_artifacts("sess-del")


def test_render_todos_markdown_uses_valid_gfm_checkboxes() -> None:
    md = ArtifactStore.render_todos_markdown(
        [
            {"content": "Step A", "status": "completed"},
            {"content": "Step B", "status": "in_progress"},
            {"content": "Step C", "status": "pending"},
        ]
    )
    assert "- [x] Step A" in md
    assert "- [ ] **(In Progress)** Step B" in md
    assert "- [ ] Step C" in md
    assert "[/]" not in md
