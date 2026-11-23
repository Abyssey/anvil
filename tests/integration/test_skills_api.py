"""Offline unit and API integration tests for the skills bounded context."""

from __future__ import annotations

import io
import json
import tarfile
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from backend.app.domains.agent.run_context import RunCatalog, RunContext
from backend.app.domains.agent.shell_guard import ShellGuard
from backend.app.domains.agent.skill_backend import SkillFiles
from backend.app.domains.skills.entities import SkillInstallRequest
from backend.app.domains.skills.frontmatter import (
    MAX_SKILL_FILE_SIZE,
    parse_skill_frontmatter,
    validate_skill_name,
)
from backend.app.domains.skills.registry_client import (
    MAX_ARCHIVE_BYTES,
    MAX_EXTRACTED_BYTES,
    MAX_SKILL_FILE_COUNT,
    SkillsShClient,
    extract_skill_from_tar_bytes,
    validate_github_source,
)
from backend.app.domains.skills.router import router as skills_router
from backend.app.domains.skills.store import PROVENANCE_FILENAME, SkillStore
from backend.app.shared.dependencies import get_skill_store
from backend.app.shared.errors import DomainError
from backend.app.shared.http import register_exception_handlers
from tests.fakes.fake_skills_registry import FakeSkillsRegistry


def _make_tar_gz(
    entries: list[tuple[str, bytes | None, int, str]],
) -> bytes:
    """Build an in-memory .tar.gz archive.

    Each entry is (name, data_or_None, tar_type, linkname).
    """
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, data, tar_type, linkname in entries:
            info = tarfile.TarInfo(name=name)
            info.type = tar_type
            if linkname:
                info.linkname = linkname
            if data is not None and tar_type == tarfile.REGTYPE:
                info.size = len(data)
                tf.addfile(info, io.BytesIO(data))
            else:
                info.size = 0
                tf.addfile(info)
    return buf.getvalue()


def _skill_md(name: str, description: str = "Helpful skill.", extra: str = "") -> bytes:
    header = f"---\nname: {name}\ndescription: {description}\n"
    if extra:
        header += extra
    header += f"---\n\n# {name}\n\nFollow these steps.\n"
    return header.encode("utf-8")


def _write_local_skill(root: Path, name: str, description: str) -> Path:
    sdir = root / name
    sdir.mkdir(parents=True, exist_ok=True)
    (sdir / "SKILL.md").write_bytes(_skill_md(name, description))
    return sdir


# ---------------------------------------------------------------------------
# Frontmatter & name validation
# ---------------------------------------------------------------------------


def test_validate_skill_name_enforces_spec() -> None:
    assert validate_skill_name("react-patterns") == "react-patterns"
    assert validate_skill_name("a") == "a"
    assert validate_skill_name("skill123-v2") == "skill123-v2"

    for bad in (
        "",
        "../evil",
        "a/b",
        "UpperCase",
        "-leading",
        "trailing-",
        "double--dash",
        "has space",
        "a" * 65,
    ):
        with pytest.raises(ValueError):
            validate_skill_name(bad)


def test_parse_skill_frontmatter_and_size_limit() -> None:
    raw = (
        "---\n"
        "name: test-skill\n"
        "description: Runs tests cleanly.\n"
        "license: MIT\n"
        "compatibility: Python 3.12+\n"
        "metadata:\n"
        "  author: acme\n"
        "---\n\n"
        "# Body\n\nDetails here.\n"
    )
    parsed = parse_skill_frontmatter(raw)
    assert parsed.name == "test-skill"
    assert parsed.description == "Runs tests cleanly."
    assert parsed.license == "MIT"
    assert parsed.compatibility == "Python 3.12+"
    assert parsed.metadata == {"author": "acme"}
    assert "# Body" in parsed.body

    with pytest.raises(ValueError):
        parse_skill_frontmatter("no frontmatter here")

    oversized = "---\nname: big\ndescription: big\n---\n" + ("x" * (MAX_SKILL_FILE_SIZE + 1))
    with pytest.raises(ValueError):
        parse_skill_frontmatter(oversized)


