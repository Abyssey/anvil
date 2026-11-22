"""Read-only virtual SkillFiles backend aggregating bundled and workspace skills per run."""

from __future__ import annotations

from pathlib import Path, PurePosixPath

from deepagents.backends.protocol import (
    BackendProtocol,
    DeleteResult,
    EditResult,
    FileDownloadResponse,
    FileInfo,
    FileUploadResponse,
    GlobResult,
    GrepMatch,
    GrepResult,
    LsResult,
    ReadResult,
    WriteResult,
)
from deepagents.backends.utils import create_file_data, file_data_to_string, slice_read_response

from backend.app.domains.agent.run_context import RunContext

_READ_ONLY_ERROR = "Skills storage is read-only."


class SkillFiles(BackendProtocol):
    """Virtual read-only filesystem exposing bundled + target-workspace skills."""

    def __init__(self, bundled_skills_dir: Path) -> None:
        self._bundled_skills_dir = bundled_skills_dir

    def _skill_roots(self) -> list[Path]:
        roots: list[Path] = []
        if self._bundled_skills_dir.is_dir():
            roots.append(self._bundled_skills_dir)
        catalog = RunContext.current_or_none()
        if catalog is not None:
            for candidate in (
                catalog.workspace_path / ".agents" / "skills",
                catalog.workspace_path / ".claude" / "skills",
                *catalog.extra_skills_dirs,
            ):
                if candidate.is_dir() and candidate not in roots:
                    roots.append(candidate)
        return roots

    def _collect_skills(self) -> dict[str, Path]:
        """Map skill_name -> local directory path (workspace overrides bundled)."""
        discovered: dict[str, Path] = {}
        for root in self._skill_roots():
            for entry in sorted(root.iterdir()):
                if entry.is_dir() and (entry / "SKILL.md").is_file():
                    discovered[entry.name] = entry
        return discovered

    def _resolve_virtual_path(self, path: str) -> Path | None:
        normalized = PurePosixPath("/" + path.lstrip("/"))
        parts = [p for p in normalized.parts if p != "/"]
        if parts and parts[0] == "skills":
            parts = parts[1:]
        if not parts:
            return None
        skills = self._collect_skills()
        skill_dir = skills.get(parts[0])
        if skill_dir is None:
            return None
        rel = PurePosixPath(*parts[1:]) if len(parts) > 1 else PurePosixPath(".")
        target = (skill_dir / str(rel)).resolve()
        try:
            target.relative_to(skill_dir.resolve())
        except ValueError:
            return None
        return target

    def ls(self, path: str) -> LsResult:
        normalized = "/" + path.strip("/")
        if normalized in {"/", "/skills"}:
            skills = self._collect_skills()
            entries: list[FileInfo] = [
                {"path": f"/{name}", "is_dir": True} for name in sorted(skills)
            ]
            return LsResult(entries=entries)

        resolved = self._resolve_virtual_path(path)
        if resolved is None or not resolved.exists() or not resolved.is_dir():
            return LsResult(entries=[])

        entries = []
        for item in sorted(resolved.iterdir()):
            rel_prefix = normalized.rstrip("/")
            entries.append(
                {
                    "path": f"{rel_prefix}/{item.name}",
                    "is_dir": item.is_dir(),
                    "size": item.stat().st_size if item.is_file() else 0,
                }
            )
        return LsResult(entries=entries)

    async def als(self, path: str) -> LsResult:
        return self.ls(path)

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        resolved = self._resolve_virtual_path(file_path)
        if resolved is None or not resolved.is_file():
            return ReadResult(error=f"File '{file_path}' not found")
        text = resolved.read_text(encoding="utf-8")
        return slice_read_response(create_file_data(text), offset, limit)

    async def aread(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        return self.read(file_path, offset=offset, limit=limit)

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        responses: list[FileDownloadResponse] = []
        for path in paths:
            resolved = self._resolve_virtual_path(path)
            if resolved is None or not resolved.is_file():
                responses.append(FileDownloadResponse(path=path, error="file_not_found"))
                continue
            responses.append(FileDownloadResponse(path=path, content=resolved.read_bytes()))
        return responses

    async def adownload_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        return self.download_files(paths)

    def glob(self, pattern: str, path: str = "/") -> GlobResult:
        skills = self._collect_skills()
        matches: list[FileInfo] = []
        clean_pat = pattern.lstrip("/")
        for skill_name, skill_dir in sorted(skills.items()):
            for candidate in sorted(skill_dir.rglob("*")):
                if candidate.is_file():
                    rel = f"{skill_name}/{candidate.relative_to(skill_dir).as_posix()}"
                    if PurePosixPath(rel).match(clean_pat) or clean_pat in {"*", "**", "**/*"}:
                        matches.append(
                            {
                                "path": f"/{rel}",
                                "is_dir": False,
                                "size": candidate.stat().st_size,
                            }
                        )
        return GlobResult(matches=matches)

    async def aglob(self, pattern: str, path: str = "/") -> GlobResult:
        return self.glob(pattern, path=path)

    def grep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
    ) -> GrepResult:
        matches: list[GrepMatch] = []
        for skill_name, skill_dir in sorted(self._collect_skills().items()):
            for candidate in sorted(skill_dir.rglob("*.md")):
                content = candidate.read_text(encoding="utf-8", errors="replace")
                for line_no, line in enumerate(content.splitlines(), start=1):
                    if pattern in line:
                        rel = f"/{skill_name}/{candidate.relative_to(skill_dir).as_posix()}"
                        matches.append({"path": rel, "line": line_no, "text": line})
        return GrepResult(matches=matches)

    async def agrep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
    ) -> GrepResult:
        return self.grep(pattern, path=path, glob=glob)

    def write(self, file_path: str, content: str) -> WriteResult:
        return WriteResult(error=_READ_ONLY_ERROR)

    async def awrite(self, file_path: str, content: str) -> WriteResult:
        return WriteResult(error=_READ_ONLY_ERROR)

    def edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        return EditResult(error=_READ_ONLY_ERROR)

    async def aedit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        return EditResult(error=_READ_ONLY_ERROR)

    def delete(self, file_path: str) -> DeleteResult:
        return DeleteResult(error=_READ_ONLY_ERROR)

    async def adelete(self, file_path: str) -> DeleteResult:
        return DeleteResult(error=_READ_ONLY_ERROR)

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        return [FileUploadResponse(path=p, error="permission_denied") for p, _ in files]

    async def aupload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        return self.upload_files(files)


def read_skill_file_text(skill_path: Path) -> str:
    return file_data_to_string(create_file_data(skill_path.read_text(encoding="utf-8")))
