"""Unit tests for pure-stdlib CJK Han guard, stream sanitizer, and LanguageGuardMiddleware."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from backend.app.domains.agent.cjk_guard import (
    CjkAllowance,
    CjkStreamSanitizer,
    detect_broken_grep_pattern,
    detect_broken_shell_cjk_regex,
    extract_cjk_han_chars,
    is_cjk_han_char,
)
from backend.app.domains.agent.middleware.language_guard import LanguageGuardMiddleware
from backend.app.domains.agent.run_context import RunCatalog, RunContext
from backend.app.domains.agent.shell_guard import ShellGuard


def _make_catalog(tmp_path: Path, *, allowance: CjkAllowance | None = None) -> RunCatalog:
    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    art = tmp_path / "artifacts"
    art.mkdir(parents=True, exist_ok=True)
    return RunCatalog(
        session_id="sess-cjk",
        workspace_path=ws,
        mode="auto",
        artifacts_dir=art,
        shell_guard=ShellGuard(),
        cjk_allowance=allowance or CjkAllowance(),
    )


def test_is_cjk_han_preserves_korean_hangul_ascii_and_symbols() -> None:
    sample = "안녕하세요! Qwen 3.8 Flash 테스트입니다. [Code: def foo_123() -> bool] ★ ✅"
    assert extract_cjk_han_chars(sample) == set()
    assert not any(is_cjk_han_char(ch) for ch in sample)

    mixed = "먼저 配置文件을 확인하고 函数를 수정합니다."
    assert extract_cjk_han_chars(mixed) == {"配", "置", "文", "件", "函", "数"}


def test_cjk_allowance_distinguishes_negated_korean_complaint_vs_affirmative_request() -> None:
    # Negated complaint MUST NOT enable allow_all
    neg = CjkAllowance()
    neg.absorb_user_text("중국어 간체 나오지 않게 한국어로만 작성해줘")
    assert neg.allow_all is False
    assert neg.find_unallowed_chars("默认配置") == ["默", "认", "配", "置"]

    # Mentioning a specific Chinese token to fix allows ONLY those exact characters, not new ones
    specific = CjkAllowance()
    specific.absorb_user_text("코드에 '配置文件'이라는 오타가 있으니 한국어로 고쳐줘")
    assert specific.allow_all is False
    assert specific.find_unallowed_chars("配置文件") == []
    assert specific.find_unallowed_chars("默认函数") == ["默", "认", "函", "数"]

    # Affirmative request enables allow_all
    pos_ko = CjkAllowance()
    pos_ko.absorb_user_text("이 문서를 중국어로 번역해줘")
    assert pos_ko.allow_all is True
    assert pos_ko.find_unallowed_chars("默认配置") == []

    # Pure Chinese prompt enables allow_all
    pos_zh = CjkAllowance()
    pos_zh.absorb_user_text("你好，请帮我检查一下这个项目的结构")
    assert pos_zh.allow_all is True


def test_cjk_stream_sanitizer_handles_multi_chunk_tokens_and_orphan_punctuation() -> None:
    allowance = CjkAllowance()
    allowance.absorb_user_text("서비스 구조를 요약해줘")
    sanitizer = CjkStreamSanitizer(allowance)

    # Pure Chinese chunk becomes empty string (caller skips emitting empty token event)
    assert sanitizer.sanitize("好的") == ""
    # Adjacent fullwidth Chinese comma right after stripped CJK is also cleaned
    assert sanitizer.sanitize("，먼저 ") == "먼저 "
    assert sanitizer.sanitize("`service.py`의 函数") == "`service.py`의 "
    assert sanitizer.sanitize("`run_app`을 확인합니다。") == "`run_app`을 확인합니다."

    # Single-character streaming chunks with leading Chinese comma before stripped CJK
    char_sanitizer = CjkStreamSanitizer(allowance)
    per_char = "".join(char_sanitizer.sanitize(c) for c in "업데이트했습니다，请确认。")
    assert per_char == "업데이트했습니다."


def test_detect_broken_grep_and_shell_cjk_regexes() -> None:
    # Literal rg -F grep misused with Unicode range regexes
    assert detect_broken_grep_pattern(r"[\u4e00-\u9fff]") is not None
    assert detect_broken_grep_pattern(r"\p{Han}") is not None
    assert detect_broken_grep_pattern("[一-龥]") is not None
    assert detect_broken_grep_pattern("SessionService") is None
    assert detect_broken_grep_pattern("세션 삭제") is None

    # Shell sed/grep with POSIX-corrupting [\u4e00-\u9fff] bracket expression
    assert detect_broken_shell_cjk_regex(r"sed -i 's/[\u4e00-\u9fff]//g' app.py") is not None
    assert detect_broken_shell_cjk_regex(r"grep -rn '[\u4e00-\u9fff]' backend/") is not None
    assert detect_broken_shell_cjk_regex(r"grep -P '[\u4e00-\u9fff]' backend/") is not None
    # Legitimate shell commands pass untouched
    assert detect_broken_shell_cjk_regex("pytest -q tests/unit") is None
    assert detect_broken_shell_cjk_regex("rg -n 'SessionService' backend/") is None


@pytest.mark.asyncio
async def test_language_guard_middleware_sanitizes_prior_ai_history_on_model_call(
    tmp_path: Path,
) -> None:
    catalog = _make_catalog(tmp_path)
    mw = LanguageGuardMiddleware()

    contaminated_ai = AIMessage(
        content="먼저 配置文件을 읽고 다음 단계로 갑니다.",
        additional_kwargs={"reasoning_content": "我们需要先查看 service.py"},
    )
    req = ModelRequest(
        model=MagicMock(),
        messages=[
            HumanMessage(content="이 레포의 엔트리포인트를 찾아줘"),
            contaminated_ai,
        ],
        system_message=SystemMessage(content="System"),
    )

    captured_requests: list[ModelRequest] = []

    async def fake_handler(updated_req: ModelRequest) -> ModelResponse:
        captured_requests.append(updated_req)
        return ModelResponse(result=[AIMessage(content="엔트리포인트는 `main.py`입니다.")])

    async with RunContext.activate(catalog):
        await mw.awrap_model_call(req, fake_handler)

    assert len(captured_requests) == 1
    sent_ai = captured_requests[0].messages[1]
    assert isinstance(sent_ai, AIMessage)
    assert extract_cjk_han_chars(str(sent_ai.content)) == set()
    assert "먼저" in str(sent_ai.content)
    sent_reasoning = str(sent_ai.additional_kwargs.get("reasoning_content") or "")
    assert extract_cjk_han_chars(sent_reasoning) == set()
    assert "service.py" in sent_reasoning


@pytest.mark.asyncio
async def test_language_guard_middleware_blocks_unallowed_tool_args_and_allows_existing_file_cjk(
    tmp_path: Path,
) -> None:
    catalog = _make_catalog(tmp_path)
    catalog.cjk_allowance.absorb_user_text("로케일 파일과 앱 코드를 점검해줘")  # type: ignore[union-attr]
    # Existing file in workspace legitimately contains Han characters
    locale_file = catalog.workspace_path / "zh.json"
    locale_file.write_text('{"welcome": "欢迎使用"}\n', encoding="utf-8")
    app_file = catalog.workspace_path / "app.py"
    app_file.write_text("def greet() -> str:\n    return 'hello'\n", encoding="utf-8")

    mw = LanguageGuardMiddleware()
    handler = AsyncMock(
        return_value=ToolMessage(content="ok", name="edit_file", tool_call_id="tc-ok")
    )

    def _tc_req(name: str, args: dict[str, object], tc_id: str = "tc-1") -> MagicMock:
        req = MagicMock()
        req.tool_call = {"id": tc_id, "name": name, "args": args}
        req.state = {"messages": [HumanMessage(content="로케일 파일과 앱 코드를 점검해줘")]}
        return req

    async with RunContext.activate(catalog):
        # 1. Hallucinated Chinese grep is blocked before handler runs
        res_grep = await mw.awrap_tool_call(
            _tc_req("grep", {"pattern": "配置文件"}),
            handler,
        )
        assert isinstance(res_grep, ToolMessage)
        assert res_grep.status == "error"
        assert handler.await_count == 0

        # 2. Broken regex range in literal grep is blocked
        res_grep_re = await mw.awrap_tool_call(
            _tc_req("grep", {"pattern": r"[\u4e00-\u9fff]"}),
            handler,
        )
        assert isinstance(res_grep_re, ToolMessage)
        assert res_grep_re.status == "error"
        assert handler.await_count == 0

        # 3. Broken POSIX sed [\u4e00-\u9fff] command is blocked
        res_sed = await mw.awrap_tool_call(
            _tc_req("execute", {"command": r"sed -i 's/[\u4e00-\u9fff]//g' app.py"}),
            handler,
        )
        assert isinstance(res_sed, ToolMessage)
        assert res_sed.status == "error"
        assert handler.await_count == 0

        # 4. Writing hallucinated Chinese into English/Korean app.py is blocked
        res_edit_bad = await mw.awrap_tool_call(
            _tc_req(
                "edit_file",
                {
                    "file_path": "/app.py",
                    "old_string": "return 'hello'",
                    "new_string": "return '默认 인사말'",
                },
            ),
            handler,
        )
        assert isinstance(res_edit_bad, ToolMessage)
        assert res_edit_bad.status == "error"
        assert handler.await_count == 0

        # 5. Editing zh.json preserving its existing Han characters ("欢迎使用") is ALLOWED
        res_edit_ok = await mw.awrap_tool_call(
            _tc_req(
                "edit_file",
                {
                    "file_path": "/zh.json",
                    "old_string": '{"welcome": "欢迎使用"}',
                    "new_string": '{"welcome": "欢迎使用!"}',
                },
            ),
            handler,
        )
        assert isinstance(res_edit_ok, ToolMessage)
        assert res_edit_ok.status != "error"
        assert handler.await_count == 1