# ---------------------------------------------------------------------------
# SkillsShClient & tarball security
# ---------------------------------------------------------------------------


def test_validate_github_source_prevents_ssrf() -> None:
    assert validate_github_source("vercel-labs/agent-skills") == "vercel-labs/agent-skills"
    assert validate_github_source("org_1/repo.name-2") == "org_1/repo.name-2"

    for bad in (
        "https://github.com/a/b",
        "http://127.0.0.1/a/b",
        "../etc/passwd",
        "owner/..",
        "./repo",
        "owner/repo/extra",
        "owner/repo?foo=bar",
        "owner/repo#frag",
        "owner",
        "",
    ):
        with pytest.raises(DomainError) as exc_info:
            validate_github_source(bad)
        assert exc_info.value.status_code == 400


def test_tarball_security_rejects_zip_slip_symlinks_hardlinks_and_caps() -> None:
    # 1. Path traversal (zip-slip)
    zip_slip = _make_tar_gz(
        [
            ("repo-main/skills/ok/SKILL.md", _skill_md("ok"), tarfile.REGTYPE, ""),
            ("repo-main/skills/ok/../../escape.txt", b"boom", tarfile.REGTYPE, ""),
        ]
    )
    with pytest.raises(DomainError) as exc_slip:
        extract_skill_from_tar_bytes(zip_slip, source="o/r", skill_id="ok")
    assert exc_slip.value.code == "UNSAFE_SKILL_ARCHIVE"

    # 2. Absolute path
    abs_tar = _make_tar_gz(
        [
            ("/etc/passwd", b"root:x:0:0", tarfile.REGTYPE, ""),
        ]
    )
    with pytest.raises(DomainError) as exc_abs:
        extract_skill_from_tar_bytes(abs_tar, source="o/r", skill_id="ok")
    assert exc_abs.value.code == "UNSAFE_SKILL_ARCHIVE"

    # 3. Symlink
    sym_tar = _make_tar_gz(
        [
            ("repo-main/skills/ok/SKILL.md", _skill_md("ok"), tarfile.REGTYPE, ""),
            ("repo-main/skills/ok/evil", None, tarfile.SYMTYPE, "/etc/passwd"),
        ]
    )
    with pytest.raises(DomainError) as exc_sym:
        extract_skill_from_tar_bytes(sym_tar, source="o/r", skill_id="ok")
    assert exc_sym.value.code == "UNSAFE_SKILL_ARCHIVE"

    # 4. Hardlink
    lnk_tar = _make_tar_gz(
        [
            ("repo-main/skills/ok/SKILL.md", _skill_md("ok"), tarfile.REGTYPE, ""),
            ("repo-main/skills/ok/hard", None, tarfile.LNKTYPE, "repo-main/skills/ok/SKILL.md"),
        ]
    )
    with pytest.raises(DomainError) as exc_lnk:
        extract_skill_from_tar_bytes(lnk_tar, source="o/r", skill_id="ok")
    assert exc_lnk.value.code == "UNSAFE_SKILL_ARCHIVE"

    # 5. File count cap (>200)
    many_entries: list[tuple[str, bytes | None, int, str]] = [
        ("repo-main/skills/many/SKILL.md", _skill_md("many"), tarfile.REGTYPE, "")
    ]
    for i in range(MAX_SKILL_FILE_COUNT):
        many_entries.append((f"repo-main/skills/many/f_{i}.txt", b"a", tarfile.REGTYPE, ""))
    many_tar = _make_tar_gz(many_entries)
    with pytest.raises(DomainError) as exc_cnt:
        extract_skill_from_tar_bytes(many_tar, source="o/r", skill_id="many")
    assert exc_cnt.value.code == "SKILL_TOO_MANY_FILES"

    # 6. Extracted size cap (>10 MiB)
    huge_tar = _make_tar_gz(
        [
            ("repo-main/skills/big/SKILL.md", _skill_md("big"), tarfile.REGTYPE, ""),
            (
                "repo-main/skills/big/blob.bin",
                b"0" * (MAX_EXTRACTED_BYTES + 1),
                tarfile.REGTYPE,
                "",
            ),
        ]
    )
    with pytest.raises(DomainError) as exc_sz:
        extract_skill_from_tar_bytes(huge_tar, source="o/r", skill_id="big")
    assert exc_sz.value.code == "SKILL_TOO_LARGE"


