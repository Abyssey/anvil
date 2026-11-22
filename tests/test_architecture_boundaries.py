"""AST boundary tests enforcing Abyss v2 DDD Bounded Context invariants."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

BACKEND_APP = Path(__file__).resolve().parents[1] / "backend" / "app"
STDLIB_MODULES = frozenset(sys.stdlib_module_names) | {"__future__"}

FORBIDDEN_TEST_DOUBLE_NAMES = {
    "FakeChatModel",
    "MockLLM",
    "mock_responder",
    "ScriptedDeepAgentChatModel",
    "ScriptedReasoningChatModel",
}


def _extract_imports(py_file: Path) -> set[str]:
    tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imports.add(node.module)
    return imports


def _top_level(module_name: str) -> str:
    return module_name.split(".", 1)[0]


def _assert_stdlib_only(py_file: Path) -> None:
    if not py_file.exists():
        return
    imports = _extract_imports(py_file)
    non_stdlib = sorted(m for m in imports if _top_level(m) not in STDLIB_MODULES)
    assert not non_stdlib, f"{py_file} must import stdlib only; found: {non_stdlib}"


def _assert_stdlib_or_pydantic(py_file: Path) -> None:
    if not py_file.exists():
        return
    allowed = STDLIB_MODULES | {"pydantic"}
    imports = _extract_imports(py_file)
    disallowed = sorted(m for m in imports if _top_level(m) not in allowed)
    assert not disallowed, f"{py_file} must import stdlib/pydantic only; found: {disallowed}"


def _has_prefix(module_name: str, prefixes: tuple[str, ...]) -> bool:
    return any(module_name == prefix or module_name.startswith(f"{prefix}.") for prefix in prefixes)


def test_pure_stdlib_domain_modules() -> None:
    _assert_stdlib_only(BACKEND_APP / "domains" / "agent" / "shell_guard.py")
    _assert_stdlib_only(BACKEND_APP / "domains" / "artifacts" / "walkthrough_diff.py")


def test_pure_domain_entities_stdlib_and_pydantic_only() -> None:
    _assert_stdlib_or_pydantic(BACKEND_APP / "domains" / "sessions" / "entities.py")
    _assert_stdlib_or_pydantic(BACKEND_APP / "domains" / "artifacts" / "entities.py")
    _assert_stdlib_or_pydantic(BACKEND_APP / "domains" / "skills" / "entities.py")


def test_agent_middleware_isolation() -> None:
    middleware_dir = BACKEND_APP / "domains" / "agent" / "middleware"
    if not middleware_dir.exists():
        return
    forbidden = ("backend.app.domains.sessions", "domains.sessions", "fastapi", "starlette")
    for py_file in middleware_dir.rglob("*.py"):
        bad = sorted(m for m in _extract_imports(py_file) if _has_prefix(m, forbidden))
        assert not bad, f"{py_file} violates agent middleware isolation: {bad}"


def test_agent_domain_isolation() -> None:
    agent_dir = BACKEND_APP / "domains" / "agent"
    if not agent_dir.exists():
        return
    forbidden = ("backend.app.domains.sessions", "domains.sessions", "fastapi", "starlette")
    for py_file in agent_dir.rglob("*.py"):
        if py_file.name == "router.py":
            continue
        bad = sorted(m for m in _extract_imports(py_file) if _has_prefix(m, forbidden))
        assert not bad, f"{py_file} violates agent domain isolation: {bad}"


def test_artifacts_domain_isolation() -> None:
    artifacts_dir = BACKEND_APP / "domains" / "artifacts"
    if not artifacts_dir.exists():
        return
    forbidden = (
        "backend.app.domains.sessions",
        "domains.sessions",
        "backend.app.domains.agent",
        "domains.agent",
        "fastapi",
        "starlette",
    )
    for py_file in artifacts_dir.rglob("*.py"):
        if py_file.name == "router.py":
            continue
        bad = sorted(m for m in _extract_imports(py_file) if _has_prefix(m, forbidden))
        assert not bad, f"{py_file} violates artifacts domain isolation: {bad}"


def test_skills_domain_isolation() -> None:
    skills_dir = BACKEND_APP / "domains" / "skills"
    if not skills_dir.exists():
        return
    forbidden = (
        "backend.app.domains.sessions",
        "domains.sessions",
        "backend.app.domains.agent",
        "domains.agent",
        "backend.app.domains.artifacts",
        "domains.artifacts",
        "fastapi",
        "starlette",
    )
    for py_file in skills_dir.rglob("*.py"):
        if py_file.name == "router.py":
            continue
        bad = sorted(m for m in _extract_imports(py_file) if _has_prefix(m, forbidden))
        assert not bad, f"{py_file} violates skills domain isolation: {bad}"


def test_routers_are_thin_http_adapters() -> None:
    domains_dir = BACKEND_APP / "domains"
    if not domains_dir.exists():
        return
    forbidden = ("sqlite3", "aiosqlite", "deepagents", "langgraph", "subprocess")
    for py_file in domains_dir.rglob("router.py"):
        bad = sorted(m for m in _extract_imports(py_file) if _has_prefix(m, forbidden))
        assert not bad, f"{py_file} must be a thin HTTP adapter; forbidden imports: {bad}"


def test_no_test_doubles_inside_backend_app() -> None:
    if not BACKEND_APP.exists():
        return
    for py_file in BACKEND_APP.rglob("*.py"):
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        defined = {
            node.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef)
        }
        overlap = sorted(defined & FORBIDDEN_TEST_DOUBLE_NAMES)
        assert not overlap, f"{py_file} defines forbidden test double(s): {overlap}"
