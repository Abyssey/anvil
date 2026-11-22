"""Pure-stdlib unified git diff parser and walkthrough hunk planner (Open SWE inspired)."""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Literal

DiffStatus = Literal["modified", "added", "deleted", "renamed", "untracked"]

_DIFF_GIT_RE = re.compile(r"^diff --git a/(.+?) b/(.+)$")
_HUNK_HEADER_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$")


@dataclass(frozen=True)
class ParsedHunk:
    path: str
    name: str
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    header: str
    lines: tuple[str, ...]


@dataclass
class ParsedFileDiff:
    path: str
    status: DiffStatus = "modified"
    hunks: list[ParsedHunk] = field(default_factory=list)


@dataclass(frozen=True)
class ParsedWalkthroughChunk:
    id: str
    title: str
    explanation: str
    file_hunks: dict[str, list[str]]


@dataclass(frozen=True)
class ParsedWalkthroughPlan:
    git_head: str
    chunks: list[ParsedWalkthroughChunk]
    other_files: list[str]


def _compute_hunk_name(
    lines: Sequence[str],
    *,
    old_start: int,
    new_start: int,
) -> str:
    old_line = old_start
    new_line = new_start
    for raw in lines:
        if raw.startswith("+") and not raw.startswith("+++"):
            return f"+{new_line}"
        if raw.startswith("-") and not raw.startswith("---"):
            return f"-{old_line}"
        if raw.startswith("\\"):
            continue
        old_line += 1
        new_line += 1
    return f"+{new_start}"


def parse_unified_diff(diff_text: str) -> list[ParsedFileDiff]:
    """Parse git unified diff output into structured files and named hunks."""
    files: list[ParsedFileDiff] = []
    current_file: ParsedFileDiff | None = None
    current_hunk_meta: tuple[int, int, int, int, str] | None = None
    current_hunk_lines: list[str] = []

    def _flush_hunk() -> None:
        nonlocal current_hunk_meta, current_hunk_lines
        if current_file is None or current_hunk_meta is None:
            return
        old_start, old_count, new_start, new_count, header = current_hunk_meta
        name = _compute_hunk_name(
            current_hunk_lines,
            old_start=old_start,
            new_start=new_start,
        )
        current_file.hunks.append(
            ParsedHunk(
                path=current_file.path,
                name=name,
                old_start=old_start,
                old_count=old_count,
                new_start=new_start,
                new_count=new_count,
                header=header,
                lines=tuple(current_hunk_lines),
            )
        )
        current_hunk_meta = None
        current_hunk_lines = []

    for line in diff_text.splitlines():
        if match := _DIFF_GIT_RE.match(line):
            _flush_hunk()
            current_file = ParsedFileDiff(path=match.group(2))
            files.append(current_file)
            continue

        if current_file is None:
            continue

        if line.startswith("new file mode"):
            current_file.status = "added"
        elif line.startswith("deleted file mode"):
            current_file.status = "deleted"
        elif line.startswith("rename from "):
            current_file.status = "renamed"
        elif hunk_match := _HUNK_HEADER_RE.match(line):
            _flush_hunk()
            old_start = int(hunk_match.group(1))
            old_count = int(hunk_match.group(2) or "1")
            new_start = int(hunk_match.group(3))
            new_count = int(hunk_match.group(4) or "1")
            current_hunk_meta = (old_start, old_count, new_start, new_count, line)
            current_hunk_lines = []
        elif current_hunk_meta is not None:
            current_hunk_lines.append(line)

    _flush_hunk()
    return files


def build_default_walkthrough_plan(
    file_diffs: Sequence[ParsedFileDiff],
    *,
    git_head: str = "",
) -> ParsedWalkthroughPlan:
    """Group changed files by top-level area into ordered walkthrough review chunks."""
    grouped: dict[str, list[ParsedFileDiff]] = defaultdict(list)
    other_files: list[str] = []

    for fdiff in file_diffs:
        if not fdiff.hunks:
            other_files.append(fdiff.path)
            continue
        parts = PurePosixPath(fdiff.path).parts
        area = "/".join(parts[:2]) if len(parts) > 2 else (parts[0] if len(parts) > 1 else "root")
        grouped[area].append(fdiff)

    chunks: list[ParsedWalkthroughChunk] = []
    for idx, (area, items) in enumerate(grouped.items(), start=1):
        file_hunks = {item.path: [h.name for h in item.hunks] for item in items}
        file_names = ", ".join(PurePosixPath(item.path).name for item in items[:3])
        extra = f" (+{len(items) - 3} more)" if len(items) > 3 else ""
        hunk_total = sum(len(v) for v in file_hunks.values())
        chunks.append(
            ParsedWalkthroughChunk(
                id=f"chunk-{idx}",
                title=f"Updates in {area}",
                explanation=f"Modified {file_names}{extra} ({hunk_total} hunk(s)).",
                file_hunks=file_hunks,
            )
        )

    return ParsedWalkthroughPlan(git_head=git_head, chunks=chunks, other_files=other_files)


def validate_walkthrough_chunks(
    chunks: Sequence[ParsedWalkthroughChunk],
    file_diffs: Sequence[ParsedFileDiff],
) -> list[str]:
    """Validate that every referenced (path, hunk) pair exists in parsed diffs."""
    available: dict[str, set[str]] = {
        fdiff.path: {h.name for h in fdiff.hunks} for fdiff in file_diffs
    }
    errors: list[str] = []
    for chunk in chunks:
        for path, hunks in chunk.file_hunks.items():
            if path not in available:
                errors.append(f"{chunk.id}: unknown file '{path}'")
                continue
            for hunk_name in hunks:
                if hunk_name not in available[path]:
                    errors.append(f"{chunk.id}: unknown hunk '{hunk_name}' in '{path}'")
    return errors
