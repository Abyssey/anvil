"""WorkspaceInspector: directory tree, confined file preview, and git diff hunks."""

from __future__ import annotations

import asyncio
from pathlib import Path

from pydantic import BaseModel, Field

from backend.app.domains.agent.git_utils import run_git
from backend.app.domains.artifacts.entities import DiffHunk, FileDiff
from backend.app.domains.artifacts.walkthrough_diff import parse_unified_diff
from backend.app.shared.errors import DomainError, InvalidWorkspaceError, PathEscapeError

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


class DirectoryEntry(BaseModel):
    name: str
    path: str
    is_git_repo: bool = False


class DirectoryBrowseResponse(BaseModel):
    current_path: str
    parent_path: str | None = None
    is_git_repo: bool = False
    branch: str = ""
    directories: list[DirectoryEntry] = Field(default_factory=list)


class WorkspaceInspector:
    """Inspects local repositories safely within canonical workspace boundaries."""

    async def validate_workspace(self, workspace_path: str) -> WorkspaceInfo:
        root = self._resolve_root(workspace_path)
        branch = await run_git(root, "rev-parse", "--abbrev-ref", "HEAD")
        is_git = (root / ".git").exists() or bool(
            await run_git(root, "rev-parse", "--is-inside-work-tree")
        )
        return WorkspaceInfo(
            path=str(root),
            name=root.name or str(root),
            is_git_repo=is_git,
            branch=branch if is_git else "",
        )

    async def browse_directories(
        self,
        workspace_path: str | None = None,
    ) -> DirectoryBrowseResponse:
        target = self._resolve_root(workspace_path) if workspace_path else Path.home().resolve()
        info = await self.validate_workspace(str(target))
        directories = await asyncio.to_thread(self._list_child_dirs, target)
        parent = str(target.parent) if target.parent != target else None
        return DirectoryBrowseResponse(
            current_path=str(target),
            parent_path=parent,
            is_git_repo=info.is_git_repo,
            branch=info.branch,
            directories=directories,
        )

    @staticmethod
    def _list_child_dirs(target: Path) -> list[DirectoryEntry]:
        entries: list[DirectoryEntry] = []
        try:
            children = sorted(target.iterdir(), key=lambda p: p.name.lower())
        except PermissionError:
            return []
        for child in children:
            if child.name.startswith(".") or child.name in _IGNORED_NAMES:
                continue
            try:
                if not child.is_dir():
                    continue
                resolved = child.resolve()
                is_git = (resolved / ".git").exists()
            except OSError:
                continue
            entries.append(
                DirectoryEntry(
                    name=child.name,
                    path=str(resolved),
                    is_git_repo=is_git,
                )
            )
        return entries

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
            await run_git(root, "rev-parse", "--is-inside-work-tree")
        )
        if not is_git:
            return WorkspaceDiffResponse(workspace_path=str(root))

        branch = await run_git(root, "rev-parse", "--abbrev-ref", "HEAD")
        git_head = await run_git(root, "rev-parse", "HEAD")
        raw_diff = await run_git(root, "diff", "--no-color", "HEAD")
        if not raw_diff:
            raw_diff = await run_git(root, "diff", "--no-color")

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
        status_raw = await run_git(root, "status", "--porcelain", "-uall")
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
            if not target.is_file():
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

    async def revert_diff(
        self,
        workspace_path: str,
        *,
        files: list[str] | None = None,
    ) -> WorkspaceDiffResponse:
        """Revert specific dirty/untracked files or the entire git working tree back to HEAD."""
        root = self._resolve_root(workspace_path)
        is_git = (root / ".git").exists() or (
            await run_git(root, "rev-parse", "--is-inside-work-tree") == "true"
        )
        if not is_git:
            raise DomainError(
                "Workspace is not a Git repository; use session turn revert instead.",
                status_code=400,
                code="GIT_REPO_REQUIRED",
            )

        if files is not None:
            for rel in files:
                target = self._resolve_inside(root, rel)
                if target == root:
                    raise PathEscapeError(rel)
                norm_rel = target.relative_to(root).as_posix()
                tracked_probe = await run_git(root, "ls-files", "--error-unmatch", "--", norm_rel)
                if tracked_probe:
                    await run_git(root, "checkout", "HEAD", "--", norm_rel)
                else:
                    if target.is_file() or target.is_symlink():
                        await asyncio.to_thread(target.unlink)
        else:
            status_raw = await run_git(root, "status", "--porcelain", "-uall")
            untracked: list[Path] = []
            has_tracked = False
            for line in status_raw.splitlines():
                if not line.strip() or len(line) < 4:
                    continue
                if line.startswith("?? "):
                    rel_untracked = line[3:].strip()
                    target = self._resolve_inside(root, rel_untracked)
                    if target != root:
                        untracked.append(target)
                else:
                    has_tracked = True
            if has_tracked:
                await run_git(root, "checkout", "HEAD", "--", ".")
            for target in untracked:
                if target.is_file() or target.is_symlink():
                    await asyncio.to_thread(target.unlink)

        return await self.get_diff(str(root))

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
