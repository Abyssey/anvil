"""Unit tests for WorkspaceInspector path confinement, tree listing, and git diff parsing."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from backend.app.domains.agent.workspace import WorkspaceInspector
from backend.app.shared.errors import DomainError, InvalidWorkspaceError, PathEscapeError


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

    # Mutate tracked file + add untracked file + add untracked file in new subdirectory
    (tmp_path / "src" / "app.py").write_text("line1\nline2_changed\nline3\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("# New Doc\n", encoding="utf-8")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "ONBOARDING.md").write_text("# Onboarding\n", encoding="utf-8")

    diff_result = await inspector.get_diff(str(tmp_path))
    paths = {f.path: f for f in diff_result.files}
    assert "src/app.py" in paths
    assert len(paths["src/app.py"].hunks) >= 1
    assert "README.md" in paths
    assert paths["README.md"].status == "untracked"
    assert "docs/ONBOARDING.md" in paths
    assert "docs/" not in paths
    assert "# Onboarding" in (paths["docs/ONBOARDING.md"].preview_content or "")


@pytest.mark.asyncio
async def test_browse_directories_lists_subdirs_skips_hidden_and_detects_git(
    tmp_path: Path,
) -> None:
    repo_a = tmp_path / "repo_a"
    repo_a.mkdir()
    _init_git_repo(repo_a)
    (tmp_path / "plain_dir").mkdir()
    (tmp_path / ".hidden_dir").mkdir()
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "notes.txt").write_text("hi", encoding="utf-8")

    inspector = WorkspaceInspector()
    result = await inspector.browse_directories(str(tmp_path))

    assert result.current_path == str(tmp_path.resolve())
    assert result.parent_path == str(tmp_path.resolve().parent)
    names = [d.name for d in result.directories]
    assert names == ["plain_dir", "repo_a"]
    by_name = {d.name: d for d in result.directories}
    assert by_name["repo_a"].is_git_repo is True
    assert by_name["plain_dir"].is_git_repo is False


@pytest.mark.asyncio
async def test_create_directory_creates_child_and_rejects_traversal_or_file_conflict(
    tmp_path: Path,
) -> None:
    inspector = WorkspaceInspector()
    (tmp_path / "occupied.txt").write_text("file", encoding="utf-8")

    created = await inspector.create_directory(str(tmp_path), "my-new-project")
    expected = (tmp_path / "my-new-project").resolve()
    assert expected.is_dir()
    assert created.path == str(expected)
    assert created.name == "my-new-project"
    assert created.is_git_repo is False

    # Idempotent if directory already exists
    again = await inspector.create_directory(str(tmp_path), "my-new-project")
    assert again.path == str(expected)

    # Reject empty, '.', '..', or path separators
    for invalid_name in ("", "   ", ".", "..", "../escape", "nested/sub", "back\\slash"):
        with pytest.raises(DomainError):
            await inspector.create_directory(str(tmp_path), invalid_name)

    # Reject conflict with existing regular file
    with pytest.raises(DomainError) as exc_info:
        await inspector.create_directory(str(tmp_path), "occupied.txt")
    assert exc_info.value.status_code == 409
