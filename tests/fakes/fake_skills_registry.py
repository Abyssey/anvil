"""Deterministic offline fake implementation of SkillsRegistryProtocol for tests."""

from __future__ import annotations

from backend.app.domains.skills.entities import (
    CatalogTab,
    RemoteSkillSummary,
    SkillCatalogPage,
    SkillPreview,
)
from backend.app.domains.skills.frontmatter import parse_skill_frontmatter
from backend.app.domains.skills.registry_client import (
    ExtractedSkillPackage,
    validate_github_source,
)
from backend.app.shared.errors import DomainError


class FakeSkillsRegistry:
    """In-memory skills.sh catalog + GitHub package fake for 100% offline tests."""

    def __init__(self) -> None:
        self.packages: dict[tuple[str, str], ExtractedSkillPackage] = {}
        self.catalog_entries: list[RemoteSkillSummary] = []
        self._seed_defaults()

    def _seed_defaults(self) -> None:
        self.add_package(
            source="vercel-labs/agent-skills",
            skill_id="react-patterns",
            files={
                "SKILL.md": (
                    b"---\n"
                    b"name: react-patterns\n"
                    b"description: Modern React composition and hook patterns.\n"
                    b"license: MIT\n"
                    b"compatibility: React 19+\n"
                    b"---\n\n"
                    b"# React Patterns\n\nUse compound components and explicit variants.\n"
                ),
                "references/hooks.md": b"# Hooks Reference\n",
            },
            installs=125000,
            is_official=True,
        )
        self.add_package(
            source="anthropics/skills",
            skill_id="pdf-helper",
            files={
                "SKILL.md": (
                    b"---\n"
                    b"name: pdf-helper\n"
                    b"description: Extract and summarize tables from PDF documents.\n"
                    b"---\n\n"
                    b"# PDF Helper\n\nInspect pages deterministically.\n"
                ),
                "scripts/extract.py": b"print('ok')\n",
            },
            installs=82000,
            is_official=False,
        )

    def add_package(
        self,
        *,
        source: str,
        skill_id: str,
        files: dict[str, bytes],
        installs: int = 1000,
        is_official: bool = False,
    ) -> None:
        raw_md = files["SKILL.md"].decode("utf-8")
        parsed = parse_skill_frontmatter(raw_md)
        pkg = ExtractedSkillPackage(
            source=source,
            skill_id=skill_id,
            name=parsed.name,
            description=parsed.description,
            license=parsed.license,
            compatibility=parsed.compatibility,
            skill_md=raw_md,
            files=dict(files),
        )
        self.packages[(source, skill_id)] = pkg
        summary = RemoteSkillSummary(
            id=f"{source}/{skill_id}",
            source=source,
            skill_id=skill_id,
            name=parsed.name,
            installs=installs,
            is_official=is_official,
        )
        self.catalog_entries = [
            item for item in self.catalog_entries if item.id != summary.id
        ] + [summary]

    async def browse(
        self,
        tab: CatalogTab = "all-time",
        page: int = 0,
    ) -> SkillCatalogPage:
        ordered = sorted(self.catalog_entries, key=lambda s: s.installs, reverse=True)
        return SkillCatalogPage(
            tab=tab,
            page=page,
            total=len(ordered),
            has_more=False,
            skills=ordered if page == 0 else [],
        )

    async def search(
        self,
        query: str,
        limit: int = 20,
    ) -> list[RemoteSkillSummary]:
        needle = query.strip().lower()
        if len(needle) < 2:
            raise DomainError(
                "Search query must be at least 2 characters.",
                status_code=400,
                code="INVALID_SEARCH_QUERY",
            )
        matches = [
            item
            for item in self.catalog_entries
            if needle in item.name.lower()
            or needle in item.skill_id.lower()
            or needle in item.source.lower()
        ]
        return matches[:limit]

    async def fetch_skill_archive(
        self,
        source: str,
        skill_id: str | None = None,
    ) -> ExtractedSkillPackage:
        clean_source = validate_github_source(source)
        if skill_id is not None:
            pkg = self.packages.get((clean_source, skill_id))
            if pkg is None:
                raise DomainError(
                    f"Skill '{skill_id}' not found in '{clean_source}'.",
                    status_code=404,
                    code="SKILL_NOT_FOUND",
                )
            return pkg

        candidates = [
            pkg for (src, _sid), pkg in self.packages.items() if src == clean_source
        ]
        if not candidates:
            raise DomainError(
                f"No skills found in '{clean_source}'.",
                status_code=404,
                code="SKILL_NOT_FOUND",
            )
        if len(candidates) > 1:
            raise DomainError(
                f"Multiple skills found in '{clean_source}'; specify skill_id.",
                status_code=400,
                code="AMBIGUOUS_SKILL_SOURCE",
            )
        return candidates[0]

    async def preview_remote_skill(
        self,
        source: str,
        skill_id: str | None = None,
    ) -> SkillPreview:
        pkg = await self.fetch_skill_archive(source, skill_id)
        return SkillPreview(
            source=pkg.source,
            skill_id=pkg.skill_id,
            name=pkg.name,
            description=pkg.description,
            content=pkg.skill_md,
            license=pkg.license,
            compatibility=pkg.compatibility,
            files=sorted(pkg.files.keys()),
        )
