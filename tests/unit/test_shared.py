"""Unit tests for shared config, domain errors, and redacted LLM credentials."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.shared.config import AppConfig
from backend.app.shared.dependencies import LLMCredentialsDep
from backend.app.shared.errors import (
    ArtifactNotFoundError,
    CommandBlockedError,
    DomainError,
    InvalidInterruptResumeError,
    InvalidWorkspaceError,
    LLMSettingsRequiredError,
    PathEscapeError,
    SessionNotFoundError,
)
from backend.app.shared.http import register_exception_handlers
from backend.app.shared.llm_credentials import LLMCredentials
from tests.fakes.fake_chat_model import ScriptedReasoningChatModel, ScriptedTurn


def test_app_config_creates_data_directories(tmp_path: Path) -> None:
    cfg = AppConfig.for_data_dir(tmp_path / ".data", default_workspace_path=tmp_path)
    assert not cfg.data_dir.exists()
    cfg.ensure_directories()
    assert cfg.data_dir.is_dir()
    assert cfg.artifacts_root.is_dir()
    assert cfg.sqlite_path == tmp_path / ".data" / "sessions.db"
    assert cfg.bundled_skills_dir.parts[-4:] == ("domains", "skills", "builtin") or (
        cfg.bundled_skills_dir.parts[-3:] == ("domains", "skills", "builtin")
    )
    assert (cfg.bundled_skills_dir / "codebase-onboarding" / "SKILL.md").is_file()


def test_create_app_defers_directory_creation_to_lifespan_and_fastapi_cli_works(
    tmp_path: Path,
) -> None:
    import subprocess
    import sys

    from backend.app.main import create_app

    cfg = AppConfig.for_data_dir(tmp_path / ".data", default_workspace_path=tmp_path)
    app = create_app(config=cfg)
    assert not cfg.data_dir.exists()

    with TestClient(app):
        assert cfg.data_dir.is_dir()
        assert cfg.sqlite_path.is_file()

    fastapi_bin = Path(sys.executable).with_name("fastapi")
    proc = subprocess.run([str(fastapi_bin), "--version"], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


def test_llm_credentials_normalizes_endpoint_suffixes() -> None:
    c1 = LLMCredentials(
        base_url="http://localhost:8001/v1/chat/completions/",
        model="qwen3.8-flash",
        api_key="sk-test-123456",
    )
    assert c1.base_url == "http://localhost:8001/v1"

    c2 = LLMCredentials(
        base_url="https://dashscope-intl.aliyuncs.com/compatible-mode/v1/models",
        model="qwen3.8-flash",
        api_key="sk-test-123456",
    )
    assert c2.base_url == "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"


def test_domain_errors_expose_status_and_codes() -> None:
    assert LLMSettingsRequiredError().status_code == 428
    assert LLMSettingsRequiredError().code == "LLM_SETTINGS_REQUIRED"
    assert SessionNotFoundError("s1").status_code == 404
    assert ArtifactNotFoundError("walkthrough").status_code == 404
    assert InvalidWorkspaceError("/missing").status_code == 400
    assert PathEscapeError("../secret").status_code == 403
    assert CommandBlockedError("sudo rm").status_code == 403
    assert InvalidInterruptResumeError("none").status_code == 409


def test_llm_credentials_never_reveal_api_key() -> None:
    secret = "sk-super-secret-value-123456"
    creds = LLMCredentials(
        base_url="https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        model="qwen3.8-flash",
        api_key=secret,
        enable_thinking=True,
    )
    assert secret not in repr(creds)
    assert secret not in str(creds)
    assert secret not in creds.cache_fingerprint()
    assert "sk-super-secret" not in creds.redact_secrets(f"Authorization: Bearer {secret}")
    assert creds.redact_secrets(f"key={secret}") == "key=[REDACTED]"


def test_extract_llm_credentials_header_validation() -> None:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/ping")
    async def ping(creds: LLMCredentialsDep) -> dict[str, object]:
        return {
            "base_url": creds.base_url,
            "model": creds.model,
            "enable_thinking": creds.enable_thinking,
        }

    client = TestClient(app)
    missing = client.get("/ping")
    assert missing.status_code == 428
    assert missing.json()["error"]["code"] == "LLM_SETTINGS_REQUIRED"

    bad_url = client.get(
        "/ping",
        headers={
            "X-LLM-Base-Url": "ftp://invalid",
            "X-LLM-Model": "qwen3.8-flash",
            "X-LLM-Api-Key": "sk-test-123456",
        },
    )
    assert bad_url.status_code == 428

    ok = client.get(
        "/ping",
        headers={
            "X-LLM-Base-Url": "http://localhost:8001/v1/",
            "X-LLM-Model": "qwen3.8-flash",
            "X-LLM-Api-Key": "sk-test-123456",
            "X-LLM-Enable-Thinking": "false",
        },
    )
    assert ok.status_code == 200
    assert ok.json() == {
        "base_url": "http://localhost:8001/v1",
        "model": "qwen3.8-flash",
        "enable_thinking": False,
    }


@pytest.mark.asyncio
async def test_scripted_reasoning_chat_model_streams_reasoning_and_tokens() -> None:
    model = ScriptedReasoningChatModel(
        turns=[
            ScriptedTurn(
                reasoning="Checking workspace layout first.",
                content="Hello from Qwen.",
            )
        ]
    )
    chunks = [chunk async for chunk in model.astream("hi")]
    reasoning = "".join(c.additional_kwargs.get("reasoning_content", "") for c in chunks)
    text = "".join(c.content if isinstance(c.content, str) else "" for c in chunks)
    assert reasoning == "Checking workspace layout first."
    assert text == "Hello from Qwen."
    assert DomainError.__name__ == "DomainError"
