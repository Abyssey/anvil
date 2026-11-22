"""WorkspaceInspector: directory tree, confined file preview, git diff hunks, and LLM ping."""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
from pydantic import BaseModel, Field

from backend.app.domains.artifacts.entities import DiffHunk, FileDiff
from backend.app.domains.artifacts.walkthrough_diff import parse_unified_diff
from backend.app.shared.errors import InvalidWorkspaceError, LLMUpstreamError, PathEscapeError
from backend.app.shared.llm_credentials import LLMCredentials

_IGNORED_NAMES = frozenset(
    {".git", "node_modules", ".venv", "__pycache__", "dist", ".abyss-data", ".pytest_cache"}
)
_MAX_FILE_PREVIEW_BYTES = 200_000


class WorkspaceInfo(BaseModel):
    path: str
    name: str
    is_git_repo: bool
    branch: str = ""


class TreeNode(BaseModel):
    name: str
    path: str
    is_dir: bool
    size: int = 0
    children: list[TreeNode] = Field(default_factory=list)


class FilePreview(BaseModel):
    path: str
    content: str
    is_binary: bool = False
    truncated: bool = False
    size: int = 0


class WorkspaceDiffResponse(BaseModel):
    workspace_path: str
    branch: str = ""
    git_head: str = ""
    files: list[FileDiff] = Field(default_factory=list)


