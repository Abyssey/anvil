"""Async skills.sh catalog client and safe pure-Python GitHub tarball extractor."""

from __future__ import annotations

import asyncio
import contextlib
import io
import logging
import os
import queue
import re
import tarfile
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import BinaryIO, Protocol
from urllib.parse import urlparse

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

SKILLS_SH_BASE_URL = "http://skills.sh"
CODELOAD_BASE_URL = "https://codeload.github.com"

logger = logging.getLogger(__name__)


def _read_max_archive_bytes() -> int:
    raw = os.environ.get("SKILLS_MAX_ARCHIVE_BYTES", "").strip()
    if raw.isdigit() and int(raw) > 0:
        return int(raw)
    return 512 * 1024 * 1024  # 512 MiB streaming scan cap (never buffered whole in RAM)


MAX_ARCHIVE_BYTES = _read_max_archive_bytes()
MAX_EXTRACTED_BYTES = 10 * 1024 * 1024  # 10 MiB total extracted skill payload cap
MAX_SKILL_FILE_COUNT = 200
_FALLBACK_NON_PRIORITY_BUFFER_BYTES = 16 * 1024 * 1024

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


def normalize_skill_source_input(
    source: str,
    skill_id: str | None = None,
) -> tuple[str, str | None]:
    """Normalize shorthand ('owner/repo@skill', 'owner/repo/skill', GitHub/skills.sh URLs)."""
    cleaned = source.strip()
    explicit_skill = skill_id.strip() if isinstance(skill_id, str) and skill_id.strip() else None

    if cleaned.startswith(("https://", "http://")):
        parsed = urlparse(cleaned)
        host = (parsed.hostname or "").lower()
        if host not in {"github.com", "www.github.com", "skills.sh", "www.skills.sh"}:
            return cleaned, explicit_skill
        segments = [seg for seg in parsed.path.split("/") if seg]
        if len(segments) >= 2:
            owner_repo = f"{segments[0]}/{segments[1].removesuffix('.git')}"
            inferred: str | None = None
            if host in {"skills.sh", "www.skills.sh"} and len(segments) >= 3:
                inferred = segments[-1]
            elif len(segments) >= 5 and segments[2] in {"tree", "blob"}:
                # e.g. /owner/repo/tree/main/skills/react-patterns
                leaf = segments[-1]
                if leaf.upper() == "SKILL.MD" and len(segments) >= 6:
                    inferred = segments[-2]
                elif leaf.upper() != "SKILL.MD":
                    inferred = leaf
            return owner_repo, explicit_skill or inferred
        return cleaned, explicit_skill

    if "@" in cleaned:
        left, right = cleaned.split("@", 1)
        inferred_at = right.strip() or None
        return left.strip(), explicit_skill or inferred_at

    slash_parts = [seg for seg in cleaned.split("/") if seg]
    if (
        len(slash_parts) == 3
        and not cleaned.startswith((".", "/"))
        and all(seg not in {".", ".."} for seg in slash_parts)
        and "?" not in cleaned
        and "#" not in cleaned
    ):
        owner_repo = f"{slash_parts[0]}/{slash_parts[1]}"
        if _GITHUB_SOURCE_RE.fullmatch(owner_repo):
            return owner_repo, explicit_skill or slash_parts[2]

    return cleaned, explicit_skill


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


@dataclass(frozen=True)
class _BufferedEntry:
    size: int
    data: bytes | None


class _StreamChunkReader(io.RawIOBase):
    """Thread-safe file-like adapter bridging async HTTP chunks to tarfile mode='r|gz'."""

    def __init__(
        self,
        chunk_queue: queue.Queue[bytes | BaseException | None],
        stop_event: threading.Event,
    ) -> None:
        super().__init__()
        self._queue = chunk_queue
        self._stop_event = stop_event
        self._buf = bytearray()
        self._eof = False

    def readable(self) -> bool:
        return True

    def readinto(self, b: bytearray | memoryview) -> int:  # type: ignore[override]
        if self._eof and not self._buf:
            return 0
        target = memoryview(b)
        needed = len(target)
        if needed == 0:
            return 0
        while not self._buf and not self._eof:
            item = self._queue.get()
            if item is None:
                self._eof = True
                break
            if isinstance(item, BaseException):
                raise item
            self._buf.extend(item)
        if not self._buf:
            return 0
        n = min(needed, len(self._buf))
        target[:n] = self._buf[:n]
        del self._buf[:n]
        return n

    def close(self) -> None:
        self._stop_event.set()
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break
        super().close()


