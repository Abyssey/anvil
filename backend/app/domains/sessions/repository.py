"""SessionRepository Protocol and async SQLite implementation."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

import aiosqlite

from backend.app.domains.sessions.entities import (
    ChatMessage,
    PendingInterrupt,
    RunMode,
    Session,
    SessionStatus,
    ToolCallRecord,
)
from backend.app.shared.errors import SessionNotFoundError


class SessionRepository(Protocol):
    async def initialize(self) -> None: ...
    async def list_sessions(self) -> list[Session]: ...
    async def create_session(self, session: Session) -> Session: ...
    async def get_session(self, session_id: str) -> Session: ...
    async def update_session(self, session: Session) -> Session: ...
    async def delete_session(self, session_id: str) -> None: ...
    async def append_message(self, message: ChatMessage) -> ChatMessage: ...
    async def list_messages(self, session_id: str) -> list[ChatMessage]: ...


class SQLiteSessionRepository:
    """Async SQLite repository persisting sessions, transcripts, and pending interrupts."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path

    async def initialize(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute("PRAGMA journal_mode=WAL;")
            await db.execute("PRAGMA foreign_keys=ON;")
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    workspace_path TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    status TEXT NOT NULL,
                    pending_interrupt_json TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS messages (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    thinking TEXT NOT NULL DEFAULT '',
                    tool_calls_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL
                )
                """
            )
            await db.commit()

    async def list_sessions(self) -> list[Session]:
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM sessions ORDER BY updated_at DESC"
            ) as cursor:
                rows = await cursor.fetchall()
        return [self._row_to_session(row) for row in rows]

    async def create_session(self, session: Session) -> Session:
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute("PRAGMA foreign_keys=ON;")
            await db.execute(
                """
                INSERT INTO sessions (
                    id, title, workspace_path, mode, status,
                    pending_interrupt_json, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session.id,
                    session.title,
                    session.workspace_path,
                    session.mode.value,
                    session.status.value,
                    session.pending_interrupt.model_dump_json()
                    if session.pending_interrupt
                    else None,
                    session.created_at.isoformat(),
                    session.updated_at.isoformat(),
                ),
            )
            await db.commit()
        return session

    async def get_session(self, session_id: str) -> Session:
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM sessions WHERE id = ?",
                (session_id,),
            ) as cursor:
                row = await cursor.fetchone()
        if row is None:
            raise SessionNotFoundError(session_id)
        return self._row_to_session(row)

    async def update_session(self, session: Session) -> Session:
        updated = session.model_copy(update={"updated_at": datetime.now(UTC)})
        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute(
                """
                UPDATE sessions
                SET title = ?, workspace_path = ?, mode = ?, status = ?,
                    pending_interrupt_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    updated.title,
                    updated.workspace_path,
                    updated.mode.value,
                    updated.status.value,
                    updated.pending_interrupt.model_dump_json()
                    if updated.pending_interrupt
                    else None,
                    updated.updated_at.isoformat(),
                    updated.id,
                ),
            )
            if cursor.rowcount == 0:
                raise SessionNotFoundError(session.id)
            await db.commit()
        return updated

    async def delete_session(self, session_id: str) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute("PRAGMA foreign_keys=ON;")
            cursor = await db.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
            if cursor.rowcount == 0:
                raise SessionNotFoundError(session_id)
            await db.commit()

    async def append_message(self, message: ChatMessage) -> ChatMessage:
        tool_json = json.dumps([tc.model_dump(mode="json") for tc in message.tool_calls])
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute("PRAGMA foreign_keys=ON;")
            await db.execute(
                """
                INSERT INTO messages (
                    id, session_id, role, content, thinking, tool_calls_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    message.id,
                    message.session_id,
                    message.role,
                    message.content,
                    message.thinking,
                    tool_json,
                    message.created_at.isoformat(),
                ),
            )
            await db.commit()
        return message

    async def list_messages(self, session_id: str) -> list[ChatMessage]:
        await self.get_session(session_id)
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM messages WHERE session_id = ? ORDER BY created_at ASC",
                (session_id,),
            ) as cursor:
                rows = await cursor.fetchall()
        messages: list[ChatMessage] = []
        for row in rows:
            raw_tools = json.loads(row["tool_calls_json"])
            messages.append(
                ChatMessage(
                    id=row["id"],
                    session_id=row["session_id"],
                    role=row["role"],
                    content=row["content"],
                    thinking=row["thinking"],
                    tool_calls=[ToolCallRecord.model_validate(tc) for tc in raw_tools],
                    created_at=datetime.fromisoformat(row["created_at"]),
                )
            )
        return messages

    @staticmethod
    def _row_to_session(row: aiosqlite.Row) -> Session:
        raw_int = row["pending_interrupt_json"]
        pending = PendingInterrupt.model_validate_json(raw_int) if raw_int else None
        return Session(
            id=row["id"],
            title=row["title"],
            workspace_path=row["workspace_path"],
            mode=RunMode(row["mode"]),
            status=SessionStatus(row["status"]),
            pending_interrupt=pending,
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )
