"""Application runtime configuration."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AppConfig:
    """Immutable filesystem paths and runtime defaults for Abyss v2."""

    data_dir: Path
    sqlite_path: Path
    artifacts_root: Path
    bundled_skills_dir: Path
    default_workspace_path: Path
    frontend_dist_dir: Path

    @classmethod
    def for_data_dir(
        cls,
        data_dir: Path,
        *,
        default_workspace_path: Path | None = None,
        bundled_skills_dir: Path | None = None,
        frontend_dist_dir: Path | None = None,
    ) -> AppConfig:
        resolved_data = data_dir.resolve()
        repo_root = Path(__file__).resolve().parents[3]
        default_bundled = Path(__file__).resolve().parents[1] / "domains" / "skills" / "builtin"
        skills_dir = bundled_skills_dir or default_bundled
        dist_dir = frontend_dist_dir or (repo_root / "frontend" / "dist")
        return cls(
            data_dir=resolved_data,
            sqlite_path=resolved_data / "abyss.db",
            artifacts_root=resolved_data / "artifacts",
            bundled_skills_dir=skills_dir.resolve(),
            default_workspace_path=(default_workspace_path or repo_root).resolve(),
            frontend_dist_dir=dist_dir.resolve(),
        )

    @classmethod
    def from_repo_root(cls, repo_root: Path | None = None) -> AppConfig:
        root = (repo_root or Path(__file__).resolve().parents[3]).resolve()
        return cls.for_data_dir(
            root / ".abyss-data",
            default_workspace_path=root,
            frontend_dist_dir=root / "frontend" / "dist",
        )

    @property
    def skills_root(self) -> Path:
        return self.data_dir / "skills"

    @property
    def memories_root(self) -> Path:
        return self.data_dir / "memories"

    def ensure_directories(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.artifacts_root.mkdir(parents=True, exist_ok=True)
        self.skills_root.mkdir(parents=True, exist_ok=True)
        self.memories_root.mkdir(parents=True, exist_ok=True)
