"""Unit tests for WorkspaceInspector path confinement, tree listing, and git diff parsing."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from backend.app.domains.agent.workspace import WorkspaceInspector
from backend.app.shared.errors import InvalidWorkspaceError, PathEscapeError


def _init_git_repo(repo_dir: Path) -> None:
    subprocess.run(["git", "init"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=repo_dir,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=repo_dir,
        check=True,
        capture_output=True,
    )
    (repo_dir / "src").mkdir()
    (repo_dir / "src" / "app.py").write_text("line1\nline2\nline3\n", encoding="utf-8")
    (repo_dir / ".venv").mkdir()
    (repo_dir / ".venv" / "ignored.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "src/app.py"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo_dir, check=True, capture_output=True)


@pytest.mark.asyncio
async def test_validate_workspace_and_list_tree(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    inspector = WorkspaceInspector()

    info = await inspector.validate_workspace(str(tmp_path))
    assert info.path == str(tmp_path.resolve())
    assert info.is_git_repo is True
    assert info.branch != ""

    with pytest.raises(InvalidWorkspaceError):
        await inspector.validate_workspace(str(tmp_path / "does_not_exist"))

    nodes = await inspector.list_tree(str(tmp_path), max_depth=3)
    names = {node.name for node in nodes}
    assert "src" in names
    assert ".venv" not in names
    assert ".git" not in names


@pytest.mark.asyncio
async def test_read_file_confinement_and_diff_hunks(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    outside = tmp_path.parent / "outside_secret.txt"
    outside.write_text("secret", encoding="utf-8")
    inspector = WorkspaceInspector()

    preview = await inspector.read_file(str(tmp_path), "src/app.py")
    assert "line1" in preview.content
    assert preview.is_binary is False

    with pytest.raises(PathEscapeError):
        await inspector.read_file(str(tmp_path), "../outside_secret.txt")

    # Mutate tracked file + add untracked file
    (tmp_path / "src" / "app.py").write_text("line1\nline2_changed\nline3\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("# New Doc\n", encoding="utf-8")

    diff_result = await inspector.get_diff(str(tmp_path))
    paths = {f.path: f for f in diff_result.files}
    assert "src/app.py" in paths
    assert len(paths["src/app.py"].hunks) >= 1
    assert "README.md" in paths
    assert paths["README.md"].status == "untracked"
