"""Async skills.sh catalog client and safe pure-Python GitHub tarball extractor."""

from __future__ import annotations

import asyncio
import io
import logging
import re
import tarfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Protocol

import httpx

from backend.app.domains.skills.entities import (
    CatalogTab,
    RemoteSkillSummary,
    SkillCatalogPage,
    SkillPreview,
)
from backend.app.domains.skills.frontmatter import (
    MAX_SKILL_FILE_SIZE,
    parse_skill_frontmatter,
    validate_skill_name,
)
from backend.app.shared.errors import DomainError

SKILLS_SH_BASE_URL = "https://skills.sh"
CODELOAD_BASE_URL = "https://codeload.github.com"

logger = logging.getLogger(__name__)

MAX_ARCHIVE_BYTES = 25 * 1024 * 1024  # 25 MiB compressed tarball download cap
MAX_EXTRACTED_BYTES = 10 * 1024 * 1024  # 10 MiB total extracted skill payload cap
MAX_SKILL_FILE_COUNT = 200

_GITHUB_SOURCE_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_VALID_TABS: frozenset[str] = frozenset({"all-time", "trending", "hot"})
_SKIP_DIRS: frozenset[str] = frozenset(
    {".git", "node_modules", "dist", "build", "__pycache__"}
)
_PRIORITY_PREFIXES: tuple[str, ...] = (
    "skills",
    "skills/.curated",
    "skills/.experimental",
    "skills/.system",
    ".agents/skills",
    ".claude/skills",
)


@dataclass(frozen=True)
class ExtractedSkillPackage:
    """Validated in-memory files extracted safely from a remote skill archive."""

    source: str
    skill_id: str
    name: str
    description: str
    license: str | None
    compatibility: str | None
    skill_md: str
    files: dict[str, bytes]


class SkillsRegistryProtocol(Protocol):
    """Protocol for remote skills.sh catalog discovery and archive fetching."""

    async def browse(
        self,
        tab: CatalogTab = "all-time",
        page: int = 0,
    ) -> SkillCatalogPage: ...

    async def search(
        self,
        query: str,
        limit: int = 20,
    ) -> list[RemoteSkillSummary]: ...

    async def fetch_skill_archive(
        self,
        source: str,
        skill_id: str | None = None,
    ) -> ExtractedSkillPackage: ...

    async def preview_remote_skill(
        self,
        source: str,
        skill_id: str | None = None,
    ) -> SkillPreview: ...


def validate_github_source(source: str) -> str:
    """Strictly validate an owner/repo identifier to prevent SSRF or path manipulation."""
    cleaned = source.strip()
    if not _GITHUB_SOURCE_RE.fullmatch(cleaned):
        raise DomainError(
            "Skill source must match 'owner/repo' (alphanumeric, '.', '_', '-').",
            status_code=400,
            code="INVALID_SKILL_SOURCE",
        )
    owner, repo = cleaned.split("/", 1)
    if owner in {".", ".."} or repo in {".", ".."}:
        raise DomainError(
            "Skill source owner/repo segments must not be '.' or '..'.",
            status_code=400,
            code="INVALID_SKILL_SOURCE",
        )
    return cleaned


def _normalize_slug(value: str) -> str:
    return re.sub(r"[\s_]+", "-", value.strip().lower())


def _safe_tar_parts(raw_name: str) -> tuple[str, ...]:
    """Validate and split a tar member path, rejecting traversal or absolute paths."""
    if not raw_name or "\x00" in raw_name or "\\" in raw_name:
        raise DomainError(
            "Skill archive contains an invalid path.",
            status_code=400,
            code="UNSAFE_SKILL_ARCHIVE",
        )
    if raw_name.startswith("/"):
        raise DomainError(
            "Skill archive contains an absolute path.",
            status_code=400,
            code="UNSAFE_SKILL_ARCHIVE",
        )
    posix = PurePosixPath(raw_name)
    parts = tuple(p for p in posix.parts if p not in {"", "."})
    if any(part == ".." for part in parts):
        raise DomainError(
            "Skill archive contains a path traversal entry ('..').",
            status_code=400,
            code="UNSAFE_SKILL_ARCHIVE",
        )
    return parts


