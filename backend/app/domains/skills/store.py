"""SkillStore: manages bundled, global, and workspace skill lifecycles atomically."""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path

from backend.app.domains.skills.entities import (
    CatalogTab,
    InstalledSkill,
    InstalledSkillDetail,
    RemoteSkillSummary,
    SkillCatalogPage,
    SkillInstallRequest,
    SkillPreview,
    SkillScope,
)
from backend.app.domains.skills.frontmatter import (
    parse_skill_frontmatter,
    validate_skill_name,
)
from backend.app.domains.skills.registry_client import (
    ExtractedSkillPackage,
    SkillsRegistryProtocol,
    normalize_skill_source_input,
)
from backend.app.shared.errors import DomainError, InvalidWorkspaceError, PathEscapeError

logger = logging.getLogger(__name__)

PROVENANCE_FILENAME = ".skill-meta.json"


def _read_provenance(skill_dir: Path) -> dict[str, object]:
    prov_path = skill_dir / PROVENANCE_FILENAME
    if not prov_path.is_file():
        return {}
    try:
        raw = json.loads(prov_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _parse_iso_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _validate_workspace_dir(workspace_path: str | None) -> Path:
    if workspace_path is None or not workspace_path.strip():
        raise InvalidWorkspaceError(workspace_path or "", "Workspace path is required")
    resolved = Path(workspace_path).expanduser().resolve()
    if not resolved.is_dir():
        raise InvalidWorkspaceError(str(resolved))
    return resolved


def _read_installed_entry(
    skill_dir: Path,
    *,
    scope: SkillScope,
    source_label: str,
    deletable: bool,
) -> InstalledSkill | None:
    if skill_dir.is_symlink() or not skill_dir.is_dir():
        return None
    skill_md_path = skill_dir / "SKILL.md"
    if not skill_md_path.is_file():
        return None
    try:
        raw_text = skill_md_path.read_text(encoding="utf-8")
        parsed = parse_skill_frontmatter(raw_text, strict_name=False)
    except (OSError, ValueError) as exc:
        logger.warning("Skipping unreadable skill at %s: %s", skill_md_path, exc)
        return None

    prov = _read_provenance(skill_dir)
    origin_source = str(prov["source"]) if isinstance(prov.get("source"), str) else None
    origin_skill_id = str(prov["skill_id"]) if isinstance(prov.get("skill_id"), str) else None
    origin_url = str(prov["url"]) if isinstance(prov.get("url"), str) else None
    installed_at = _parse_iso_datetime(prov.get("installed_at"))

    return InstalledSkill(
        name=parsed.name,
        description=parsed.description,
        scope=scope,
        source_label=source_label,
        path=f"/skills/{skill_dir.name}/SKILL.md",
        deletable=deletable,
        license=parsed.license,
        compatibility=parsed.compatibility,
        origin_source=origin_source,
        origin_skill_id=origin_skill_id,
        origin_url=origin_url,
        installed_at=installed_at,
    )


def _write_package_atomically(
    target_root: Path,
    pkg: ExtractedSkillPackage,
    *,
    scope: SkillScope,
    source_label: str,
) -> InstalledSkill:
    skill_name = validate_skill_name(pkg.name)
    resolved_root = target_root.resolve()
    resolved_root.mkdir(parents=True, exist_ok=True)

    target_dir = (resolved_root / skill_name).resolve()
    if target_dir.parent != resolved_root:
        raise PathEscapeError(skill_name)
    if target_dir.is_symlink():
        raise DomainError(
            "Refusing to overwrite a symlinked skill directory.",
            status_code=403,
            code="UNSAFE_SKILL_PATH",
        )

    now = datetime.now(UTC)
    provenance = {
        "source": pkg.source,
        "skill_id": pkg.skill_id,
        "name": skill_name,
        "scope": scope,
        "installed_at": now.isoformat(),
        "url": f"https://skills.sh/{pkg.source}/{pkg.skill_id}",
    }

    tmp_dir = resolved_root / f".tmp-{skill_name}-{uuid.uuid4().hex[:8]}"
    backup_dir: Path | None = None
    try:
        tmp_dir.mkdir(parents=True, exist_ok=False)
        resolved_tmp = tmp_dir.resolve()
        for rel_path, content in pkg.files.items():
            dest = (resolved_tmp / rel_path).resolve()
            try:
                dest.relative_to(resolved_tmp)
            except ValueError as exc:
                raise PathEscapeError(rel_path) from exc
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(content)

        (resolved_tmp / PROVENANCE_FILENAME).write_text(
            json.dumps(provenance, indent=2) + "\n",
            encoding="utf-8",
        )

        if target_dir.exists():
            backup_dir = resolved_root / f".bak-{skill_name}-{uuid.uuid4().hex[:8]}"
            target_dir.rename(backup_dir)
            try:
                tmp_dir.rename(target_dir)
            except Exception:
                backup_dir.rename(target_dir)
                raise
            shutil.rmtree(backup_dir, ignore_errors=True)
            backup_dir = None
        else:
            tmp_dir.rename(target_dir)
    except Exception:
        if tmp_dir.exists():
            shutil.rmtree(tmp_dir, ignore_errors=True)
        if backup_dir is not None and backup_dir.exists() and not target_dir.exists():
            backup_dir.rename(target_dir)
        raise

    return InstalledSkill(
        name=skill_name,
        description=pkg.description,
        scope=scope,
        source_label=source_label,
        path=f"/skills/{skill_name}/SKILL.md",
        deletable=True,
        license=pkg.license,
        compatibility=pkg.compatibility,
        origin_source=pkg.source,
        origin_skill_id=pkg.skill_id,
        origin_url=provenance["url"],
        installed_at=now,
    )


class SkillStore:
    """Manages bundled, global, and workspace skills plus skills.sh catalog operations."""

    def __init__(
        self,
        *,
        bundled_dir: Path,
        global_dir: Path,
        registry_client: SkillsRegistryProtocol,
    ) -> None:
        self._bundled_dir = bundled_dir.resolve()
        self._global_dir = global_dir.resolve()
        self._registry = registry_client

    def _scan_root(
        self,
        root: Path,
        *,
        scope: SkillScope,
        source_label: str,
        deletable: bool,
    ) -> list[InstalledSkill]:
        if not root.is_dir():
            return []
        entries: list[InstalledSkill] = []
        for child in sorted(root.iterdir(), key=lambda p: p.name):
            if child.name.startswith("."):
                continue
            item = _read_installed_entry(
                child,
                scope=scope,
                source_label=source_label,
                deletable=deletable,
            )
            if item is not None:
                entries.append(item)
        return entries

    def list_installed(self, workspace_path: str | None = None) -> list[InstalledSkill]:
        """List all installed skills across bundled, global, and optional workspace scopes."""
        results: list[InstalledSkill] = []
        results.extend(
            self._scan_root(
                self._bundled_dir,
                scope="bundled",
                source_label="bundled",
                deletable=False,
            )
        )
        results.extend(
            self._scan_root(
                self._global_dir,
                scope="global",
                source_label="global",
                deletable=True,
            )
        )
        if workspace_path is not None and workspace_path.strip():
            ws = Path(workspace_path).expanduser().resolve()
            if ws.is_dir():
                results.extend(
                    self._scan_root(
                        ws / ".agents" / "skills",
                        scope="workspace",
                        source_label=".agents/skills",
                        deletable=True,
                    )
                )
                results.extend(
                    self._scan_root(
                        ws / ".claude" / "skills",
                        scope="workspace",
                        source_label=".claude/skills",
                        deletable=False,
                    )
                )
        return results

    def get_installed_detail(
        self,
        scope: SkillScope,
        name: str,
        workspace_path: str | None = None,
    ) -> InstalledSkillDetail:
        """Read full SKILL.md content and relative files for a specific installed skill."""
        try:
            safe_name = validate_skill_name(name)
        except ValueError as exc:
            raise DomainError(str(exc), status_code=400, code="INVALID_SKILL_NAME") from exc

        candidates: list[tuple[Path, str, bool]] = []
        if scope == "bundled":
            candidates.append((self._bundled_dir, "bundled", False))
        elif scope == "global":
            candidates.append((self._global_dir, "global", True))
        elif scope == "workspace":
            ws = _validate_workspace_dir(workspace_path)
            candidates.append((ws / ".agents" / "skills", ".agents/skills", True))
            candidates.append((ws / ".claude" / "skills", ".claude/skills", False))

        for root, source_label, deletable in candidates:
            if not root.is_dir():
                continue
            resolved_root = root.resolve()
            target = resolved_root / safe_name
            if target.is_symlink():
                continue
            resolved_target = target.resolve()
            if resolved_target.parent != resolved_root or not resolved_target.is_dir():
                continue
            skill_md_path = resolved_target / "SKILL.md"
            if not skill_md_path.is_file():
                continue
            summary = _read_installed_entry(
                resolved_target,
                scope=scope,
                source_label=source_label,
                deletable=deletable,
            )
            if summary is None:
                continue
            content = skill_md_path.read_text(encoding="utf-8", errors="replace")
            files = sorted(
                p.relative_to(resolved_target).as_posix()
                for p in resolved_target.rglob("*")
                if p.is_file() and not p.is_symlink() and p.name != PROVENANCE_FILENAME
            )
            return InstalledSkillDetail(
                **summary.model_dump(),
                content=content,
                files=files,
            )

        raise DomainError(
            f"Skill '{safe_name}' not found in scope '{scope}'.",
            status_code=404,
            code="SKILL_NOT_FOUND",
        )

    async def browse_catalog(
        self,
        tab: CatalogTab = "all-time",
        page: int = 0,
    ) -> SkillCatalogPage:
        return await self._registry.browse(tab=tab, page=page)

    async def search_catalog(
        self,
        query: str,
        limit: int = 20,
    ) -> list[RemoteSkillSummary]:
        return await self._registry.search(query=query, limit=limit)

    async def preview_remote(
        self,
        source: str,
        skill_id: str | None = None,
    ) -> SkillPreview:
        norm_source, norm_skill_id = normalize_skill_source_input(source, skill_id)
        return await self._registry.preview_remote_skill(
            source=norm_source,
            skill_id=norm_skill_id,
        )

    async def install(self, req: SkillInstallRequest) -> InstalledSkill:
        """Download, validate, and atomically install a skill into global or workspace scope."""
        if req.scope == "workspace":
            ws = _validate_workspace_dir(req.workspace_path)
            target_root = ws / ".agents" / "skills"
            source_label = ".agents/skills"
        else:
            target_root = self._global_dir
            source_label = "global"

        norm_source, norm_skill_id = normalize_skill_source_input(req.source, req.skill_id)
        pkg = await self._registry.fetch_skill_archive(norm_source, norm_skill_id)
        installed = await asyncio.to_thread(
            _write_package_atomically,
            target_root,
            pkg,
            scope=req.scope,
            source_label=source_label,
        )
        logger.info(
            "skills.installed",
            extra={
                "event": "skills.installed",
                "skill_name": installed.name,
                "scope": installed.scope,
                "source": pkg.source,
                "skill_id": pkg.skill_id,
            },
        )
        return installed

    async def delete(
        self,
        scope: SkillScope,
        name: str,
        workspace_path: str | None = None,
    ) -> None:
        """Safely delete a user-managed skill from global or workspace .agents/skills scope."""
        if scope == "bundled":
            raise DomainError(
                "Bundled skills are read-only and cannot be deleted.",
                status_code=403,
                code="BUNDLED_SKILL_PROTECTED",
            )
        try:
            safe_name = validate_skill_name(name)
        except ValueError as exc:
            raise DomainError(str(exc), status_code=400, code="INVALID_SKILL_NAME") from exc

        if scope == "workspace":
            ws = _validate_workspace_dir(workspace_path)
            managed_root = (ws / ".agents" / "skills").resolve()
        else:
            managed_root = self._global_dir.resolve()

        target = managed_root / safe_name
        if target.is_symlink():
            raise DomainError(
                "Refusing to delete a symlinked skill directory.",
                status_code=403,
                code="UNSAFE_SKILL_PATH",
            )
        resolved = target.resolve()
        if resolved.parent != managed_root:
            raise PathEscapeError(name)
        if not resolved.is_dir():
            raise DomainError(
                f"Skill '{safe_name}' not found in scope '{scope}'.",
                status_code=404,
                code="SKILL_NOT_FOUND",
            )
        await asyncio.to_thread(shutil.rmtree, resolved)
        logger.info(
            "skills.deleted",
            extra={
                "event": "skills.deleted",
                "skill_name": safe_name,
                "scope": scope,
            },
        )