def test_tarball_discovery_priority_dirs_and_root_fallback() -> None:
    # Curated directory discovery
    curated = _make_tar_gz(
        [
            (
                "repo-abc/skills/.curated/code-audit/SKILL.md",
                _skill_md("code-audit", "Audit code carefully."),
                tarfile.REGTYPE,
                "",
            ),
            (
                "repo-abc/skills/.curated/code-audit/scripts/check.py",
                b"print('checked')\n",
                tarfile.REGTYPE,
                "",
            ),
        ]
    )
    pkg = extract_skill_from_tar_bytes(curated, source="acme/skills", skill_id="code-audit")
    assert pkg.name == "code-audit"
    assert sorted(pkg.files.keys()) == ["SKILL.md", "scripts/check.py"]

    # Root-level single skill when skill_id is omitted
    root_tar = _make_tar_gz(
        [
            (
                "single-sha/SKILL.md",
                _skill_md("solo-skill", "Single root skill."),
                tarfile.REGTYPE,
                "",
            ),
            ("single-sha/notes.md", b"extra", tarfile.REGTYPE, ""),
        ]
    )
    solo = extract_skill_from_tar_bytes(root_tar, source="acme/solo", skill_id=None)
    assert solo.name == "solo-skill"
    assert "notes.md" in solo.files

    # Ambiguous multi-skill repo without skill_id raises 400
    multi_tar = _make_tar_gz(
        [
            ("repo-sha/skills/alpha/SKILL.md", _skill_md("alpha", "A."), tarfile.REGTYPE, ""),
            ("repo-sha/skills/beta/SKILL.md", _skill_md("beta", "B."), tarfile.REGTYPE, ""),
        ]
    )
    with pytest.raises(DomainError) as exc_multi:
        extract_skill_from_tar_bytes(multi_tar, source="acme/multi", skill_id=None)
    assert exc_multi.value.code == "AMBIGUOUS_SKILL_SOURCE"

    # Malformed gzip archive raises 400
    with pytest.raises(DomainError) as exc_bad:
        extract_skill_from_tar_bytes(b"not-a-gzip", source="acme/bad", skill_id=None)
    assert exc_bad.value.code == "INVALID_SKILL_ARCHIVE"