def _candidate_priority_rank(skill_dir: str) -> tuple[int, int, str]:
    """Rank candidate skill directories in vercel-labs/skills priority order."""
    if not skill_dir:
        return (len(_PRIORITY_PREFIXES), 0, "")
    parent = str(PurePosixPath(skill_dir).parent)
    if parent == ".":
        parent = ""
    for idx, prefix in enumerate(_PRIORITY_PREFIXES):
        if parent == prefix:
            return (idx, 0, skill_dir)
    depth = len(PurePosixPath(skill_dir).parts)
    return (len(_PRIORITY_PREFIXES) + 1, depth, skill_dir)


def _has_skipped_segment(rel_path: str) -> bool:
    if not rel_path:
        return False
    return any(part in _SKIP_DIRS for part in PurePosixPath(rel_path).parts)


def extract_skill_from_tar_bytes(
    archive_bytes: bytes,
    *,
    source: str,
    skill_id: str | None = None,
) -> ExtractedSkillPackage:
    """Safely inspect and extract a single skill directory from a GitHub .tar.gz archive."""
    clean_source = validate_github_source(source)
    repo_name = clean_source.split("/", 1)[1]

    regular_members: dict[str, tarfile.TarInfo] = {}
    try:
        with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:gz") as tf:
            members = tf.getmembers()
            for member in members:
                # Reject symlinks, hardlinks, FIFOs, and char/block devices unconditionally.
                if member.issym() or member.islnk() or member.isfifo() or member.isdev():
                    logger.warning(
                        "skills.archive_rejected",
                        extra={
                            "event": "skills.archive_rejected",
                            "source": clean_source,
                            "reason": "disallowed_member_type",
                        },
                    )
                    raise DomainError(
                        "Skill archive contains disallowed symlink or special file.",
                        status_code=400,
                        code="UNSAFE_SKILL_ARCHIVE",
                    )
                parts = _safe_tar_parts(member.name)
                if not parts:
                    continue
                if not member.isfile():
                    continue
                # Strip GitHub's single top-level wrapper folder (<repo>-<sha>/...)
                rel_parts = parts[1:] if len(parts) > 1 else parts
                rel_path = "/".join(rel_parts)
                if not rel_path:
                    continue
                regular_members[rel_path] = member

            # Discover candidate directories containing SKILL.md
            candidate_dirs: list[str] = []
            for rel_path in regular_members:
                p = PurePosixPath(rel_path)
                if p.name != "SKILL.md":
                    continue
                parent = "" if str(p.parent) == "." else str(p.parent)
                if _has_skipped_segment(parent):
                    continue
                candidate_dirs.append(parent)

            candidate_dirs = sorted(set(candidate_dirs), key=_candidate_priority_rank)
            if not candidate_dirs:
                raise DomainError(
                    f"No SKILL.md found in repository '{clean_source}'.",
                    status_code=404,
                    code="SKILL_NOT_FOUND",
                )

            # Parse frontmatter for each candidate to match by skill_id or frontmatter name
            parsed_candidates: list[tuple[str, str, str, str | None, str | None, str]] = []
            for cdir in candidate_dirs:
                md_rel = f"{cdir}/SKILL.md" if cdir else "SKILL.md"
                md_member = regular_members[md_rel]
                if md_member.size > MAX_SKILL_FILE_SIZE:
                    continue
                extracted = tf.extractfile(md_member)
                if extracted is None:
                    continue
                raw_bytes = extracted.read(MAX_SKILL_FILE_SIZE + 1)
                if len(raw_bytes) > MAX_SKILL_FILE_SIZE:
                    continue
                try:
                    raw_text = raw_bytes.decode("utf-8")
                    fm = parse_skill_frontmatter(raw_text, strict_name=True)
                except (UnicodeDecodeError, ValueError):
                    continue
                parsed_candidates.append(
                    (cdir, fm.name, fm.description, fm.license, fm.compatibility, raw_text)
                )

            if not parsed_candidates:
                raise DomainError(
                    f"No valid SKILL.md with Agent Skills frontmatter found in '{clean_source}'.",
                    status_code=400,
                    code="INVALID_SKILL_METADATA",
                )

            selected = _select_candidate(
                parsed_candidates,
                skill_id=skill_id,
                repo_name=repo_name,
                source=clean_source,
            )
            sel_dir, sel_name, sel_desc, sel_license, sel_compat, sel_md = selected
            validate_skill_name(sel_name)

            # Collect all regular files belonging to the selected skill directory
            prefix = f"{sel_dir}/" if sel_dir else ""
            matched_paths = [
                rel_path
                for rel_path in sorted(regular_members)
                if (rel_path.startswith(prefix) if prefix else True)
            ]

            if len(matched_paths) > MAX_SKILL_FILE_COUNT:
                raise DomainError(
                    f"Skill exceeds maximum allowed file count ({MAX_SKILL_FILE_COUNT}).",
                    status_code=413,
                    code="SKILL_TOO_MANY_FILES",
                )

            files: dict[str, bytes] = {}
            total_bytes = 0
            for rel_path in matched_paths:
                inner_rel = rel_path[len(prefix) :] if prefix else rel_path
                if not inner_rel or _has_skipped_segment(inner_rel):
                    continue
                member = regular_members[rel_path]
                if member.size < 0 or total_bytes + member.size > MAX_EXTRACTED_BYTES:
                    raise DomainError(
                        f"Skill exceeds maximum extracted size ({MAX_EXTRACTED_BYTES} bytes).",
                        status_code=413,
                        code="SKILL_TOO_LARGE",
                    )
                extracted = tf.extractfile(member)
                if extracted is None:
                    continue
                remaining = MAX_EXTRACTED_BYTES - total_bytes
                data = extracted.read(remaining + 1)
                if len(data) > remaining:
                    raise DomainError(
                        f"Skill exceeds maximum extracted size ({MAX_EXTRACTED_BYTES} bytes).",
                        status_code=413,
                        code="SKILL_TOO_LARGE",
                    )
                total_bytes += len(data)
                files[inner_rel] = data
    except (tarfile.TarError, OSError, EOFError) as exc:
        logger.warning(
            "skills.archive_corrupt",
            extra={
                "event": "skills.archive_corrupt",
                "source": clean_source,
                "error_type": type(exc).__name__,
            },
        )
        raise DomainError(
            f"Failed to read skill archive: {exc}",
            status_code=400,
            code="INVALID_SKILL_ARCHIVE",
        ) from exc

    resolved_skill_id = skill_id or (PurePosixPath(sel_dir).name if sel_dir else sel_name)
    return ExtractedSkillPackage(
        source=clean_source,
        skill_id=resolved_skill_id,
        name=sel_name,
        description=sel_desc,
        license=sel_license,
        compatibility=sel_compat,
        skill_md=sel_md,
        files=files,
    )


