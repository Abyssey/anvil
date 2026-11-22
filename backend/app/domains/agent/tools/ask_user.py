"""Interactive `ask_user` LangGraph interrupt tool for structured single-choice clarification."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from langchain_core.tools import StructuredTool
from langgraph.types import interrupt
from pydantic import BaseModel, Field

_RECOMMENDED_PREFIX_RE = re.compile(r"^\s*\(recommended\)\s*", re.IGNORECASE)


class AskUserOptionInput(BaseModel):
    """One selectable option presented to the user."""

    label: str = Field(
        min_length=1,
        description="Concise option text without numbering. Do not include an 'Other' option.",
    )
    description: str | None = Field(
        default=None,
        description="Optional short 1-line rationale or trade-off.",
    )
    recommended: bool = Field(
        default=False,
        description="Set true on the single recommended option (placed first).",
    )


class AskUserQuestionInput(BaseModel):
    """A single-choice clarifying question with structured options."""

    question: str = Field(
        min_length=1,
        description="Clear, direct question to resolve an ambiguity or design choice.",
    )
    options: list[AskUserOptionInput] = Field(
        min_length=2,
        max_length=6,
        description="2 to 6 mutually exclusive choices; place the recommended option first.",
    )


class AskUserArgs(BaseModel):
    """Arguments accepted by the `ask_user` tool."""

    questions: list[AskUserQuestionInput] = Field(
        min_length=1,
        max_length=5,
        description="1 to 5 single-choice clarifying questions to ask the user.",
    )


def normalize_questions(
    questions: Sequence[AskUserQuestionInput],
) -> list[dict[str, object]]:
    """Normalize option labels, stripping duplicate `(Recommended)` prefixes."""
    normalized: list[dict[str, object]] = []
    for item in questions:
        raw_options: list[dict[str, object]] = []
        recommended_seen = False
        for opt in item.options:
            raw_label = opt.label.strip()
            has_prefix = bool(_RECOMMENDED_PREFIX_RE.match(raw_label))
            clean_label = _RECOMMENDED_PREFIX_RE.sub("", raw_label).strip() or raw_label
            is_recommended = (opt.recommended or has_prefix) and not recommended_seen
            if is_recommended:
                recommended_seen = True
            entry: dict[str, object] = {
                "label": clean_label,
                "recommended": is_recommended,
            }
            if opt.description and opt.description.strip():
                entry["description"] = opt.description.strip()
            raw_options.append(entry)
        normalized.append(
            {
                "question": item.question.strip(),
                "options": raw_options,
            }
        )
    return normalized


def format_resumed_answers(
    normalized_questions: Sequence[Mapping[str, object]],
    resume_payload: object,
) -> str:
    """Convert the resumed Command payload into a clear tool result string for the LLM."""
    if not isinstance(resume_payload, Mapping):
        return f"User response: {resume_payload}"

    decision = str(resume_payload.get("decision") or "answer").lower()
    raw_answers = resume_payload.get("answers")
    answers_list = raw_answers if isinstance(raw_answers, list) else []
    feedback = str(resume_payload.get("feedback") or "").strip()

    if decision == "skip" and not answers_list:
        note = (
            "User skipped the clarifying questions. "
            "Proceed using your recommended defaults."
        )
        if feedback:
            note += f"\nAdditional note: {feedback}"
        return note

    lines: list[str] = ["User answered clarifying questions:"]
    for idx, q in enumerate(normalized_questions, start=1):
        q_text = str(q.get("question") or "")
        ans: Mapping[str, Any] | None = (
            answers_list[idx - 1]
            if idx - 1 < len(answers_list) and isinstance(answers_list[idx - 1], Mapping)
            else None
        )
        if ans is None:
            for candidate in answers_list:
                if (
                    isinstance(candidate, Mapping)
                    and str(candidate.get("question") or "").strip() == q_text
                ):
                    ans = candidate
                    break

        if ans is None or bool(ans.get("skipped")):
            ans_text = "(skipped — use recommended default)"
        else:
            custom = str(ans.get("custom_text") or "").strip()
            selected = str(ans.get("selected_option") or "").strip()
            ans_text = custom or selected or "(skipped — use recommended default)"

        lines.append(f"{idx}. Q: {q_text}")
        lines.append(f"   A: {ans_text}")

    if feedback:
        lines.append(f"Additional feedback: {feedback}")
    return "\n".join(lines)


def _ask_user_impl(questions: list[AskUserQuestionInput]) -> str:
    normalized = normalize_questions(questions)
    summary = str(normalized[0]["question"]) if normalized else "Clarification required"
    resume_value = interrupt(
        {
            "kind": "user_question",
            "summary": summary,
            "questions": normalized,
        }
    )
    return format_resumed_answers(normalized, resume_value)


def build_ask_user_tool() -> StructuredTool:
    """Create the `ask_user` StructuredTool backed by LangGraph `interrupt()`."""
    return StructuredTool.from_function(
        func=_ask_user_impl,
        name="ask_user",
        description=(
            "Pause execution and ask the user 1 to 5 concise single-choice clarifying "
            "questions via interactive option buttons above the chat input. "
            "Put the recommended choice first with `recommended=true`. "
            "Do NOT include an 'Other' option — the UI automatically adds a free-text "
            "'Other' input and a 'Skip' button. Use this whenever requirements or design "
            "trade-offs are ambiguous before drafting a plan, or whenever an active skill "
            "instructs you to interview or ask the user."
        ),
        args_schema=AskUserArgs,
    )
