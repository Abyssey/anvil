"""Application runtime configuration."""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal, cast

APP_NAME: Final[str] = "Anvil"

SqliteJournalMode = Literal["WAL", "DELETE", "TRUNCATE", "PERSIST", "MEMORY", "OFF"]
VALID_SQLITE_JOURNAL_MODES: Final[frozenset[str]] = frozenset(
    {"WAL", "DELETE", "TRUNCATE", "PERSIST", "MEMORY", "OFF"}
)


def normalize_sqlite_journal_mode(raw: str | None) -> SqliteJournalMode:
    """Validate and normalize SQLITE_JOURNAL_MODE (defaults to WAL)."""
    if raw is None or not raw.strip():
        return "WAL"
    cleaned = raw.strip().upper()
    if cleaned not in VALID_SQLITE_JOURNAL_MODES:
        allowed = ", ".join(sorted(VALID_SQLITE_JOURNAL_MODES))
        raise RuntimeError(
            f"Unsupported SQLITE_JOURNAL_MODE='{raw}'. Expected one of: {allowed}."
        )
    return cast(SqliteJournalMode, cleaned)


@dataclass(frozen=True)
class AppConfig:
    """Immutable filesystem paths and runtime defaults."""

    data_dir: Path
    sqlite_path: Path
    artifacts_root: Path
    bundled_skills_dir: Path
    default_workspace_path: Path
    frontend_dist_dir: Path
    sqlite_journal_mode: SqliteJournalMode = "WAL"

    @classmethod
    def for_data_dir(
        cls,
        data_dir: Path,
        *,
        default_workspace_path: Path | None = None,
        bundled_skills_dir: Path | None = None,
        frontend_dist_dir: Path | None = None,
        sqlite_journal_mode: str | None = None,
    ) -> AppConfig:
        resolved_data = data_dir.expanduser().resolve()
        repo_root = Path(__file__).resolve().parents[3]
        default_bundled = Path(__file__).resolve().parents[1] / "domains" / "skills" / "builtin"
        skills_dir = bundled_skills_dir or default_bundled
        dist_dir = frontend_dist_dir or (repo_root / "frontend" / "dist")
        journal_mode = normalize_sqlite_journal_mode(sqlite_journal_mode)
        return cls(
            data_dir=resolved_data,
            sqlite_path=resolved_data / "sessions.db",
            artifacts_root=resolved_data / "artifacts",
            bundled_skills_dir=skills_dir.resolve(),
            default_workspace_path=(default_workspace_path or repo_root).resolve(),
            frontend_dist_dir=dist_dir.resolve(),
            sqlite_journal_mode=journal_mode,
        )

    @classmethod
    def from_repo_root(cls, repo_root: Path | None = None) -> AppConfig:
        root = (repo_root or Path(__file__).resolve().parents[3]).resolve()
        raw_data_dir = os.environ.get("DATA_DIR", "").strip()
        raw_journal_mode = os.environ.get("SQLITE_JOURNAL_MODE")
        target_data_dir = Path(raw_data_dir).expanduser() if raw_data_dir else (root / ".data")
        if not target_data_dir.is_absolute():
            target_data_dir = root / target_data_dir
        return cls.for_data_dir(
            target_data_dir,
            default_workspace_path=root,
            frontend_dist_dir=root / "frontend" / "dist",
            sqlite_journal_mode=raw_journal_mode,
        )

    @property
    def skills_root(self) -> Path:
        return self.data_dir / "skills"

    @property
    def memories_root(self) -> Path:
        return self.data_dir / "memories"

    @property
    def checkpoints_root(self) -> Path:
        return self.data_dir / "checkpoints"

    def ensure_directories(self) -> None:
        try:
            for directory in (
                self.data_dir,
                self.artifacts_root,
                self.skills_root,
                self.memories_root,
                self.checkpoints_root,
            ):
                directory.mkdir(parents=True, exist_ok=True)
                if not directory.is_dir():
                    raise RuntimeError(f"Storage path is not a directory: {directory}")
        except OSError as exc:
            raise RuntimeError(
                f"Failed to initialize DATA_DIR at '{self.data_dir}': {exc}"
            ) from exc

    def verify_storage_writable(self) -> None:
        """Fail fast at startup if DATA_DIR (e.g. NAS mount) is unreachable or read-only."""
        probe = self.data_dir / f".write-probe-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        try:
            fd = os.open(probe, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                os.write(fd, b"ok")
                os.fsync(fd)
            finally:
                os.close(fd)
            probe.unlink()
        except OSError as exc:
            raise RuntimeError(
                f"DATA_DIR '{self.data_dir}' is not writable: {exc}"
            ) from exc