def _select_candidate(
    candidates: list[tuple[str, str, str, str | None, str | None, str]],
    *,
    skill_id: str | None,
    repo_name: str,
    source: str,
) -> tuple[str, str, str, str | None, str | None, str]:
    if skill_id is not None and skill_id.strip():
        target = skill_id.strip()
        norm_target = _normalize_slug(target)
        # Prefer exact dir name or exact frontmatter name match
        for cand in candidates:
            cdir, cname = cand[0], cand[1]
            dir_leaf = PurePosixPath(cdir).name if cdir else ""
            if target in {dir_leaf, cname}:
                return cand
        # Fallback to normalized slug match
        for cand in candidates:
            cdir, cname = cand[0], cand[1]
            dir_leaf = PurePosixPath(cdir).name if cdir else ""
            if norm_target in {_normalize_slug(dir_leaf), _normalize_slug(cname)}:
                return cand
        raise DomainError(
            f"Skill '{target}' not found in '{source}'.",
            status_code=404,
            code="SKILL_NOT_FOUND",
        )

    if len(candidates) == 1:
        return candidates[0]

    norm_repo = _normalize_slug(repo_name)
    for cand in candidates:
        cdir, cname = cand[0], cand[1]
        dir_leaf = PurePosixPath(cdir).name if cdir else ""
        if norm_repo in {_normalize_slug(dir_leaf), _normalize_slug(cname)}:
            return cand

    raise DomainError(
        f"Repository '{source}' contains multiple skills; specify skill_id.",
        status_code=400,
        code="AMBIGUOUS_SKILL_SOURCE",
    )


