"""Mode-scoped DynamicRunBackend and GuardedLocalShellBackend for DeepAgents."""

from __future__ import annotations

import os
from pathlib import Path

from deepagents.backends import CompositeBackend, FilesystemBackend, LocalShellBackend
from deepagents.backends.protocol import (
    BackendProtocol,
    DeleteResult,
    EditResult,
    ExecuteResponse,
    FileDownloadResponse,
    FileUploadResponse,
    GlobResult,
    GrepResult,
    LsResult,
    ReadResult,
    SandboxBackendProtocol,
    WriteResult,
)

from backend.app.domains.agent.run_context import RunCatalog, RunContext
from backend.app.domains.agent.shell_guard import ShellAction
from backend.app.domains.agent.skills import SkillFiles


def _safe_shell_env() -> dict[str, str]:
    path_val = os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin")
    local_bin = str(Path.home() / ".local" / "bin")
    if local_bin not in path_val.split(":"):
        path_val = f"{local_bin}:{path_val}"
    return {
        "PATH": path_val,
        "HOME": os.environ.get("HOME", ""),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
    }


def _is_artifacts_path(path: str) -> bool:
    clean = "/" + path.strip().lstrip("/")
    return clean == "/artifacts" or clean.startswith("/artifacts/")


class DynamicRunBackend(BackendProtocol):
    """Routes file calls to the RunContext-bound CompositeBackend for the active session."""

    def __init__(self, bundled_skills_dir: Path, *, read_only_workspace: bool = False) -> None:
        self._bundled_skills_dir = bundled_skills_dir
        self._read_only_workspace = read_only_workspace

    def _catalog(self) -> RunCatalog:
        return RunContext.current()

    def _default_backend(self, catalog: RunCatalog) -> BackendProtocol:
        return FilesystemBackend(root_dir=catalog.workspace_path, virtual_mode=True)

    def _composite(self) -> tuple[RunCatalog, CompositeBackend]:
        catalog = self._catalog()
        catalog.artifacts_dir.mkdir(parents=True, exist_ok=True)
        composite = CompositeBackend(
            default=self._default_backend(catalog),
            routes={
                "/artifacts/": FilesystemBackend(root_dir=catalog.artifacts_dir, virtual_mode=True),
                "/skills/": SkillFiles(self._bundled_skills_dir),
            },
        )
        return catalog, composite

    def ls(self, path: str) -> LsResult:
        _, comp = self._composite()
        return comp.ls(path)

    async def als(self, path: str) -> LsResult:
        _, comp = self._composite()
        return await comp.als(path)

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        _, comp = self._composite()
        return comp.read(file_path, offset=offset, limit=limit)

    async def aread(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        _, comp = self._composite()
        return await comp.aread(file_path, offset=offset, limit=limit)

    def write(self, file_path: str, content: str) -> WriteResult:
        if self._read_only_workspace and not _is_artifacts_path(file_path):
            return WriteResult(error="Workspace is read-only in Ask mode.")
        _, comp = self._composite()
        return comp.write(file_path, content)

    async def awrite(self, file_path: str, content: str) -> WriteResult:
        if self._read_only_workspace and not _is_artifacts_path(file_path):
            return WriteResult(error="Workspace is read-only in Ask mode.")
        _, comp = self._composite()
        return await comp.awrite(file_path, content)

    def edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        if self._read_only_workspace and not _is_artifacts_path(file_path):
            return EditResult(error="Workspace is read-only in Ask mode.")
        _, comp = self._composite()
        return comp.edit(file_path, old_string, new_string, replace_all=replace_all)

    async def aedit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        if self._read_only_workspace and not _is_artifacts_path(file_path):
            return EditResult(error="Workspace is read-only in Ask mode.")
        _, comp = self._composite()
        return await comp.aedit(file_path, old_string, new_string, replace_all=replace_all)

    def delete(self, file_path: str) -> DeleteResult:
        if self._read_only_workspace and not _is_artifacts_path(file_path):
            return DeleteResult(error="Workspace is read-only in Ask mode.")
        _, comp = self._composite()
        return comp.delete(file_path)

    async def adelete(self, file_path: str) -> DeleteResult:
        if self._read_only_workspace and not _is_artifacts_path(file_path):
            return DeleteResult(error="Workspace is read-only in Ask mode.")
        _, comp = self._composite()
        return await comp.adelete(file_path)

    def glob(self, pattern: str, path: str = "/") -> GlobResult:
        _, comp = self._composite()
        return comp.glob(pattern, path=path)

    async def aglob(self, pattern: str, path: str = "/") -> GlobResult:
        _, comp = self._composite()
        return await comp.aglob(pattern, path=path)

    def grep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
    ) -> GrepResult:
        _, comp = self._composite()
        return comp.grep(pattern, path=path, glob=glob)

    async def agrep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
    ) -> GrepResult:
        _, comp = self._composite()
        return await comp.agrep(pattern, path=path, glob=glob)

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        if self._read_only_workspace:
            blocked: list[FileUploadResponse] = []
            allowed: list[tuple[str, bytes]] = []
            for p, content in files:
                if _is_artifacts_path(p):
                    allowed.append((p, content))
                else:
                    blocked.append(FileUploadResponse(path=p, error="permission_denied"))
            if not allowed:
                return blocked
            _, comp = self._composite()
            return blocked + comp.upload_files(allowed)
        _, comp = self._composite()
        return comp.upload_files(files)

    async def aupload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        if self._read_only_workspace:
            blocked: list[FileUploadResponse] = []
            allowed: list[tuple[str, bytes]] = []
            for p, content in files:
                if _is_artifacts_path(p):
                    allowed.append((p, content))
                else:
                    blocked.append(FileUploadResponse(path=p, error="permission_denied"))
            if not allowed:
                return blocked
            _, comp = self._composite()
            return blocked + await comp.aupload_files(allowed)
        _, comp = self._composite()
        return await comp.aupload_files(files)

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        _, comp = self._composite()
        return comp.download_files(paths)

    async def adownload_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        _, comp = self._composite()
        return await comp.adownload_files(paths)


