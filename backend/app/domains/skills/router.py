"""Thin FastAPI router for skills discovery, preview, installation, and deletion."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, status

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
from backend.app.shared.dependencies import SkillStoreDep

router = APIRouter(prefix="/api/skills", tags=["skills"])


@router.get("/installed")
async def list_installed_skills(
    store: SkillStoreDep,
    workspace_path: Annotated[str | None, Query()] = None,
) -> list[InstalledSkill]:
    return store.list_installed(workspace_path=workspace_path)


@router.get("/installed/{scope}/{name}")
async def get_installed_skill_detail(
    scope: SkillScope,
    name: str,
    store: SkillStoreDep,
    workspace_path: Annotated[str | None, Query()] = None,
) -> InstalledSkillDetail:
    return store.get_installed_detail(scope, name, workspace_path=workspace_path)


@router.get("/catalog")
async def browse_skills_catalog(
    store: SkillStoreDep,
    tab: Annotated[CatalogTab, Query()] = "all-time",
    page: Annotated[int, Query(ge=0)] = 0,
) -> SkillCatalogPage:
    return await store.browse_catalog(tab=tab, page=page)


@router.get("/search")
async def search_skills_catalog(
    store: SkillStoreDep,
    q: Annotated[str, Query(min_length=2)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> list[RemoteSkillSummary]:
    return await store.search_catalog(query=q, limit=limit)


@router.get("/preview")
async def preview_remote_skill(
    store: SkillStoreDep,
    source: Annotated[str, Query(min_length=3)],
    skill_id: Annotated[str | None, Query()] = None,
) -> SkillPreview:
    return await store.preview_remote(source=source, skill_id=skill_id)


@router.post("/install", status_code=status.HTTP_201_CREATED)
async def install_skill(
    body: SkillInstallRequest,
    store: SkillStoreDep,
) -> InstalledSkill:
    return await store.install(body)


@router.delete("/{scope}/{name}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_skill(
    scope: SkillScope,
    name: str,
    store: SkillStoreDep,
    workspace_path: Annotated[str | None, Query()] = None,
) -> None:
    await store.delete(scope, name, workspace_path=workspace_path)