def _parse_remote_skill(item: Mapping[str, object]) -> RemoteSkillSummary | None:
    source = str(item.get("source") or "").strip()
    skill_id = str(item.get("skillId") or item.get("skill_id") or "").strip()
    name = str(item.get("name") or skill_id).strip()
    if not source or not skill_id:
        return None
    raw_id = str(item.get("id") or f"{source}/{skill_id}").strip()
    raw_installs = item.get("installs")
    installs = int(raw_installs) if isinstance(raw_installs, int | float) else 0
    is_official = bool(item.get("isOfficial", False))

    raw_weekly = item.get("weeklyInstalls")
    weekly_installs: list[int] = []
    if isinstance(raw_weekly, list):
        weekly_installs = [int(v) for v in raw_weekly if isinstance(v, int | float)]

    raw_yesterday = item.get("installsYesterday")
    installs_yesterday = (
        int(raw_yesterday) if isinstance(raw_yesterday, int | float) else None
    )
    raw_change = item.get("change")
    change = int(raw_change) if isinstance(raw_change, int | float) else None

    return RemoteSkillSummary(
        id=raw_id,
        source=source,
        skill_id=skill_id,
        name=name,
        installs=installs,
        is_official=is_official,
        weekly_installs=weekly_installs,
        installs_yesterday=installs_yesterday,
        change=change,
    )


