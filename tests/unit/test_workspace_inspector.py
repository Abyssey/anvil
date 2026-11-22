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
async def test_ping_llm_falls_back_when_models_route_missing_and_redacts_api_key() -> None:
    import httpx

    from backend.app.shared.errors import LLMUpstreamError
    from backend.app.shared.llm_credentials import LLMCredentials

    seen_paths: list[tuple[str, str]] = []

    def fallback_handler(request: httpx.Request) -> httpx.Response:
        seen_paths.append((request.method, request.url.path))
        if request.url.path.endswith("/models"):
            return httpx.Response(404, json={"error": {"message": "models endpoint disabled"}})
        if request.url.path.endswith("/chat/completions"):
            return httpx.Response(
                200,
                json={
                    "id": "chatcmpl-ping",
                    "object": "chat.completion",
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": "ok"},
                            "finish_reason": "stop",
                        }
                    ],
                },
            )
        return httpx.Response(500)

    creds = LLMCredentials(
        base_url="http://localhost:8001/v1",
        model="qwen3.8-flash",
        api_key="sk-ultra-secret-999",
    )
    inspector = WorkspaceInspector(http_transport=httpx.MockTransport(fallback_handler))
    res = await inspector.ping_llm(creds)
    assert res == {"ok": True, "base_url": "http://localhost:8001/v1", "model": "qwen3.8-flash"}
    assert seen_paths == [("GET", "/v1/models"), ("POST", "/v1/chat/completions")]

    def auth_error_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401,
            json={"error": {"message": "Invalid bearer token sk-ultra-secret-999"}},
        )

    err_inspector = WorkspaceInspector(http_transport=httpx.MockTransport(auth_error_handler))
    with pytest.raises(LLMUpstreamError) as exc_info:
        await err_inspector.ping_llm(creds)
    assert "401" in exc_info.value.message
    assert "sk-ultra-secret-999" not in exc_info.value.message
    assert "[REDACTED]" in exc_info.value.message


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
