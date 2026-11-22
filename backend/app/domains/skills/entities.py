"""Pure domain entities for the skills bounded context (stdlib + pydantic only)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

SkillScope = Literal["bundled", "global", "workspace"]
CatalogTab = Literal["all-time", "trending", "hot"]


class InstalledSkill(BaseModel):
    """Metadata for a locally available skill across bundled, global, or workspace scopes."""

    name: str
    description: str
    scope: SkillScope
    source_label: str
    path: str
    deletable: bool
    license: str | None = None
    compatibility: str | None = None
    origin_source: str | None = None
    origin_skill_id: str | None = None
    origin_url: str | None = None
    installed_at: datetime | None = None


class InstalledSkillDetail(InstalledSkill):
    """Full SKILL.md content and supporting file inventory for an installed skill."""

    content: str
    files: list[str] = Field(default_factory=list)


class RemoteSkillSummary(BaseModel):
    """Summary entry returned by the skills.sh catalog or search API."""

    id: str
    source: str
    skill_id: str
    name: str
    installs: int = 0
    is_official: bool = False
    weekly_installs: list[int] = Field(default_factory=list)
    installs_yesterday: int | None = None
    change: int | None = None


class SkillCatalogPage(BaseModel):
    """Paginated leaderboard response from skills.sh."""

    tab: CatalogTab
    page: int
    total: int
    has_more: bool
    skills: list[RemoteSkillSummary] = Field(default_factory=list)


class SkillPreview(BaseModel):
    """Remote SKILL.md preview fetched prior to installation."""

    source: str
    skill_id: str
    name: str
    description: str
    content: str
    license: str | None = None
    compatibility: str | None = None
    files: list[str] = Field(default_factory=list)


class SkillInstallRequest(BaseModel):
    """Request payload to install a skill from GitHub/skills.sh into global or workspace scope."""

    source: str = Field(min_length=3)
    skill_id: str | None = None
    scope: Literal["global", "workspace"] = "global"
    workspace_path: str | None = None