class SkillsShClient:
    """Async HTTP client for skills.sh public APIs and GitHub codeload tarballs."""

    def __init__(
        self,
        *,
        skills_sh_base_url: str = SKILLS_SH_BASE_URL,
        codeload_base_url: str = CODELOAD_BASE_URL,
        timeout: float = 20.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._skills_sh_base_url = skills_sh_base_url.rstrip("/")
        self._codeload_base_url = codeload_base_url.rstrip("/")
        self._timeout = timeout
        self._transport = transport

    def _new_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            trust_env=False,
            timeout=self._timeout,
            follow_redirects=True,
            transport=self._transport,
        )

    async def browse(
        self,
        tab: CatalogTab = "all-time",
        page: int = 0,
    ) -> SkillCatalogPage:
        if tab not in _VALID_TABS:
            raise DomainError(
                f"Invalid catalog tab '{tab}'.",
                status_code=400,
                code="INVALID_CATALOG_TAB",
            )
        if page < 0:
            raise DomainError(
                "Catalog page must be >= 0.",
                status_code=400,
                code="INVALID_CATALOG_PAGE",
            )
        url = f"{self._skills_sh_base_url}/api/skills/{tab}/{page}"
        try:
            async with self._new_client() as client:
                resp = await client.get(url)
        except httpx.HTTPError as exc:
            logger.warning(
                "skills.registry_unavailable",
                extra={
                    "event": "skills.registry_unavailable",
                    "operation": "browse",
                    "error_type": type(exc).__name__,
                },
            )
            raise DomainError(
                f"Failed to reach skills.sh catalog: {exc}",
                status_code=502,
                code="SKILLS_REGISTRY_UNAVAILABLE",
            ) from exc

        if resp.status_code != 200:
            logger.warning(
                "skills.registry_http_error",
                extra={
                    "event": "skills.registry_http_error",
                    "operation": "browse",
                    "status_code": resp.status_code,
                },
            )
            raise DomainError(
                f"skills.sh catalog returned HTTP {resp.status_code}.",
                status_code=502,
                code="SKILLS_REGISTRY_ERROR",
            )

        data = resp.json()
        if not isinstance(data, Mapping):
            raise DomainError(
                "Unexpected skills.sh catalog response format.",
                status_code=502,
                code="SKILLS_REGISTRY_ERROR",
            )
        raw_skills = data.get("skills")
        skills: list[RemoteSkillSummary] = []
        if isinstance(raw_skills, list):
            for entry in raw_skills:
                if isinstance(entry, Mapping):
                    parsed = _parse_remote_skill(entry)
                    if parsed is not None:
                        skills.append(parsed)
        raw_total = data.get("total")
        total = int(raw_total) if isinstance(raw_total, int | float) else len(skills)
        has_more = bool(data.get("hasMore", False))
        return SkillCatalogPage(
            tab=tab,
            page=page,
            total=total,
            has_more=has_more,
            skills=skills,
        )

    async def search(
        self,
        query: str,
        limit: int = 20,
    ) -> list[RemoteSkillSummary]:
        clean_q = query.strip()
        if len(clean_q) < 2:
            raise DomainError(
                "Search query must be at least 2 characters.",
                status_code=400,
                code="INVALID_SEARCH_QUERY",
            )
        safe_limit = max(1, min(limit, 100))
        url = f"{self._skills_sh_base_url}/api/search"
        try:
            async with self._new_client() as client:
                resp = await client.get(url, params={"q": clean_q, "limit": str(safe_limit)})
        except httpx.HTTPError as exc:
            logger.warning(
                "skills.registry_unavailable",
                extra={
                    "event": "skills.registry_unavailable",
                    "operation": "search",
                    "error_type": type(exc).__name__,
                },
            )
            raise DomainError(
                f"Failed to search skills.sh: {exc}",
                status_code=502,
                code="SKILLS_REGISTRY_UNAVAILABLE",
            ) from exc

        if resp.status_code != 200:
            logger.warning(
                "skills.registry_http_error",
                extra={
                    "event": "skills.registry_http_error",
                    "operation": "search",
                    "status_code": resp.status_code,
                },
            )
            raise DomainError(
                f"skills.sh search returned HTTP {resp.status_code}.",
                status_code=502,
                code="SKILLS_REGISTRY_ERROR",
            )

        data = resp.json()
        raw_skills = data.get("skills") if isinstance(data, Mapping) else None
        results: list[RemoteSkillSummary] = []
        if isinstance(raw_skills, list):
            for entry in raw_skills:
                if isinstance(entry, Mapping):
                    parsed = _parse_remote_skill(entry)
                    if parsed is not None:
                        results.append(parsed)
        return results[:safe_limit]

    async def fetch_skill_archive(
        self,
        source: str,
        skill_id: str | None = None,
    ) -> ExtractedSkillPackage:
        clean_source = validate_github_source(source)
        owner, repo = clean_source.split("/", 1)
        url = f"{self._codeload_base_url}/{owner}/{repo}/tar.gz/HEAD"
        chunks: list[bytes] = []
        total = 0

        try:
            async with self._new_client() as client, client.stream("GET", url) as resp:
                if resp.status_code == 404:
                    raise DomainError(
                        f"GitHub repository '{clean_source}' not found.",
                        status_code=404,
                        code="SKILL_REPO_NOT_FOUND",
                    )
                if resp.status_code != 200:
                    logger.warning(
                        "skills.archive_download_failed",
                        extra={
                            "event": "skills.archive_download_failed",
                            "source": clean_source,
                            "status_code": resp.status_code,
                        },
                    )
                    raise DomainError(
                        f"Archive download failed for '{clean_source}' (HTTP {resp.status_code}).",
                        status_code=502,
                        code="SKILL_DOWNLOAD_FAILED",
                    )
                content_length = resp.headers.get("Content-Length")
                if (
                    content_length is not None
                    and content_length.isdigit()
                    and int(content_length) > MAX_ARCHIVE_BYTES
                ):
                    raise DomainError(
                        f"Skill repository archive exceeds {MAX_ARCHIVE_BYTES} bytes cap.",
                        status_code=413,
                        code="SKILL_ARCHIVE_TOO_LARGE",
                    )
                async for chunk in resp.aiter_bytes(chunk_size=65536):
                    total += len(chunk)
                    if total > MAX_ARCHIVE_BYTES:
                        raise DomainError(
                            f"Skill repository archive exceeds {MAX_ARCHIVE_BYTES} bytes cap.",
                            status_code=413,
                            code="SKILL_ARCHIVE_TOO_LARGE",
                        )
                    chunks.append(chunk)
        except DomainError:
            raise
        except httpx.HTTPError as exc:
            logger.warning(
                "skills.archive_download_error",
                extra={
                    "event": "skills.archive_download_error",
                    "source": clean_source,
                    "error_type": type(exc).__name__,
                },
            )
            raise DomainError(
                f"Failed to download archive for '{clean_source}': {exc}",
                status_code=502,
                code="SKILL_DOWNLOAD_FAILED",
            ) from exc

        archive_bytes = b"".join(chunks)
        return await asyncio.to_thread(
            extract_skill_from_tar_bytes,
            archive_bytes,
            source=clean_source,
            skill_id=skill_id,
        )

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
