"""Pure-stdlib CJK Han script guard, stream sanitizer, and broken grep/shell regex detector."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Affirmative user requests to write/translate/respond in Chinese or Hanja
_AFFIRMATIVE_CJK_KO_RE = re.compile(
    r"(?:중국어|간체자?|번체자?|한자)(?:로|으로)\s*"
    r"(?:작성|번역|답변|답해|출력|설명|정리|표기|변환|바꿔|말해|써)"
)
_AFFIRMATIVE_CJK_EN_RE = re.compile(
    r"\b(?:write|respond|reply|answer|translate|output|explain)\s+"
    r"(?:it\s+|this\s+|all\s+)?(?:in|into|to)\s+"
    r"(?:simplified\s+|traditional\s+)?(?:chinese|mandarin|hanja)\b",
    re.IGNORECASE,
)
# Meta-complaints forbidding Chinese must NOT accidentally unlock allow_all
_NEGATIVE_CJK_META_RE = re.compile(
    r"(?:중국어|간체자?|번체자?|한자)[^\n]{0,24}"
    r"(?:나오지|쓰지\s*마|금지|제거|지워|삭제|빼|없애|말고|마세요|오류|버그|꼬이)"
    r"|(?:\bno\b|\bavoid\b|\bwithout\b|\bnever\b|\bstop\b)\s+"
    r"(?:using\s+|outputting\s+)?(?:simplified\s+|traditional\s+)?chinese\b",
    re.IGNORECASE,
)

# Fixed-string `grep` (rg -F) misused with CJK Unicode range / property regexes
_BROKEN_GREP_ESCAPE_RE = re.compile(
    r"\\u(?:3[4-9a-fA-F]|4[0-9a-dA-D]|[4-9][0-9a-fA-F]|f[9aA])[0-9a-fA-F]{2}"
    r"|\\x\{[0-9a-fA-F]{3,6}\}"
    r"|\\p\{(?:Han|Script=Han|CJK)[^}]*\}"
    r"|\[[^\]]*\\u[0-9a-fA-F]{4}[^\]]*\]"
    r"|\[[\u3400-\u4dbf\u4e00-\u9fff]\s*-\s*[\u3400-\u4dbf\u4e00-\u9fff]\]"
)

# Shell commands passing [\uXXXX-...] into POSIX/GNU grep/sed/awk/tr (corrupts 0-9 / A-Z via 0-\)
_BROKEN_SHELL_UNICODE_BRACKET_RE = re.compile(
    r"\b(?:e?grep|sed|awk|tr)\b[^\n]*\[[^\]]*\\u[0-9a-fA-F]{4}"
)
_BROKEN_SHELL_SED_CJK_RANGE_RE = re.compile(
    r"\bsed\b[^\n]*\[[\u3400-\u4dbf\u4e00-\u9fff]\s*-\s*[\u3400-\u4dbf\u4e00-\u9fff]\]"
)

_ORPHAN_CJK_PUNCT = frozenset({"，", "、", "：", "；"})


def is_cjk_han_char(ch: str) -> bool:
    """Return True iff `ch` is a CJK Han ideograph (excludes Korean Hangul, ASCII, and symbols)."""
    if len(ch) != 1:
        return False
    cp = ord(ch)
    return (
        0x3400 <= cp <= 0x4DBF
        or 0x4E00 <= cp <= 0x9FFF
        or 0xF900 <= cp <= 0xFAFF
        or 0x20000 <= cp <= 0x2EBEF
    )


def is_hangul_char(ch: str) -> bool:
    """Return True iff `ch` is a Korean Hangul syllable or Jamo character."""
    if len(ch) != 1:
        return False
    cp = ord(ch)
    return (
        0xAC00 <= cp <= 0xD7AF
        or 0x1100 <= cp <= 0x11FF
        or 0x3130 <= cp <= 0x318F
        or 0xA960 <= cp <= 0xA97F
        or 0xD7B0 <= cp <= 0xD7FF
    )


def extract_cjk_han_chars(text: str) -> set[str]:
    """Return the set of distinct CJK Han characters present in `text`."""
    return {ch for ch in text if is_cjk_han_char(ch)}


def ordered_unique_cjk_han(text: str) -> list[str]:
    """Return distinct CJK Han characters in order of first appearance."""
    seen: set[str] = set()
    ordered: list[str] = []
    for ch in text:
        if is_cjk_han_char(ch) and ch not in seen:
            seen.add(ch)
            ordered.append(ch)
    return ordered


def detect_broken_grep_pattern(pattern: str) -> str | None:
    """Return an error hint if `pattern` passes CJK regex escapes to literal `grep` (`rg -F`)."""
    if not pattern:
        return None
    if _BROKEN_GREP_ESCAPE_RE.search(pattern):
        return (
            "The `grep` tool uses literal fixed-string matching (`rg -F`), not regexes. "
            "Unicode range/property patterns like `[\\u4e00-\\u9fff]`, `\\p{Han}`, or `[一-龥]` "
            "will never match CJK characters here. Do not grep for Chinese character ranges; "
            "search exact English symbols/identifiers or Korean strings, or use `read_file`."
        )
    return None


def detect_broken_shell_cjk_regex(command: str) -> str | None:
    """Return an error hint if a shell command uses broken `\\uXXXX` CJK bracket expressions."""
    if not command:
        return None
    if _BROKEN_SHELL_UNICODE_BRACKET_RE.search(command):
        return (
            "Blocked unsafe shell regex: `\\uXXXX` inside `[...]` in `grep`/`sed`/`awk`/`tr` "
            "is invalid in PCRE2 and in POSIX BRE/ERE expands `0-\\` across ASCII digits `0-9` "
            "and uppercase letters `A-Z`, corrupting source files. Use `read_file` and "
            "`edit_file` directly instead of shell CJK regexes."
        )
    if _BROKEN_SHELL_SED_CJK_RANGE_RE.search(command):
        return (
            "Blocked unsafe `sed` CJK character-range edit: collation order for `[一-龥]` varies "
            "by locale and risks corrupting files. Use `edit_file` or `write_file` instead."
        )
    return None


@dataclass
class CjkAllowance:
    """Tracks whether CJK Han characters are globally requested or observed in workspace files."""

    allow_all: bool = False
    observed_chars: set[str] = field(default_factory=set)

    def absorb_user_text(self, text: str) -> None:
        """Update allowance from a user prompt, mid-run steer, or HITL feedback string."""
        if not text:
            return
        has_negation = bool(_NEGATIVE_CJK_META_RE.search(text))
        if not has_negation and (
            _AFFIRMATIVE_CJK_KO_RE.search(text) or _AFFIRMATIVE_CJK_EN_RE.search(text)
        ):
            self.allow_all = True
            return

        cjk_chars = extract_cjk_han_chars(text)
        if not cjk_chars:
            return

        hangul_count = sum(1 for ch in text if is_hangul_char(ch))
        if len(cjk_chars) >= 4 and hangul_count == 0 and not has_negation:
            self.allow_all = True
            return

        # Specific CJK characters quoted by the user are allowed (e.g. fixing a typo or Hanja term)
        self.observed_chars.update(cjk_chars)

    def absorb_workspace_text(self, text: str) -> None:
        """Record CJK Han characters observed in legitimate workspace files (`read_file`/`grep`)."""
        if not text or self.allow_all:
            return
        cjk_chars = extract_cjk_han_chars(text)
        if cjk_chars:
            self.observed_chars.update(cjk_chars)

    def find_unallowed_chars(
        self,
        text: str,
        *,
        extra_allowed: set[str] | frozenset[str] = frozenset(),
    ) -> list[str]:
        """Return ordered unique CJK Han characters in `text` not permitted in this run."""
        if not text or self.allow_all:
            return []
        allowed = self.observed_chars | set(extra_allowed)
        return [ch for ch in ordered_unique_cjk_han(text) if ch not in allowed]

    def strip_unallowed_cjk(self, text: str) -> str:
        """Strip unallowed CJK Han characters and normalize adjacent Chinese punctuation."""
        if not text or self.allow_all:
            return text
        return CjkStreamSanitizer(self).sanitize(text)


class CjkStreamSanitizer:
    """Stateful chunk-by-chunk sanitizer stripping unallowed CJK Han tokens cleanly."""

    def __init__(self, allowance: CjkAllowance) -> None:
        self._allowance = allowance
        self._just_stripped = False
        self._pending_punct = ""

    def sanitize(self, chunk: str) -> str:
        if not chunk or self._allowance.allow_all:
            return chunk

        allowed = self._allowance.observed_chars
        out: list[str] = []
        for ch in chunk:
            if is_cjk_han_char(ch) and ch not in allowed:
                self._pending_punct = ""
                self._just_stripped = True
                continue
            if ch in _ORPHAN_CJK_PUNCT:
                if self._just_stripped:
                    self._just_stripped = False
                    continue
                if self._pending_punct:
                    out.append(self._pending_punct)
                self._pending_punct = ch
                continue
            if ch == "。":
                self._pending_punct = ""
                self._just_stripped = False
                out.append(".")
                continue
            if self._pending_punct:
                out.append(self._pending_punct)
                self._pending_punct = ""
            if self._just_stripped and ch == " " and out and out[-1] == " ":
                self._just_stripped = False
                continue
            self._just_stripped = False
            out.append(ch)
        return "".join(out)
