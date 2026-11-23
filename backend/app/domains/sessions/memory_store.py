"""Project-scoped long-term memory file store (.data/memories/projects/{project_id}/)."""

from __future__ import annotations

import os
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path

from backend.app.shared.errors import DomainError, PathEscapeError

MAX_PROJECT_MEMORY_BYTES = 32 * 1024
_MEMORY_FILENAME = "AGENTS.md"


class ProjectMemoryStore:
    """Manages isolated per-project long-term Markdown memory files safely outside target repos."""

    def __init__(self, memories_root: Path) -> None:
        self._projects_root = (memories_root.resolve() / "projects").resolve()

    def project_dir(self, project_id: str) -> Path:
        raw = project_id.strip()
        if not raw or "/" in raw or "\\" in raw or raw in {".", ".."}:
            raise PathEscapeError(project_id)
        safe_id = Path(raw).name
        target = (self._projects_root / safe_id).resolve()
        try:
            target.relative_to(self._projects_root)
        except ValueError as exc:
            raise PathEscapeError(project_id) from exc
        if target == self._projects_root:
            raise PathEscapeError(project_id)
        return target

    def memory_file(self, project_id: str) -> Path:
        return self.project_dir(project_id) / _MEMORY_FILENAME

    def read(self, project_id: str) -> tuple[str, datetime | None]:
        path = self.memory_file(project_id)
        if not path.is_file():
            return "", None
        stat = path.stat()
        text = path.read_text(encoding="utf-8", errors="replace")
        return text, datetime.fromtimestamp(stat.st_mtime, tz=UTC)

    def write(self, project_id: str, content: str) -> tuple[str, datetime]:
        encoded = content.encode("utf-8")
        if len(encoded) > MAX_PROJECT_MEMORY_BYTES:
            raise DomainError(
                f"Project memory exceeds maximum size of {MAX_PROJECT_MEMORY_BYTES} bytes.",
                status_code=400,
                code="PROJECT_MEMORY_TOO_LARGE",
                details={
                    "max_bytes": MAX_PROJECT_MEMORY_BYTES,
                    "actual_bytes": len(encoded),
                },
            )
        target = self.memory_file(project_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = target.parent / f"{_MEMORY_FILENAME}.tmp.{uuid.uuid4().hex[:8]}"
        try:
            tmp_path.write_bytes(encoded)
            os.replace(tmp_path, target)
        finally:
            if tmp_path.exists():
                tmp_path.unlink(missing_ok=True)
        stat = target.stat()
        return content, datetime.fromtimestamp(stat.st_mtime, tz=UTC)

    def clear(self, project_id: str) -> None:
        path = self.memory_file(project_id)
        if path.is_file():
            path.unlink()

    def delete_project_dir(self, project_id: str) -> None:
        target = self.project_dir(project_id)
        if target != self._projects_root and target.is_dir():
            shutil.rmtree(target)
