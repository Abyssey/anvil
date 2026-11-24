"""Integration tests for mid-run session steering and chronological segment persistence."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from backend.app.domains.agent.model_adapter import ModelFactory
from backend.app.main import create_app
from backend.app.shared.config import AppConfig
from backend.app.shared.llm_credentials import LLMCredentials
from tests.fakes.fake_chat_model import ScriptedReasoningChatModel, ScriptedTurn


class _FakeModelFactory(ModelFactory):
    def __init__(self, model: ScriptedReasoningChatModel) -> None:
        self._model = model

    def build(self, creds: LLMCredentials) -> ScriptedReasoningChatModel:
        return self._model


def _headers() -> dict[str, str]:
    return {
        "X-LLM-Base-Url": "http://localhost:8001/v1",
        "X-LLM-Model": "qwen3.8-flash",
        "X-LLM-Api-Key": "sk-test-steer-key",
        "X-LLM-Enable-Thinking": "true",
    }


def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Tester"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    (repo / "app.py").write_text("def main() -> int:\n    return 1\n", encoding="utf-8")
    (repo / "auth.py").write_text("def check_token() -> bool:\n    return True\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)


@pytest.mark.asyncio
async def test_steer_idle_session_returns_409_session_not_running(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    _init_repo(workspace)
    model = ScriptedReasoningChatModel(turns=[ScriptedTurn(content="ok")])
    cfg = AppConfig.for_data_dir(tmp_path / ".data", default_workspace_path=workspace)
    app = create_app(config=cfg, model_factory=_FakeModelFactory(model))

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post(
                "/api/sessions",
                json={"workspace_path": str(workspace), "mode": "ask"},
            )
            sid = created.json()["id"]

            resp = await client.post(
                f"/api/sessions/{sid}/steer",
                json={"content": "지금 auth.py 봐줘"},
            )
            assert resp.status_code == 409
            assert resp.json()["error"]["code"] == "SESSION_NOT_RUNNING"


@pytest.mark.asyncio
async def test_mid_run_steer_injects_human_message_and_splits_timeline_chronologically(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "repo"
    _init_repo(workspace)
    model = ScriptedReasoningChatModel(
        turns=[
            ScriptedTurn(
                reasoning="먼저 app.py를 읽습니다.",
                tool_calls=[{"name": "read_file", "args": {"file_path": "/app.py"}}],
            ),
            ScriptedTurn(
                reasoning="사용자 개입 지시에 따라 auth.py를 확인합니다.",
                tool_calls=[{"name": "read_file", "args": {"file_path": "/auth.py"}}],
            ),
            ScriptedTurn(
                content="app.py에 이어 auth.py까지 모두 확인했습니다.",
            ),
        ]
    )
    cfg = AppConfig.for_data_dir(tmp_path / ".data", default_workspace_path=workspace)
    app = create_app(config=cfg, model_factory=_FakeModelFactory(model))

    async with app.router.lifespan_context(app):
        service = app.state.container.session_service
        creds = LLMCredentials(
            base_url="http://localhost:8001/v1",
            model="qwen3.8-flash",
            api_key="sk-test-steer-key",
            enable_thinking=True,
        )
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post(
                "/api/sessions",
                json={"workspace_path": str(workspace), "mode": "ask"},
            )
            sid = created.json()["id"]

            seen_events: list[tuple[str, dict[str, object]]] = []
            steered = False

            async for ev in service.stream_message(
                session_id=sid,
                content="app.py 분석해줘",
                mode=None,
                creds=creds,
            ):
                seen_events.append((ev.event, dict(ev.data)))
                if ev.event == "tool_start" and not steered:
                    steered = True
                    steer_res = await client.post(
                        f"/api/sessions/{sid}/steer",
                        json={
                            "content": "방향 전환: auth.py도 함께 확인해줘",
                            "pinned_skills": ["verify"],
                        },
                    )
                    assert steer_res.status_code == 202

            event_names = [name for name, _ in seen_events]
            assert "steer_applied" in event_names
            steer_idx = event_names.index("steer_applied")
            # Pre-steer tool ran before steer_applied, and post-steer tool ran after!
            assert "tool_start" in event_names[:steer_idx]
            assert "tool_start" in event_names[steer_idx + 1 :]

            # Verify the LLM actually saw the steered HumanMessage on its second call!
            assert len(model.recorded_calls) >= 2
            second_call_texts = [
                str(getattr(m, "content", ""))
                for m in model.recorded_calls[1]
                if getattr(m, "type", "") == "human"
            ]
            assert any("방향 전환: auth.py도 함께 확인해줘" in t for t in second_call_texts)
            assert any("/skills/verify/SKILL.md" in t for t in second_call_texts)

            # Verify SQLite transcript split chronologically:
            # [User 1] -> [Assistant Segment 1] -> [User Steer] -> [Assistant Segment 2]
            detail = (await client.get(f"/api/sessions/{sid}")).json()
            msgs = detail["messages"]
            assert [m["role"] for m in msgs] == ["user", "assistant", "user", "assistant"]
            assert msgs[0]["content"] == "app.py 분석해줘"
            assert "먼저 app.py를 읽습니다." in msgs[1]["thinking"]
            assert [tc["args"]["file_path"] for tc in msgs[1]["tool_calls"]] == ["/app.py"]
            assert msgs[2]["content"] == "방향 전환: auth.py도 함께 확인해줘"
            assert [tc["args"]["file_path"] for tc in msgs[3]["tool_calls"]] == ["/auth.py"]
            assert "auth.py까지 모두 확인했습니다." in msgs[3]["content"]


@pytest.mark.asyncio
async def test_late_steer_arriving_during_final_tokens_continues_in_same_stream(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "repo"
    _init_repo(workspace)
    model = ScriptedReasoningChatModel(
        turns=[
            ScriptedTurn(content="첫 번째 요약입니다.", after_chunk_delay_s=0.05),
            ScriptedTurn(content="추가 요청까지 반영한 최종 답변입니다."),
        ]
    )
    cfg = AppConfig.for_data_dir(tmp_path / ".data", default_workspace_path=workspace)
    app = create_app(config=cfg, model_factory=_FakeModelFactory(model))

    async with app.router.lifespan_context(app):
        service = app.state.container.session_service
        creds = LLMCredentials(
            base_url="http://localhost:8001/v1",
            model="qwen3.8-flash",
            api_key="sk-test-steer-key",
            enable_thinking=True,
        )
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post(
                "/api/sessions",
                json={"workspace_path": str(workspace), "mode": "ask"},
            )
            sid = created.json()["id"]

            seen_events: list[tuple[str, dict[str, object]]] = []
            steered = False

            async for ev in service.stream_message(
                session_id=sid,
                content="간단 요약해줘",
                mode=None,
                creds=creds,
            ):
                seen_events.append((ev.event, dict(ev.data)))
                # Steer arrives AFTER the first model call has already emitted its final tokens!
                if ev.event == "token" and not steered:
                    steered = True
                    steer_res = await client.post(
                        f"/api/sessions/{sid}/steer",
                        json={"content": "한 줄만 더 덧붙여줘"},
                    )
                    assert steer_res.status_code == 202

            event_names = [name for name, _ in seen_events]
            assert "steer_applied" in event_names

            detail = (await client.get(f"/api/sessions/{sid}")).json()
            msgs = detail["messages"]
            assert [m["role"] for m in msgs] == ["user", "assistant", "user", "assistant"]
            assert msgs[1]["content"] == "첫 번째 요약입니다."
            assert msgs[2]["content"] == "한 줄만 더 덧붙여줘"
            assert msgs[3]["content"] == "추가 요청까지 반영한 최종 답변입니다."


@pytest.mark.asyncio
async def test_mid_run_steer_rotates_checkpoint_for_granular_segment_revert(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "repo"
    _init_repo(workspace)
    model = ScriptedReasoningChatModel(
        turns=[
            ScriptedTurn(
                tool_calls=[
                    {
                        "name": "write_file",
                        "args": {"file_path": "/app.py", "content": "STEP_1 = True\n"},
                    }
                ],
            ),
            ScriptedTurn(
                tool_calls=[
                    {
                        "name": "write_file",
                        "args": {"file_path": "/auth.py", "content": "STEP_2 = True\n"},
                    }
                ],
            ),
            ScriptedTurn(content="두 파일 모두 수정 완료했습니다."),
        ]
    )
    cfg = AppConfig.for_data_dir(tmp_path / ".data", default_workspace_path=workspace)
    app = create_app(config=cfg, model_factory=_FakeModelFactory(model))

    async with app.router.lifespan_context(app):
        service = app.state.container.session_service
        creds = LLMCredentials(
            base_url="http://localhost:8001/v1",
            model="qwen3.8-flash",
            api_key="sk-test-steer-key",
            enable_thinking=True,
        )
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post(
                "/api/sessions",
                json={"workspace_path": str(workspace), "mode": "auto"},
            )
            sid = created.json()["id"]

            steered = False
            async for ev in service.stream_message(
                session_id=sid,
                content="app.py 먼저 수정해줘",
                mode=None,
                creds=creds,
            ):
                if ev.event == "tool_end" and not steered:
                    steered = True
                    steer_res = await client.post(
                        f"/api/sessions/{sid}/steer",
                        json={"content": "이어서 auth.py도 수정해줘"},
                    )
                    assert steer_res.status_code == 202

            assert (workspace / "app.py").read_text(encoding="utf-8") == "STEP_1 = True\n"
            assert (workspace / "auth.py").read_text(encoding="utf-8") == "STEP_2 = True\n"

            detail = (await client.get(f"/api/sessions/{sid}")).json()
            msgs = detail["messages"]
            assert [m["role"] for m in msgs] == ["user", "assistant", "user", "assistant"]

            # Revert ONLY the steered second segment — app.py from segment 1 must stay intact!
            rev = await client.post(
                f"/api/sessions/{sid}/revert",
                json={"message_id": msgs[3]["id"]},
            )
            assert rev.status_code == 200
            rev_body = rev.json()
            assert rev_body["rewound_user_prompt"] == "이어서 auth.py도 수정해줘"
            assert (workspace / "app.py").read_text(encoding="utf-8") == "STEP_1 = True\n"
            assert "def check_token()" in (workspace / "auth.py").read_text(encoding="utf-8")
