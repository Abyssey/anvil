"""Per-run execution catalog and async ContextVar registry (Open SWE RunContext pattern)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from backend.app.domains.agent.shell_guard import ShellGuard

if TYPE_CHECKING:
    from backend.app.domains.agent.checkpoints import CheckpointCapture
    from backend.app.domains.agent.middleware.git_evidence import GitEvidenceTracker
    from backend.app.domains.agent.middleware.verification import VerificationTracker


@dataclass(frozen=True)
class RunCatalog:
    """Immutable per-run execution context bound while a cached graph executes."""

    session_id: str
    workspace_path: Path
    mode: str
    artifacts_dir: Path
    shell_guard: ShellGuard
    project_id: str = ""
    project_memory_dir: Path | None = None
    secret_values: tuple[str, ...] = ()
    extra_skills_dirs: tuple[Path, ...] = ()
    git_tracker: GitEvidenceTracker | None = field(default=None, compare=False)
    checkpoint_capture: CheckpointCapture | None = field(default=None, compare=False)
    verification_tracker: VerificationTracker | None = field(default=None, compare=False)


_CURRENT_RUN: ContextVar[RunCatalog | None] = ContextVar("abyss_current_run", default=None)


class RunContext:
    """Binds and reads the active RunCatalog safely across async tasks."""

    @staticmethod
    @asynccontextmanager
    async def activate(catalog: RunCatalog) -> AsyncIterator[RunCatalog]:
        token = _CURRENT_RUN.set(catalog)
        try:
            yield catalog
        finally:
            _CURRENT_RUN.reset(token)

    @staticmethod
    def current() -> RunCatalog:
        catalog = _CURRENT_RUN.get()
        if catalog is None:
            raise RuntimeError("No active RunCatalog bound in RunContext")
        return catalog

    @staticmethod
    def current_or_none() -> RunCatalog | None:
        return _CURRENT_RUN.get()