class WorkspaceInspector:
    """Inspects local repositories safely within canonical workspace boundaries."""

    async def validate_workspace(self, workspace_path: str) -> WorkspaceInfo:
        root = self._resolve_root(workspace_path)
        branch = await self._git_output(root, "rev-parse", "--abbrev-ref", "HEAD")
        is_git = (root / ".git").exists() or bool(
            await self._git_output(root, "rev-parse", "--is-inside-work-tree")
        )
        return WorkspaceInfo(
            path=str(root),
            name=root.name or str(root),
            is_git_repo=is_git,
            branch=branch if is_git else "",
        )

    async def list_tree(
        self,
        workspace_path: str,
        *,
        rel_path: str = "",
        max_depth: int = 3,
    ) -> list[TreeNode]:
        root = self._resolve_root(workspace_path)
        target = self._resolve_inside(root, rel_path) if rel_path else root
        if not target.is_dir():
            return []
        return await asyncio.to_thread(self._scan_dir, root, target, max_depth, 0)

    async def read_file(self, workspace_path: str, rel_path: str) -> FilePreview:
        root = self._resolve_root(workspace_path)
        target = self._resolve_inside(root, rel_path)
        if not target.is_file():
            raise InvalidWorkspaceError(rel_path, reason="File not found")
        raw = await asyncio.to_thread(target.read_bytes)
        size = len(raw)
        truncated = size > _MAX_FILE_PREVIEW_BYTES
        sample = raw[:_MAX_FILE_PREVIEW_BYTES]
        if b"\x00" in sample:
            return FilePreview(
                path=target.relative_to(root).as_posix(),
                content="",
                is_binary=True,
                truncated=truncated,
                size=size,
            )
        return FilePreview(
            path=target.relative_to(root).as_posix(),
            content=sample.decode("utf-8", errors="replace"),
            is_binary=False,
            truncated=truncated,
            size=size,
        )

    async def get_diff(self, workspace_path: str) -> WorkspaceDiffResponse:
        root = self._resolve_root(workspace_path)
        is_git = (root / ".git").exists() or bool(
            await self._git_output(root, "rev-parse", "--is-inside-work-tree")
        )
        if not is_git:
            return WorkspaceDiffResponse(workspace_path=str(root))

        branch = await self._git_output(root, "rev-parse", "--abbrev-ref", "HEAD")
        git_head = await self._git_output(root, "rev-parse", "HEAD")
        raw_diff = await self._git_output(root, "diff", "--no-color", "HEAD")
        if not raw_diff:
            raw_diff = await self._git_output(root, "diff", "--no-color")

        parsed_files = parse_unified_diff(raw_diff)
        files: list[FileDiff] = [
            FileDiff(
                path=pf.path,
                status=pf.status,
                hunks=[
                    DiffHunk(
                        path=h.path,
                        name=h.name,
                        old_start=h.old_start,
                        old_count=h.old_count,
                        new_start=h.new_start,
                        new_count=h.new_count,
                        header=h.header,
                        lines=list(h.lines),
                    )
                    for h in pf.hunks
                ],
            )
            for pf in parsed_files
        ]

        seen_paths = {f.path for f in files}
        status_raw = await self._git_output(root, "status", "--porcelain")
        for line in status_raw.splitlines():
            if not line.startswith("?? "):
                continue
            untracked_rel = line[3:].strip()
            if not untracked_rel or untracked_rel in seen_paths:
                continue
            target = (root / untracked_rel).resolve()
            try:
                target.relative_to(root)
            except ValueError:
                continue
            preview = ""
            if target.is_file():
                raw = target.read_bytes()[:16_384]
                if b"\x00" not in raw:
                    preview = raw.decode("utf-8", errors="replace")
            files.append(
                FileDiff(
                    path=untracked_rel,
                    status="untracked",
                    hunks=[],
                    preview_content=preview,
                )
            )

        return WorkspaceDiffResponse(
            workspace_path=str(root),
            branch=branch,
            git_head=git_head,
            files=files,
        )

    async def ping_llm(self, creds: LLMCredentials) -> dict[str, object]:
        """Verify OpenAI-compatible endpoint reachability without logging credentials."""
        url = f"{creds.base_url}/models"
        headers = {"Authorization": f"Bearer {creds.api_key}"}
        try:
            async with httpx.AsyncClient(trust_env=False, timeout=10.0) as client:
                resp = await client.get(url, headers=headers)
        except httpx.HTTPError as exc:
            msg = f"Failed to connect to {creds.base_url}: {type(exc).__name__}"
            raise LLMUpstreamError(msg) from exc

        if resp.status_code >= 400:
            raise LLMUpstreamError(
                f"LLM endpoint returned HTTP {resp.status_code}",
                details={"status_code": resp.status_code},
            )
        return {"ok": True, "base_url": creds.base_url, "model": creds.model}

    @staticmethod
    def _resolve_root(workspace_path: str) -> Path:
        candidate = Path(workspace_path).expanduser().resolve()
        if not candidate.exists() or not candidate.is_dir():
            raise InvalidWorkspaceError(workspace_path)
        return candidate

    @staticmethod
    def _resolve_inside(root: Path, rel_path: str) -> Path:
        clean = rel_path.strip().lstrip("/")
        target = (root / clean).resolve()
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise PathEscapeError(rel_path) from exc
        return target

    def _scan_dir(
        self,
        root: Path,
        current: Path,
        max_depth: int,
        depth: int,
    ) -> list[TreeNode]:
        if depth >= max_depth:
            return []
        entries = sorted(
            current.iterdir(),
            key=lambda p: (not p.is_dir(), p.name.lower()),
        )
        nodes: list[TreeNode] = []
        for entry in entries:
            if entry.name in _IGNORED_NAMES:
                continue
            if entry.is_symlink():
                try:
                    entry.resolve().relative_to(root)
                except ValueError:
                    continue
            rel = entry.relative_to(root).as_posix()
            is_dir = entry.is_dir()
            children = self._scan_dir(root, entry, max_depth, depth + 1) if is_dir else []
            size = 0 if is_dir else entry.stat().st_size
            nodes.append(
                TreeNode(
                    name=entry.name,
                    path=rel,
                    is_dir=is_dir,
                    size=size,
                    children=children,
                )
            )
        return nodes

    @staticmethod
    async def _git_output(cwd: Path, *args: str) -> str:
        proc = await asyncio.create_subprocess_exec(
            "git",
            *args,
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        if proc.returncode != 0:
            return ""
        return stdout.decode("utf-8", errors="replace").strip()