class DynamicGuardedShellBackend(DynamicRunBackend, SandboxBackendProtocol):
    """Coding-mode backend adding ShellGuard-enforced LocalShellBackend execution."""

    def __init__(self, bundled_skills_dir: Path) -> None:
        super().__init__(bundled_skills_dir, read_only_workspace=False)

    @property
    def id(self) -> str:
        catalog = RunContext.current_or_none()
        return f"abyss-local-shell:{catalog.session_id if catalog else 'unbound'}"

    def _default_backend(self, catalog: RunCatalog) -> BackendProtocol:
        return LocalShellBackend(
            root_dir=catalog.workspace_path,
            virtual_mode=True,
            timeout=120,
            max_output_bytes=100_000,
            env=_safe_shell_env(),
            inherit_env=False,
        )

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        catalog, comp = self._composite()
        verdict = catalog.shell_guard.evaluate(command, catalog.mode)
        if verdict.action == ShellAction.BLOCK:
            return ExecuteResponse(output=f"Command blocked: {verdict.reason}", exit_code=126)
        res = comp.execute(command, timeout=timeout)
        cleaned = catalog.shell_guard.scrub_output(res.output, extra_secrets=catalog.secret_values)
        return ExecuteResponse(
            output=cleaned,
            exit_code=res.exit_code,
            truncated=res.truncated,
        )

    async def aexecute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        catalog, comp = self._composite()
        verdict = catalog.shell_guard.evaluate(command, catalog.mode)
        if verdict.action == ShellAction.BLOCK:
            return ExecuteResponse(output=f"Command blocked: {verdict.reason}", exit_code=126)
        res = await comp.aexecute(command, timeout=timeout)
        cleaned = catalog.shell_guard.scrub_output(res.output, extra_secrets=catalog.secret_values)
        return ExecuteResponse(
            output=cleaned,
            exit_code=res.exit_code,
            truncated=res.truncated,
        )
