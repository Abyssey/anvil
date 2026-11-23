"""Pure-stdlib 2-tier ShellGuard policy, verification classifier, and secret scrubbing."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum


class ShellAction(StrEnum):
    ALLOW = "allow"
    REQUIRE_APPROVAL = "require_approval"
    BLOCK = "block"


@dataclass(frozen=True)
class ShellVerdict:
    action: ShellAction
    reason: str


_BLOCK_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?:^|[;&|\s])sudo(?:\s|$)"), "Privilege escalation (sudo) is blocked"),
    (re.compile(r"(?:^|[;&|\s])su(?:\s|$)"), "Privilege escalation (su) is blocked"),
    (re.compile(r"\brm\s+-[^\s]*[rf][^\s]*\s+(?:/\s*$|/\*|~|\$HOME)"), "Root/home wipe is blocked"),
    (re.compile(r"\b(?:mkfs|fdisk|parted)\b"), "Filesystem formatting tools are blocked"),
    (re.compile(r"\bdd\s+if="), "Raw disk write (dd) is blocked"),
    (re.compile(r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:"), "Fork bomb pattern is blocked"),
    (
        re.compile(r"\b(?:curl|wget)\b[^|\n]*\|\s*(?:sh|bash|zsh)\b"),
        "Piping remote scripts to shell is blocked",
    ),
    (re.compile(r"(?:^|[\s/])\.env(?:\.local)?(?:\s|$|\"|')"), "Sensitive .env access is blocked"),
    (re.compile(r"(?:~|/)\.ssh/"), "SSH private key access is blocked"),
    (re.compile(r"(?:^|\s)/etc/shadow(?:\s|$)"), "Shadow password file access is blocked"),
    (re.compile(r"\.\./\.\./"), "Parent directory escape traversal is blocked"),
)

_DESTRUCTIVE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?:^|[;&|\s])(?:rm|rmdir)(?:\s|$)"), "File/directory deletion requires approval"),
    (
        re.compile(r"\bgit\s+(?:push|reset|clean|rebase|force-push)\b"),
        "Mutating git remote/history command requires approval",
    ),
    (
        re.compile(r"\bgit\s+(?:checkout\s+--(?:\s|$)|restore\s+(?!--staged\b))"),
        "Discarding working tree changes requires approval",
    ),
    (re.compile(r"(?:^|[;&|\s])chown(?:\s|$)"), "Ownership change requires approval"),
)

_VERIFICATION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?:^|[;&|\s/])pytest(?:\s|$)"),
    re.compile(r"\bpython[0-9.]*\s+-m\s+(?:pytest|unittest|mypy)\b"),
    re.compile(r"(?:^|[;&|\s/])(?:ruff|mypy|pyright|flake8|black\s+--check)(?:\s|$)"),
    re.compile(r"\b(?:npm|pnpm|yarn|bun)\s+(?:test|run\s+(?:test|typecheck|lint|check|build))\b"),
    re.compile(r"\bnpx\s+(?:vitest|jest|tsc|eslint|biome|playwright|astryx\s+doctor)\b"),
    re.compile(r"\bcargo\s+(?:test|check|clippy)\b"),
    re.compile(r"\bgo\s+(?:test|vet)\b"),
    re.compile(r"\bmake\s+(?:test|check|lint|verify)\b"),
)

_VERIFIER_RULE_EXACT_NAMES: frozenset[str] = frozenset(
    {
        "review.md",
        "claude.md",
        "agents.md",
        "test_architecture_boundaries.py",
    }
)

_VERIFIER_RULE_PREFIXES: tuple[str, ...] = (
    ".github/workflows/",
    ".claude/hooks/",
)

_SECRET_OUTPUT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(Bearer\s+)[A-Za-z0-9._\-]{6,}", re.IGNORECASE),
    re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}\b"),
    re.compile(r"\bAIza[A-Za-z0-9_\-]{8,}\b"),
    re.compile(r"\bghp_[A-Za-z0-9]{8,}\b"),
)


class ShellGuard:
    """Evaluates shell commands against hard deny rules, verification policies, and HITL gates."""

    def evaluate(
        self,
        command: str,
        mode: str,
        *,
        plan_approved: bool = False,
    ) -> ShellVerdict:
        normalized = command.strip()
        if not normalized:
            return ShellVerdict(ShellAction.BLOCK, "Empty command is not allowed")

        if mode == "ask":
            return ShellVerdict(
                ShellAction.BLOCK,
                "Shell execution is disabled in Ask mode.",
            )

        for pattern, reason in _BLOCK_PATTERNS:
            if pattern.search(normalized):
                return ShellVerdict(ShellAction.BLOCK, reason)

        for pattern, reason in _DESTRUCTIVE_PATTERNS:
            if pattern.search(normalized):
                return ShellVerdict(ShellAction.REQUIRE_APPROVAL, reason)

        if mode == "plan" and not plan_approved:
            return ShellVerdict(
                ShellAction.REQUIRE_APPROVAL,
                "Plan mode requires explicit approval before running shell commands.",
            )

        return ShellVerdict(ShellAction.ALLOW, "Safe command allowed.")

    @staticmethod
    def is_verification_command(command: str) -> bool:
        """Return True if the command runs an automated test, lint, typecheck, or build gate."""
        normalized = command.strip()
        if not normalized:
            return False
        return any(pattern.search(normalized) for pattern in _VERIFICATION_PATTERNS)

    @staticmethod
    def is_verifier_rule_path(virtual_or_rel_path: str) -> bool:
        """Return True if a workspace path is a governance/review/architecture check file.

        Prevents an autonomous agent from silently rewriting the rules or gates that judge its work.
        Virtual paths under `/artifacts/` and `/memories/` are excluded.
        """
        clean = virtual_or_rel_path.strip().lstrip("/")
        if not clean or clean.startswith(("artifacts/", "memories/", "skills/")):
            return False
        lower = clean.lower()
        if any(lower.startswith(prefix) for prefix in _VERIFIER_RULE_PREFIXES):
            return True
        basename = lower.rsplit("/", 1)[-1]
        return basename in _VERIFIER_RULE_EXACT_NAMES

    def scrub_output(self, text: str, extra_secrets: tuple[str, ...] = ()) -> str:
        """Mask known API key/token patterns and request-scoped secrets from shell output."""
        cleaned = text
        for secret in extra_secrets:
            if len(secret) >= 6:
                cleaned = cleaned.replace(secret, "[REDACTED]")
        cleaned = _SECRET_OUTPUT_PATTERNS[0].sub(r"\1[REDACTED]", cleaned)
        for pattern in _SECRET_OUTPUT_PATTERNS[1:]:
            cleaned = pattern.sub("[REDACTED]", cleaned)
        return cleaned