def _is_potentially_relevant_path(
    p: PurePosixPath,
    *,
    norm_target: str | None,
) -> bool:
    ancestors = p.parts[:-1]
    if not ancestors:
        return True
    if ancestors[0] in {"skills", ".agents", ".claude"}:
        return True
    if norm_target is not None:
        return any(_normalize_slug(seg) == norm_target for seg in ancestors)
    return True


def extract_skill_from_tar_stream(
    fileobj: BinaryIO,
    *,
    source: str,
    skill_id: str | None = None,
    stop_event: threading.Event | None = None,
) -> ExtractedSkillPackage:
    """Stream-scan a GitHub .tar.gz without buffering unrelated monorepo files in memory."""
    clean_source = validate_github_source(source)
    repo_name = clean_source.split("/", 1)[1]
    clean_target = skill_id.strip() if isinstance(skill_id, str) and skill_id.strip() else None
    norm_target = _normalize_slug(clean_target) if clean_target else None

    buffered_entries: dict[str, _BufferedEntry] = {}
    unsafe_paths: list[str] = []
    candidate_dirs_seen: set[str] = set()
    parsed_candidates: list[tuple[str, str, str, str | None, str | None, str]] = []
    early_match_dir: str | None = None
    non_priority_bytes = 0

    try:
        with tarfile.open(fileobj=fileobj, mode="r|gz") as tf:
            for member in tf:
                parts = _safe_tar_parts(member.name)
                if not parts:
                    continue
                rel_parts = parts[1:] if len(parts) > 1 else parts
                rel_path = "/".join(rel_parts)

                # Once an exact top-priority 'skills/<skill_id>' subtree has been fully read,
                # stop consuming the remaining archive stream immediately.
                if (
                    early_match_dir is not None
                    and rel_path
                    and rel_path != early_match_dir
                    and not rel_path.startswith(f"{early_match_dir}/")
                ):
                    if stop_event is not None:
                        stop_event.set()
                    break

                if member.issym() or member.islnk() or member.isfifo() or member.isdev():
                    if rel_path:
                        unsafe_paths.append(rel_path)
                    continue

                if not member.isfile() or not rel_path:
                    continue
                if _has_skipped_segment(rel_path):
                    continue

                p = PurePosixPath(rel_path)
                if p.name == "SKILL.md":
                    parent = "" if str(p.parent) == "." else str(p.parent)
                    candidate_dirs_seen.add(parent)
                    if member.size < 0 or member.size > MAX_SKILL_FILE_SIZE:
                        buffered_entries[rel_path] = _BufferedEntry(
                            size=max(member.size, MAX_SKILL_FILE_SIZE + 1),
                            data=None,
                        )
                        continue
                    extracted = tf.extractfile(member)
                    if extracted is None:
                        continue
                    raw_bytes = extracted.read(MAX_SKILL_FILE_SIZE + 1)
                    if len(raw_bytes) > MAX_SKILL_FILE_SIZE:
                        buffered_entries[rel_path] = _BufferedEntry(
                            size=len(raw_bytes),
                            data=None,
                        )
                        continue
                    buffered_entries[rel_path] = _BufferedEntry(
                        size=len(raw_bytes),
                        data=raw_bytes,
                    )
                    try:
                        raw_text = raw_bytes.decode("utf-8")
                        fm = parse_skill_frontmatter(raw_text, strict_name=True)
                    except (UnicodeDecodeError, ValueError):
                        continue
                    parsed_candidates.append(
                        (parent, fm.name, fm.description, fm.license, fm.compatibility, raw_text)
                    )
                    if (
                        clean_target is not None
                        and parent
                        and _candidate_priority_rank(parent)[0] == 0
                    ):
                        dir_leaf = PurePosixPath(parent).name
                        if clean_target in {dir_leaf, fm.name}:
                            early_match_dir = parent
                    continue

                is_priority = _is_potentially_relevant_path(p, norm_target=norm_target)
                if not is_priority and non_priority_bytes >= _FALLBACK_NON_PRIORITY_BUFFER_BYTES:
                    continue

                if member.size < 0 or member.size > MAX_EXTRACTED_BYTES:
                    buffered_entries[rel_path] = _BufferedEntry(
                        size=max(member.size, MAX_EXTRACTED_BYTES + 1),
                        data=None,
                    )
                    continue

                extracted = tf.extractfile(member)
                if extracted is None:
                    continue
                data = extracted.read(MAX_EXTRACTED_BYTES + 1)
                if len(data) > MAX_EXTRACTED_BYTES:
                    buffered_entries[rel_path] = _BufferedEntry(size=len(data), data=None)
                    continue

                if not is_priority:
                    non_priority_bytes += len(data)
                buffered_entries[rel_path] = _BufferedEntry(size=len(data), data=data)
    except DomainError:
        raise
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

    if not candidate_dirs_seen:
        # Even when no SKILL.md exists, reject root-level unsafe entries consistently.
        if unsafe_paths:
            raise DomainError(
                "Skill archive contains disallowed symlink or special file.",
                status_code=400,
                code="UNSAFE_SKILL_ARCHIVE",
            )
        raise DomainError(
            f"No SKILL.md found in repository '{clean_source}'.",
            status_code=404,
            code="SKILL_NOT_FOUND",
        )

    parsed_candidates.sort(key=lambda cand: _candidate_priority_rank(cand[0]))
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

    prefix = f"{sel_dir}/" if sel_dir else ""
    has_unsafe_in_selected = any(
        (u == sel_dir or u.startswith(prefix)) if prefix else True
        for u in unsafe_paths
    )
    if has_unsafe_in_selected:
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

    matched_paths = [
        rel_path
        for rel_path in sorted(buffered_entries)
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
        entry = buffered_entries[rel_path]
        if entry.data is None or total_bytes + entry.size > MAX_EXTRACTED_BYTES:
            raise DomainError(
                f"Skill exceeds maximum extracted size ({MAX_EXTRACTED_BYTES} bytes).",
                status_code=413,
                code="SKILL_TOO_LARGE",
            )
        total_bytes += len(entry.data)
        files[inner_rel] = entry.data

    resolved_skill_id = clean_target or (PurePosixPath(sel_dir).name if sel_dir else sel_name)
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


def extract_skill_from_tar_bytes(
    archive_bytes: bytes,
    *,
    source: str,
    skill_id: str | None = None,
) -> ExtractedSkillPackage:
    """Safely inspect and extract a single skill directory from a GitHub .tar.gz byte buffer."""
    return extract_skill_from_tar_stream(
        io.BytesIO(archive_bytes),
        source=source,
        skill_id=skill_id,
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


def _swap_http_scheme(base_url: str) -> str | None:
    if base_url.startswith("http://"):
        return "https://" + base_url.removeprefix("http://")
    if base_url.startswith("https://"):
        return "http://" + base_url.removeprefix("https://")
    return None


class SkillsShClient:
    """Async HTTP client for skills.sh public APIs and GitHub codeload tarballs."""

    def __init__(
        self,
        *,
        skills_sh_base_url: str | None = None,
        codeload_base_url: str | None = None,
        verify_ssl: bool = False,
        timeout: float = 20.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        env_skills = os.environ.get("SKILLS_SH_BASE_URL", "").strip()
        env_codeload = os.environ.get("SKILLS_CODELOAD_BASE_URL", "").strip()
        resolved_skills = skills_sh_base_url or env_skills or SKILLS_SH_BASE_URL
        resolved_codeload = codeload_base_url or env_codeload or CODELOAD_BASE_URL
        self._skills_sh_base_url = resolved_skills.rstrip("/")
        self._codeload_base_url = resolved_codeload.rstrip("/")
        self._allow_scheme_fallback = skills_sh_base_url is None and not env_skills
        self._verify_ssl = verify_ssl
        self._timeout = timeout
        self._transport = transport

    def _new_client(self, *, archive_stream: bool = False) -> httpx.AsyncClient:
        effective_timeout: float | httpx.Timeout = (
            httpx.Timeout(
                connect=self._timeout,
                read=max(self._timeout, 60.0),
                write=10.0,
                pool=10.0,
            )
            if archive_stream
            else self._timeout
        )
        return httpx.AsyncClient(
            trust_env=False,
            verify=self._verify_ssl,
            timeout=effective_timeout,
            follow_redirects=True,
            transport=self._transport,
        )

    async def _request_skills_sh(
        self,
        path: str,
        *,
        params: Mapping[str, str] | None = None,
    ) -> httpx.Response:
        primary_url = f"{self._skills_sh_base_url}{path}"
        async with self._new_client() as client:
            try:
                return await client.get(primary_url, params=params)
            except httpx.HTTPError:
                fallback_base = (
                    _swap_http_scheme(self._skills_sh_base_url)
                    if self._allow_scheme_fallback
                    else None
                )
                if fallback_base is None:
                    raise
                return await client.get(f"{fallback_base}{path}", params=params)

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
        try:
            resp = await self._request_skills_sh(f"/api/skills/{tab}/{page}")
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
        try:
            resp = await self._request_skills_sh(
                "/api/search",
                params={"q": clean_q, "limit": str(safe_limit)},
            )
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
        norm_source, norm_skill_id = normalize_skill_source_input(source, skill_id)
        clean_source = validate_github_source(norm_source)
        owner, repo = clean_source.split("/", 1)
        url = f"{self._codeload_base_url}/{owner}/{repo}/tar.gz/HEAD"
        total = 0

        try:
            async with (
                self._new_client(archive_stream=True) as client,
                client.stream("GET", url) as resp,
            ):
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

                chunk_queue: queue.Queue[bytes | BaseException | None] = queue.Queue(maxsize=16)
                stop_event = threading.Event()
                reader = _StreamChunkReader(chunk_queue, stop_event)
                loop = asyncio.get_running_loop()

                def _worker() -> ExtractedSkillPackage:
                    try:
                        return extract_skill_from_tar_stream(
                            reader,  # type: ignore[arg-type]
                            source=clean_source,
                            skill_id=norm_skill_id,
                            stop_event=stop_event,
                        )
                    finally:
                        reader.close()

                worker_fut = loop.run_in_executor(None, _worker)

                async def _enqueue(item: bytes | BaseException | None) -> bool:
                    while not stop_event.is_set() and not worker_fut.done():
                        try:
                            chunk_queue.put_nowait(item)
                            return True
                        except queue.Full:
                            await asyncio.sleep(0.005)
                    return False

                try:
                    async for chunk in resp.aiter_bytes(chunk_size=65536):
                        if stop_event.is_set() or worker_fut.done():
                            break
                        total += len(chunk)
                        if total > MAX_ARCHIVE_BYTES:
                            err = DomainError(
                                f"Skill repository archive exceeds {MAX_ARCHIVE_BYTES} bytes cap.",
                                status_code=413,
                                code="SKILL_ARCHIVE_TOO_LARGE",
                            )
                            stop_event.set()
                            with contextlib.suppress(queue.Empty):
                                chunk_queue.get_nowait()
                            with contextlib.suppress(queue.Full):
                                chunk_queue.put_nowait(err)
                            raise err
                        if not await _enqueue(chunk):
                            break
                except BaseException as stream_exc:
                    if not worker_fut.done():
                        stop_event.set()
                        with contextlib.suppress(queue.Empty):
                            chunk_queue.get_nowait()
                        with contextlib.suppress(queue.Full):
                            chunk_queue.put_nowait(stream_exc)
                    raise
                finally:
                    if not worker_fut.done():
                        try:
                            chunk_queue.put_nowait(None)
                        except queue.Full:
                            stop_event.set()

                return await worker_fut
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
