"""Integration tests for interactive ask_user HITL clarification cards and Q&A persistence."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from fastapi.testclient import TestClient

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


def _parse_sse(raw_text: str) -> list[tuple[str, dict[str, object]]]:
    events: list[tuple[str, dict[str, object]]] = []
    current_event = "message"
    data_lines: list[str] = []
    for line in raw_text.splitlines():
        if line.startswith("event:"):
            current_event = line.removeprefix("event:").strip()
        elif line.startswith("data:"):
            data_lines.append(line.removeprefix("data:").strip())
        elif line == "" and data_lines:
            events.append((current_event, json.loads("\n".join(data_lines))))
            current_event = "message"
            data_lines = []
    if data_lines:
        events.append((current_event, json.loads("\n".join(data_lines))))
    return events


def _init_workspace(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
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
    (repo / "app.py").write_text("def greet() -> str:\n    return 'hi'\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)


def _headers() -> dict[str, str]:
    return {
        "X-LLM-Base-Url": "http://localhost:8001/v1",
        "X-LLM-Model": "qwen3.8-flash",
        "X-LLM-Api-Key": "sk-test-user-question-001",
        "X-LLM-Enable-Thinking": "true",
    }


def test_plan_mode_ask_user_interrupt_answer_persists_qa_and_drafts_plan(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    _init_workspace(workspace)

    model = ScriptedReasoningChatModel(
        turns=[
            ScriptedTurn(
                reasoning="Need to clarify storage and scope before drafting plan.",
                tool_calls=[
                    {
                        "name": "ask_user",
                        "args": {
                            "questions": [
                                {
                                    "question": "어느 저장소 전략을 사용할까요?",
                                    "options": [
                                        {
                                            "label": "(Recommended) SQLite 로컬 저장",
                                            "description": "별도 인프라 없이 즉시 동작",
                                        },
                                        {"label": "외부 PostgreSQL 연결"},
                                    ],
                                },
                                {
                                    "question": "범위를 어디까지 포함할까요?",
                                    "options": [
                                        {"label": "MVP 핵심 기능만", "recommended": True},
                                        {"label": "관리자 대시보드 포함"},
                                    ],
                                },
                            ]
                        },
                    }
                ],
            ),
            ScriptedTurn(
                reasoning="User answered both questions. Drafting implementation_plan.md.",
                tool_calls=[
                    {
                        "name": "write_file",
                        "args": {
                            "file_path": "/artifacts/implementation_plan.md",
                            "content": "# 구현 계획\n- SQLite + 커스텀 범위 반영",
                        },
                    }
                ],
            ),
            ScriptedTurn(content="답변을 반영해 구현 계획을 작성했습니다."),
        ]
    )

    cfg = AppConfig.for_data_dir(tmp_path / ".abyss-data", default_workspace_path=workspace)
    app = create_app(config=cfg, model_factory=_FakeModelFactory(model))

    with TestClient(app) as client:
        sess = client.post(
            "/api/sessions",
            json={"workspace_path": str(workspace), "mode": "plan"},
        ).json()
        sid = sess["id"]

        # 1. Initial prompt triggers ask_user interrupt
        r1 = client.post(
            f"/api/sessions/{sid}/messages/stream",
            headers=_headers(),
            json={"content": "새 저장 기능을 기획해줘", "mode": "plan"},
        )
        assert r1.status_code == 200
        ev1 = _parse_sse(r1.text)
        interrupts = [data for name, data in ev1 if name == "interrupt"]
        assert len(interrupts) == 1
        intr = interrupts[0]
        assert intr["kind"] == "user_question"
        questions = intr["questions"]
        assert isinstance(questions, list) and len(questions) == 2
        # Duplicate "(Recommended)" prefix stripped & normalized into recommended=True
        assert questions[0]["options"][0]["label"] == "SQLite 로컬 저장"
        assert questions[0]["options"][0]["recommended"] is True
        assert questions[1]["options"][0]["recommended"] is True

        # Session persisted with pending_interrupt across reload
        detail1 = client.get(f"/api/sessions/{sid}").json()
        assert detail1["session"]["status"] == "awaiting_approval"
        assert detail1["session"]["pending_interrupt"]["kind"] == "user_question"
        assert len(detail1["session"]["pending_interrupt"]["questions"]) == 2

        # Mismatched decision (approve on user_question) is rejected cleanly with 409
        bad = client.post(
            f"/api/sessions/{sid}/resume",
            headers=_headers(),
            json={"decision": "approve"},
        )
        assert bad.status_code == 409
        assert bad.json()["error"]["code"] == "INVALID_INTERRUPT_RESUME"

        # 2. Resume with structured single-choice + custom Other answer
        r2 = client.post(
            f"/api/sessions/{sid}/resume",
            headers=_headers(),
            json={
                "decision": "answer",
                "answers": [
                    {
                        "question": "어느 저장소 전략을 사용할까요?",
                        "selected_option": "SQLite 로컬 저장",
                    },
                    {
                        "question": "범위를 어디까지 포함할까요?",
                        "custom_text": "MVP + CLI 내보내기까지 포함",
                    },
                ],
            },
        )
        assert r2.status_code == 200
        ev2 = _parse_sse(r2.text)
        ev2_names = [name for name, _ in ev2]
        assert "artifact_updated" in ev2_names
        # Agent drafted implementation_plan.md -> triggers plan_review interrupt
        plan_interrupts = [
            data for name, data in ev2 if name == "interrupt" and data.get("kind") == "plan_review"
        ]
        assert len(plan_interrupts) == 1

        # Verify the LLM received the user's structured answers in the resumed turn
        assert len(model.recorded_calls) >= 2
        resumed_prompt = "\n".join(str(m.content) for m in model.recorded_calls[1])
        assert "SQLite 로컬 저장" in resumed_prompt
        assert "MVP + CLI 내보내기까지 포함" in resumed_prompt

        # Verify Q&A summary persisted cleanly as a user message in SQLite history
        detail2 = client.get(f"/api/sessions/{sid}").json()
        user_msgs = [m["content"] for m in detail2["messages"] if m["role"] == "user"]
        assert any(
            "SQLite 로컬 저장" in txt and "MVP + CLI 내보내기까지 포함" in txt
            for txt in user_msgs
        )


def test_ask_user_skip_persists_summary_and_continues(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    _init_workspace(workspace)

    model = ScriptedReasoningChatModel(
        turns=[
            ScriptedTurn(
                tool_calls=[
                    {
                        "name": "ask_user",
                        "args": {
                            "questions": [
                                {
                                    "question": "테스트 프레임워크를 선택할까요?",
                                    "options": [
                                        {"label": "pytest", "recommended": True},
                                        {"label": "unittest"},
                                    ],
                                }
                            ]
                        },
                    }
                ]
            ),
            ScriptedTurn(content="추천 기본값(pytest) 기준으로 진행합니다."),
        ]
    )

    cfg = AppConfig.for_data_dir(tmp_path / ".abyss-data", default_workspace_path=workspace)
    app = create_app(config=cfg, model_factory=_FakeModelFactory(model))

    with TestClient(app) as client:
        sess = client.post(
            "/api/sessions",
            json={"workspace_path": str(workspace), "mode": "ask"},
        ).json()
        sid = sess["id"]

        r1 = client.post(
            f"/api/sessions/{sid}/messages/stream",
            headers=_headers(),
            json={"content": "테스트 전략 알려줘", "mode": "ask"},
        )
        assert r1.status_code == 200
        assert any(name == "interrupt" for name, _ in _parse_sse(r1.text))

        r2 = client.post(
            f"/api/sessions/{sid}/resume",
            headers=_headers(),
            json={"decision": "skip"},
        )
        assert r2.status_code == 200
        ev2 = _parse_sse(r2.text)
        assert any(name == "done" for name, _ in ev2)

        resumed_prompt = "\n".join(str(m.content) for m in model.recorded_calls[1])
        assert "skipped" in resumed_prompt.lower()

        detail = client.get(f"/api/sessions/{sid}").json()
        assert detail["session"]["status"] == "idle"
        assert detail["session"]["pending_interrupt"] is None
        user_msgs = [m["content"] for m in detail["messages"] if m["role"] == "user"]
        assert any("건너뜀" in txt for txt in user_msgs)
