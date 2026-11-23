"""Shared async git subprocess helper for agent workspace inspection and git evidence."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


async def run_git(cwd: Path, *args: str, strip_output: bool = True) -> str:
    """Execute a read-only git command asynchronously, logging non-zero stderr at DEBUG."""
    proc = await asyncio.create_subprocess_exec(
        "git",
        *args,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        err_text = stderr.decode("utf-8", errors="replace").strip()
        if err_text:
            logger.debug(
                "git.command_failed",
                extra={
                    "event": "git.command_failed",
                    "git_args": list(args),
                    "returncode": proc.returncode,
                    "stderr": err_text[:300],
                },
            )
        return ""
    decoded = stdout.decode("utf-8", errors="replace")
    return decoded.strip() if strip_output else decoded