@pytest.mark.asyncio
async def test_skills_sh_client_http_browse_search_preview_and_size_cap() -> None:
    archive_bytes = _make_tar_gz(
        [
            (
                "agent-skills-head/skills/vercel-react/SKILL.md",
                _skill_md("vercel-react", "Best React patterns."),
                tarfile.REGTYPE,
                "",
            ),
            (
                "agent-skills-head/skills/vercel-react/ref.md",
                b"ref content",
                tarfile.REGTYPE,
                "",
            ),
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/skills/trending/0":
            return httpx.Response(
                200,
                json={
                    "skills": [
                        {
                            "source": "vercel-labs/agent-skills",
                            "skillId": "vercel-react",
                            "name": "vercel-react",
                            "installs": 42000,
                            "isOfficial": True,
                        }
                    ],
                    "total": 1,
                    "hasMore": False,
                    "page": 0,
                },
            )
        if request.url.path == "/api/search":
            return httpx.Response(
                200,
                json={
                    "query": "react",
                    "skills": [
                        {
                            "id": "vercel-labs/agent-skills/vercel-react",
                            "source": "vercel-labs/agent-skills",
                            "skillId": "vercel-react",
                            "name": "vercel-react",
                            "installs": 42000,
                        }
                    ],
                },
            )
        if request.url.host == "codeload.github.com" and "oversized" in request.url.path:
            return httpx.Response(
                200,
                headers={"Content-Length": str(MAX_ARCHIVE_BYTES + 10)},
                content=b"small",
            )
        if request.url.host == "codeload.github.com":
            return httpx.Response(200, content=archive_bytes)
        return httpx.Response(404)

    client = SkillsShClient(transport=httpx.MockTransport(handler))

    page = await client.browse("trending", page=0)
    assert page.tab == "trending"
    assert len(page.skills) == 1
    assert page.skills[0].skill_id == "vercel-react"
    assert page.skills[0].is_official is True

    found = await client.search("react", limit=5)
    assert len(found) == 1
    assert found[0].id == "vercel-labs/agent-skills/vercel-react"

    with pytest.raises(DomainError) as exc_short:
        await client.search("a")
    assert exc_short.value.status_code == 400

    preview = await client.preview_remote_skill("vercel-labs/agent-skills", "vercel-react")
    assert preview.name == "vercel-react"
    assert preview.description == "Best React patterns."
    assert preview.files == ["SKILL.md", "ref.md"]

    with pytest.raises(DomainError) as exc_cap:
        await client.fetch_skill_archive("evil/oversized", "x")
    assert exc_cap.value.status_code == 413


# ---------------------------------------------------------------------------
# SkillStore & SkillFiles precedence
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_skill_store_install_list_detail_delete_and_protections(tmp_path: Path) -> None:
    bundled = tmp_path / "bundled"
    global_dir = tmp_path / "global"
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    _write_local_skill(bundled, "codebase-onboarding", "Bundled onboarding.")
    _write_local_skill(workspace / ".claude" / "skills", "external-claude", "External skill.")

    registry = FakeSkillsRegistry()
    store = SkillStore(bundled_dir=bundled, global_dir=global_dir, registry_client=registry)

    # Install into global scope
    installed_global = await store.install(
        SkillInstallRequest(
            source="vercel-labs/agent-skills",
            skill_id="react-patterns",
            scope="global",
        )
    )
    assert installed_global.scope == "global"
    assert installed_global.deletable is True
    assert installed_global.origin_source == "vercel-labs/agent-skills"
    prov_file = global_dir / "react-patterns" / PROVENANCE_FILENAME
    assert prov_file.is_file()
    assert json.loads(prov_file.read_text(encoding="utf-8"))["skill_id"] == "react-patterns"

    # Install into workspace scope
    installed_ws = await store.install(
        SkillInstallRequest(
            source="anthropics/skills",
            skill_id="pdf-helper",
            scope="workspace",
            workspace_path=str(workspace),
        )
    )
    assert installed_ws.scope == "workspace"
    assert installed_ws.deletable is True
    assert (workspace / ".agents" / "skills" / "pdf-helper" / "SKILL.md").is_file()

    # List installed across all scopes
    items = store.list_installed(workspace_path=str(workspace))
    by_key = {(item.scope, item.name): item for item in items}
    assert by_key[("bundled", "codebase-onboarding")].deletable is False
    assert by_key[("global", "react-patterns")].deletable is True
    assert by_key[("workspace", "pdf-helper")].deletable is True
    assert by_key[("workspace", "external-claude")].deletable is False

    # Detail excludes provenance sidecar
    detail = store.get_installed_detail("global", "react-patterns")
    assert "# React Patterns" in detail.content
    assert "references/hooks.md" in detail.files
    assert PROVENANCE_FILENAME not in detail.files

    # Bundled deletion rejected
    with pytest.raises(DomainError) as exc_bundled:
        await store.delete("bundled", "codebase-onboarding")  # type: ignore[arg-type]
    assert exc_bundled.value.status_code == 403

    # External .claude/skills never deleted via workspace delete
    with pytest.raises(DomainError) as exc_ext:
        await store.delete("workspace", "external-claude", workspace_path=str(workspace))
    assert exc_ext.value.status_code == 404
    assert (workspace / ".claude" / "skills" / "external-claude" / "SKILL.md").is_file()

    # Symlink deletion refused
    outside = tmp_path / "outside-target"
    outside.mkdir()
    (global_dir / "sym-skill").symlink_to(outside)
    with pytest.raises(DomainError) as exc_sym:
        await store.delete("global", "sym-skill")
    assert exc_sym.value.status_code == 403
    assert outside.is_dir()

    # Valid atomic reinstall/upgrade replaces existing directory cleanly
    registry.add_package(
        source="vercel-labs/agent-skills",
        skill_id="react-patterns",
        files={"SKILL.md": _skill_md("react-patterns", "Updated v2 patterns.")},
    )
    upgraded = await store.install(
        SkillInstallRequest(
            source="vercel-labs/agent-skills",
            skill_id="react-patterns",
            scope="global",
        )
    )
    assert upgraded.description == "Updated v2 patterns."
    assert not (global_dir / "react-patterns" / "references" / "hooks.md").exists()

    # Workspace install with invalid workspace_path raises 400
    with pytest.raises(DomainError) as exc_ws:
        await store.install(
            SkillInstallRequest(
                source="anthropics/skills",
                skill_id="pdf-helper",
                scope="workspace",
                workspace_path=str(tmp_path / "does-not-exist"),
            )
        )
    assert exc_ws.value.status_code == 400

    # Valid deletes succeed
    await store.delete("global", "react-patterns")
    assert not (global_dir / "react-patterns").exists()
    await store.delete("workspace", "pdf-helper", workspace_path=str(workspace))
    assert not (workspace / ".agents" / "skills" / "pdf-helper").exists()


@pytest.mark.asyncio
async def test_skill_files_precedence_bundled_lt_global_lt_workspace(tmp_path: Path) -> None:
    bundled = tmp_path / "bundled"
    global_dir = tmp_path / "global"
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    _write_local_skill(bundled, "shared-skill", "from-bundled")
    _write_local_skill(bundled, "only-bundled", "only-bundled")
    _write_local_skill(global_dir, "shared-skill", "from-global")
    _write_local_skill(global_dir, "only-global", "only-global")

    sf = SkillFiles(bundled, global_skills_dir=global_dir)
    catalog = RunCatalog(
        session_id="s-1",
        workspace_path=workspace,
        mode="ask",
        artifacts_dir=tmp_path / "artifacts",
        shell_guard=ShellGuard(),
    )

    # Without workspace override: global overrides bundled
    async with RunContext.activate(catalog):
        ls_res = sf.ls("/")
        names = [e["path"].lstrip("/") for e in (ls_res.entries or [])]
        assert set(names) == {"only-bundled", "only-global", "shared-skill"}
        read_res = sf.read("/shared-skill/SKILL.md")
        assert read_res.file_data is not None
        assert "from-global" in read_res.file_data["content"]

    # With workspace .agents/skills override: workspace overrides global
    _write_local_skill(workspace / ".agents" / "skills", "shared-skill", "from-workspace")
    async with RunContext.activate(catalog):
        read_res2 = sf.read("/shared-skill/SKILL.md")
        assert read_res2.file_data is not None
        assert "from-workspace" in read_res2.file_data["content"]


# ---------------------------------------------------------------------------
# Standalone FastAPI router integration tests
# ---------------------------------------------------------------------------


def _build_skills_test_app(tmp_path: Path) -> tuple[FastAPI, Path]:
    bundled = tmp_path / "bundled"
    global_dir = tmp_path / "global"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _write_local_skill(bundled, "codebase-onboarding", "Bundled onboarding.")

    app = FastAPI()
    register_exception_handlers(app)
    store = SkillStore(
        bundled_dir=bundled,
        global_dir=global_dir,
        registry_client=FakeSkillsRegistry(),
    )
    app.dependency_overrides[get_skill_store] = lambda: store
    app.include_router(skills_router)
    return app, workspace


@pytest.mark.asyncio
async def test_skills_api_full_lifecycle(tmp_path: Path) -> None:
    app, workspace = _build_skills_test_app(tmp_path)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Catalog browse
        cat_res = await client.get("/api/skills/catalog", params={"tab": "all-time", "page": 0})
        assert cat_res.status_code == 200
        cat_body = cat_res.json()
        assert cat_body["tab"] == "all-time"
        assert len(cat_body["skills"]) == 2

        # 2. Search
        search_res = await client.get("/api/skills/search", params={"q": "react", "limit": 10})
        assert search_res.status_code == 200
        assert search_res.json()[0]["skill_id"] == "react-patterns"

        # 3. Preview
        prev_res = await client.get(
            "/api/skills/preview",
            params={"source": "vercel-labs/agent-skills", "skill_id": "react-patterns"},
        )
        assert prev_res.status_code == 200
        assert prev_res.json()["name"] == "react-patterns"
        assert "references/hooks.md" in prev_res.json()["files"]

        # 4. Install global
        inst_res = await client.post(
            "/api/skills/install",
            json={
                "source": "vercel-labs/agent-skills",
                "skill_id": "react-patterns",
                "scope": "global",
            },
        )
        assert inst_res.status_code == 201
        assert inst_res.json()["scope"] == "global"

        # 5. Install workspace
        ws_inst = await client.post(
            "/api/skills/install",
            json={
                "source": "anthropics/skills",
                "skill_id": "pdf-helper",
                "scope": "workspace",
                "workspace_path": str(workspace),
            },
        )
        assert ws_inst.status_code == 201
        assert ws_inst.json()["scope"] == "workspace"

        # 6. List installed
        list_res = await client.get(
            "/api/skills/installed",
            params={"workspace_path": str(workspace)},
        )
        assert list_res.status_code == 200
        names = {(item["scope"], item["name"]) for item in list_res.json()}
        assert ("bundled", "codebase-onboarding") in names
        assert ("global", "react-patterns") in names
        assert ("workspace", "pdf-helper") in names

        # 7. Get installed detail
        det_res = await client.get("/api/skills/installed/global/react-patterns")
        assert det_res.status_code == 200
        assert "# React Patterns" in det_res.json()["content"]

        # 8. Delete bundled is 403
        del_bundled = await client.delete("/api/skills/bundled/codebase-onboarding")
        assert del_bundled.status_code == 403
        assert del_bundled.json()["error"]["code"] == "BUNDLED_SKILL_PROTECTED"

        # 9. Delete global & workspace
        del_global = await client.delete("/api/skills/global/react-patterns")
        assert del_global.status_code == 204
        del_ws = await client.delete(
            "/api/skills/workspace/pdf-helper",
            params={"workspace_path": str(workspace)},
        )
        assert del_ws.status_code == 204

        # 10. Confirm only bundled remains
        after_res = await client.get(
            "/api/skills/installed",
            params={"workspace_path": str(workspace)},
        )
        assert after_res.status_code == 200
        assert [item["name"] for item in after_res.json()] == ["codebase-onboarding"]


def test_create_app_mounts_skills_router_and_builtin_skills(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from backend.app.main import create_app
    from backend.app.shared.config import AppConfig

    cfg = AppConfig.for_data_dir(tmp_path / ".data", default_workspace_path=tmp_path)
    app = create_app(config=cfg)
    with TestClient(app) as client:
        resp = client.get("/api/skills/installed")
        assert resp.status_code == 200
        names = [(item["scope"], item["name"]) for item in resp.json()]
        assert ("bundled", "codebase-onboarding") in names
