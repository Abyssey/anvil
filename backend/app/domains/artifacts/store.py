"""Isolated per-session ArtifactStore and safe workspace export."""

from __future__ import annotations

import shutil
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from backend.app.domains.artifacts.entities import (
    ArtifactDoc,
    ArtifactKind,
    FileDiff,
    FileHunks,
    WalkthroughChunk,
    WalkthroughPlan,
)
from backend.app.domains.artifacts.walkthrough_diff import (
    ParsedFileDiff,
    ParsedHunk,
    build_default_walkthrough_plan,
)
from backend.app.shared.errors import ArtifactNotFoundError, InvalidWorkspaceError, PathEscapeError

_KIND_TO_FILENAME: dict[ArtifactKind, tuple[str, str]] = {
    ArtifactKind.IMPLEMENTATION_PLAN: ("implementation_plan.md", "Implementation Plan"),
    ArtifactKind.TASK: ("task.md", "Task Checklist"),
    ArtifactKind.WALKTHROUGH: ("walkthrough.md", "Walkthrough"),
}


class ArtifactStore:
    """Manages isolated per-session Antigravity artifacts outside the target repository."""

    def __init__(self, artifacts_root: Path) -> None:
        self._root = artifacts_root.resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    def session_dir(self, session_id: str) -> Path:
        safe_id = Path(session_id).name
        target = (self._root / safe_id).resolve()
        try:
            target.relative_to(self._root)
        except ValueError as exc:
            raise PathEscapeError(session_id) from exc
        target.mkdir(parents=True, exist_ok=True)
        return target

    def write_artifact(self, session_id: str, kind: ArtifactKind, content: str) -> ArtifactDoc:
        filename, title = _KIND_TO_FILENAME[kind]
        path = self.session_dir(session_id) / filename
        path.write_text(content, encoding="utf-8")
        stat = path.stat()
        return ArtifactDoc(
            kind=kind,
            title=title,
            content=content,
            updated_at=datetime.fromtimestamp(stat.st_mtime, tz=UTC),
        )

    def get_artifact(self, session_id: str, kind: ArtifactKind) -> ArtifactDoc:
        filename, title = _KIND_TO_FILENAME[kind]
        path = self.session_dir(session_id) / filename
        if not path.is_file():
            raise ArtifactNotFoundError(kind.value)
        stat = path.stat()
        return ArtifactDoc(
            kind=kind,
            title=title,
            content=path.read_text(encoding="utf-8", errors="replace"),
            updated_at=datetime.fromtimestamp(stat.st_mtime, tz=UTC),
        )

    def list_artifacts(self, session_id: str) -> list[ArtifactDoc]:
        sdir = self.session_dir(session_id)
        docs: list[ArtifactDoc] = []
        for kind, (filename, title) in _KIND_TO_FILENAME.items():
            path = sdir / filename
            if path.is_file():
                stat = path.stat()
                docs.append(
                    ArtifactDoc(
                        kind=kind,
                        title=title,
                        content=path.read_text(encoding="utf-8", errors="replace"),
                        updated_at=datetime.fromtimestamp(stat.st_mtime, tz=UTC),
                    )
                )
        return docs

    def snapshot_mtimes(self, session_id: str) -> dict[ArtifactKind, float]:
        sdir = self.session_dir(session_id)
        mtimes: dict[ArtifactKind, float] = {}
        for kind, (filename, _) in _KIND_TO_FILENAME.items():
            path = sdir / filename
            if path.is_file():
                mtimes[kind] = path.stat().st_mtime_ns
        return mtimes

    def save_walkthrough_plan(self, session_id: str, plan: WalkthroughPlan) -> None:
        path = self.session_dir(session_id) / "walkthrough_plan.json"
        path.write_text(plan.model_dump_json(indent=2), encoding="utf-8")

    def get_walkthrough_plan(self, session_id: str) -> WalkthroughPlan | None:
        path = self.session_dir(session_id) / "walkthrough_plan.json"
        if not path.is_file():
            return None
        return WalkthroughPlan.model_validate_json(path.read_text(encoding="utf-8"))

    def build_and_save_walkthrough_plan(
        self,
        session_id: str,
        file_diffs: Sequence[FileDiff],
        *,
        git_head: str = "",
    ) -> WalkthroughPlan:
        parsed_diffs = [
            ParsedFileDiff(
                path=f.path,
                status=f.status,
                hunks=[
                    ParsedHunk(
                        path=h.path,
                        name=h.name,
                        old_start=h.old_start,
                        old_count=h.old_count,
                        new_start=h.new_start,
                        new_count=h.new_count,
                        header=h.header,
                        lines=tuple(h.lines),
                    )
                    for h in f.hunks
                ],
            )
            for f in file_diffs
        ]
        parsed = build_default_walkthrough_plan(parsed_diffs, git_head=git_head)
        plan = WalkthroughPlan(
            git_head=parsed.git_head,
            chunks=[
                WalkthroughChunk(
                    id=c.id,
                    title=c.title,
                    explanation=c.explanation,
                    file_hunks=[
                        FileHunks(path=path, hunks=hunks) for path, hunks in c.file_hunks.items()
                    ],
                )
                for c in parsed.chunks
            ],
            other_files=parsed.other_files,
        )
        self.save_walkthrough_plan(session_id, plan)
        return plan

    def clear_walkthrough_plan(self, session_id: str) -> None:
        path = self.session_dir(session_id) / "walkthrough_plan.json"
        if path.is_file():
            path.unlink()

    def delete_session_artifacts(self, session_id: str) -> None:
        safe_id = Path(session_id).name
        target = (self._root / safe_id).resolve()
        try:
            target.relative_to(self._root)
        except ValueError as exc:
            raise PathEscapeError(session_id) from exc
        if target != self._root and target.is_dir():
            shutil.rmtree(target)

    def save_to_workspace(
        self,
        session_id: str,
        kind: ArtifactKind,
        *,
        workspace_path: str,
        relative_path: str,
    ) -> Path:
        doc = self.get_artifact(session_id, kind)
        root = Path(workspace_path).expanduser().resolve()
        if not root.exists() or not root.is_dir():
            raise InvalidWorkspaceError(workspace_path)
        clean_rel = relative_path.strip().lstrip("/")
        if not clean_rel:
            raise PathEscapeError(relative_path)
        target = (root / clean_rel).resolve()
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise PathEscapeError(relative_path) from exc
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(doc.content, encoding="utf-8")
        return target

    @staticmethod
    def render_todos_markdown(todos: list[dict[str, object]]) -> str:
        lines = ["# Tasks", ""]
        for item in todos:
            content = str(item.get("content") or "").strip()
            status = str(item.get("status") or "pending").lower()
            if status == "completed":
                lines.append(f"- [x] {content}")
            elif status == "in_progress":
                lines.append(f"- [ ] **(In Progress)** {content}")
            else:
                lines.append(f"- [ ] {content}")
        return "\n".join(lines).rstrip() + "\n"
