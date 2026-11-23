"""SessionRepository Protocol and async SQLite implementation."""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

import aiosqlite

from backend.app.domains.sessions.entities import (
    ChatMessage,
    PendingInterrupt,
    Project,
    RunMode,
    Session,
    SessionStatus,
    ToolCallRecord,
)
from backend.app.shared.config import normalize_sqlite_journal_mode
from backend.app.shared.errors import DomainError, ProjectNotFoundError, SessionNotFoundError


class SessionRepository(Protocol):
    async def initialize(self) -> None: ...
    async def list_projects(self) -> list[Project]: ...
    async def get_project(self, project_id: str) -> Project: ...
    async def get_project_by_path(self, path: str) -> Project | None: ...
    async def create_project(self, project: Project) -> Project: ...
    async def delete_project(self, project_id: str) -> list[str]: ...
    async def list_sessions(self) -> list[Session]: ...
    async def create_session(self, session: Session) -> Session: ...
    async def get_session(self, session_id: str) -> Session: ...
    async def update_session(self, session: Session) -> Session: ...
    async def delete_session(self, session_id: str) -> None: ...
    async def append_message(self, message: ChatMessage) -> ChatMessage: ...
    async def list_messages(self, session_id: str) -> list[ChatMessage]: ...
    async def list_messages_page(
        self,
        session_id: str,
        *,
        before_id: str | None = None,
        limit: int = 20,
    ) -> tuple[list[ChatMessage], bool]: ...
    async def truncate_messages_from(
        self,
        session_id: str,
        from_message_id: str,
    ) -> list[ChatMessage]: ...


class SQLiteSessionRepository:
    """Async SQLite repository persisting sessions, transcripts, and pending interrupts."""

    def __init__(
        self,
        db_path: Path,
        *,
        journal_mode: str = "WAL",
        busy_timeout_ms: int = 15_000,
    ) -> None:
        self._db_path = db_path
        self._journal_mode = normalize_sqlite_journal_mode(journal_mode)
        self._busy_timeout_ms = max(1_000, busy_timeout_ms)
        self._synchronous = "FULL" if self._journal_mode == "DELETE" else "NORMAL"

    @asynccontextmanager
    async def _connect(self) -> AsyncIterator[aiosqlite.Connection]:
        timeout_sec = max(5.0, self._busy_timeout_ms / 1000.0)
        async with aiosqlite.connect(self._db_path, timeout=timeout_sec) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("PRAGMA foreign_keys=ON;")
            await db.execute(f"PRAGMA busy_timeout={self._busy_timeout_ms};")
            await db.execute(f"PRAGMA synchronous={self._synchronous};")
            yield db

    async def initialize(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        async with self._connect() as db:
            async with db.execute("PRAGMA journal_mode;") as cur:
                cur_row = await cur.fetchone()
                current_mode = str(cur_row[0]).upper() if cur_row and cur_row[0] else ""
            if current_mode != self._journal_mode:
                async with db.execute(f"PRAGMA journal_mode={self._journal_mode};") as mcur:
                    set_row = await mcur.fetchone()
                    current_mode = str(set_row[0]).upper() if set_row and set_row[0] else ""
            if current_mode != self._journal_mode:
                raise RuntimeError(
                    f"Failed to configure SQLite journal_mode='{self._journal_mode}' "
                    f"(effective='{current_mode}') for '{self._db_path}'."
                )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    path TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    project_id TEXT REFERENCES projects(id) ON DELETE CASCADE,
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
            async with db.execute("PRAGMA table_info(sessions)") as cursor:
                columns = {row["name"] for row in await cursor.fetchall()}
            if "project_id" not in columns:
                await db.execute("ALTER TABLE sessions ADD COLUMN project_id TEXT")

            # Backfill projects for any legacy sessions without a project_id
            async with db.execute(
                "SELECT DISTINCT workspace_path FROM sessions WHERE project_id IS NULL"
            ) as cursor:
                orphan_paths = [str(row["workspace_path"]) for row in await cursor.fetchall()]

            now_iso = datetime.now(UTC).isoformat()
            for raw_path in orphan_paths:
                async with db.execute(
                    "SELECT id FROM projects WHERE path = ?",
                    (raw_path,),
                ) as pcur:
                    existing = await pcur.fetchone()
                if existing is not None:
                    proj_id = str(existing["id"])
                else:
                    proj_id = uuid.uuid4().hex[:12]
                    proj_name = Path(raw_path).name or raw_path
                    await db.execute(
                        """
                        INSERT INTO projects (id, name, path, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (proj_id, proj_name, raw_path, now_iso, now_iso),
                    )
                await db.execute(
                    """
                    UPDATE sessions SET project_id = ?
                    WHERE workspace_path = ? AND project_id IS NULL
                    """,
                    (proj_id, raw_path),
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
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                )
                """
            )
            async with db.execute("PRAGMA table_info(messages)") as mcur:
                msg_columns = {row["name"] for row in await mcur.fetchall()}
            if "metadata_json" not in msg_columns:
                await db.execute(
                    "ALTER TABLE messages ADD COLUMN metadata_json TEXT NOT NULL DEFAULT '{}'"
                )
            await db.commit()

    async def list_projects(self) -> list[Project]:
        async with self._connect() as db, db.execute(
            "SELECT * FROM projects ORDER BY updated_at DESC"
        ) as cursor:
            rows = await cursor.fetchall()
        return [self._row_to_project(row) for row in rows]

    async def get_project(self, project_id: str) -> Project:
        async with self._connect() as db, db.execute(
            "SELECT * FROM projects WHERE id = ?",
            (project_id,),
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            raise ProjectNotFoundError(project_id)
        return self._row_to_project(row)

    async def get_project_by_path(self, path: str) -> Project | None:
        async with self._connect() as db, db.execute(
            "SELECT * FROM projects WHERE path = ?",
            (path,),
        ) as cursor:
            row = await cursor.fetchone()
        return self._row_to_project(row) if row is not None else None

    async def create_project(self, project: Project) -> Project:
        async with self._connect() as db:
            await db.execute(
                """
                INSERT INTO projects (id, name, path, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    project.id,
                    project.name,
                    project.path,
                    project.created_at.isoformat(),
                    project.updated_at.isoformat(),
                ),
            )
            await db.commit()
        return project

    async def delete_project(self, project_id: str) -> list[str]:
        async with self._connect() as db:
            async with db.execute(
                "SELECT id FROM sessions WHERE project_id = ?",
                (project_id,),
            ) as cursor:
                session_ids = [str(row["id"]) for row in await cursor.fetchall()]
            await db.execute("DELETE FROM sessions WHERE project_id = ?", (project_id,))
            cursor = await db.execute("DELETE FROM projects WHERE id = ?", (project_id,))
            if cursor.rowcount == 0:
                raise ProjectNotFoundError(project_id)
            await db.commit()
        return session_ids

    async def list_sessions(self) -> list[Session]:
        async with self._connect() as db, db.execute(
            "SELECT * FROM sessions ORDER BY updated_at DESC"
        ) as cursor:
            rows = await cursor.fetchall()
        return [self._row_to_session(row) for row in rows]

    async def create_session(self, session: Session) -> Session:
        async with self._connect() as db:
            await db.execute(
                """
                INSERT INTO sessions (
                    id, project_id, title, workspace_path, mode, status,
                    pending_interrupt_json, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session.id,
                    session.project_id,
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
        async with self._connect() as db, db.execute(
            "SELECT * FROM sessions WHERE id = ?",
            (session_id,),
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            raise SessionNotFoundError(session_id)
        return self._row_to_session(row)

    async def update_session(self, session: Session) -> Session:
        updated = session.model_copy(update={"updated_at": datetime.now(UTC)})
        async with self._connect() as db:
            cursor = await db.execute(
                """
                UPDATE sessions
                SET project_id = ?, title = ?, workspace_path = ?, mode = ?, status = ?,
                    pending_interrupt_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    updated.project_id,
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
        async with self._connect() as db:
            cursor = await db.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
            if cursor.rowcount == 0:
                raise SessionNotFoundError(session_id)
            await db.commit()

    async def append_message(self, message: ChatMessage) -> ChatMessage:
        tool_json = json.dumps([tc.model_dump(mode="json") for tc in message.tool_calls])
        meta_payload: dict[str, object] = {}
        if message.checkpoint_id is not None:
            meta_payload["checkpoint_id"] = message.checkpoint_id
        if message.changed_files:
            meta_payload["changed_files"] = list(message.changed_files)
        if message.verification_stamp is not None:
            meta_payload["verification_stamp"] = message.verification_stamp
        metadata_json = json.dumps(meta_payload, ensure_ascii=False)
        async with self._connect() as db:
            await db.execute(
                """
                INSERT INTO messages (
                    id, session_id, role, content, thinking, tool_calls_json,
                    metadata_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    message.id,
                    message.session_id,
                    message.role,
                    message.content,
                    message.thinking,
                    tool_json,
                    metadata_json,
                    message.created_at.isoformat(),
                ),
            )
            await db.commit()
        return message

    async def list_messages(self, session_id: str) -> list[ChatMessage]:
        await self.get_session(session_id)
        async with self._connect() as db, db.execute(
            "SELECT * FROM messages WHERE session_id = ? ORDER BY created_at ASC, rowid ASC",
            (session_id,),
        ) as cursor:
            rows = await cursor.fetchall()
        return [self._row_to_message(row) for row in rows]

    async def list_messages_page(
        self,
        session_id: str,
        *,
        before_id: str | None = None,
        limit: int = 20,
    ) -> tuple[list[ChatMessage], bool]:
        await self.get_session(session_id)
        async with self._connect() as db:
            if before_id is not None:
                async with db.execute(
                    "SELECT created_at, rowid FROM messages WHERE session_id = ? AND id = ?",
                    (session_id, before_id),
                ) as cur:
                    anchor = await cur.fetchone()
                if anchor is None:
                    return [], False
                async with db.execute(
                    """
                    SELECT * FROM messages
                    WHERE session_id = ?
                      AND (created_at < ? OR (created_at = ? AND rowid < ?))
                    ORDER BY created_at DESC, rowid DESC
                    LIMIT ?
                    """,
                    (
                        session_id,
                        anchor["created_at"],
                        anchor["created_at"],
                        anchor["rowid"],
                        limit + 1,
                    ),
                ) as cursor:
                    rows = await cursor.fetchall()
            else:
                async with db.execute(
                    """
                    SELECT * FROM messages
                    WHERE session_id = ?
                    ORDER BY created_at DESC, rowid DESC
                    LIMIT ?
                    """,
                    (session_id, limit + 1),
                ) as cursor:
                    rows = await cursor.fetchall()

        has_more = len(rows) > limit
        page_rows = list(reversed(rows[:limit]))
        return [self._row_to_message(row) for row in page_rows], has_more

    async def truncate_messages_from(
        self,
        session_id: str,
        from_message_id: str,
    ) -> list[ChatMessage]:
        await self.get_session(session_id)
        async with self._connect() as db:
            async with db.execute(
                "SELECT created_at, rowid FROM messages WHERE session_id = ? AND id = ?",
                (session_id, from_message_id),
            ) as cur:
                anchor = await cur.fetchone()
            if anchor is None:
                raise DomainError(
                    f"Message '{from_message_id}' not found in session '{session_id}'.",
                    status_code=404,
                    code="MESSAGE_NOT_FOUND",
                )
            async with db.execute(
                """
                SELECT * FROM messages
                WHERE session_id = ?
                  AND (created_at > ? OR (created_at = ? AND rowid >= ?))
                ORDER BY created_at ASC, rowid ASC
                """,
                (
                    session_id,
                    anchor["created_at"],
                    anchor["created_at"],
                    anchor["rowid"],
                ),
            ) as cursor:
                rows = await cursor.fetchall()

            deleted = [self._row_to_message(row) for row in rows]
            if deleted:
                placeholders = ", ".join("?" for _ in deleted)
                await db.execute(
                    f"DELETE FROM messages WHERE session_id = ? AND id IN ({placeholders})",
                    (session_id, *[m.id for m in deleted]),
                )
                await db.commit()
        return deleted

    @staticmethod
    def _row_to_message(row: aiosqlite.Row) -> ChatMessage:
        raw_tools = json.loads(row["tool_calls_json"])
        try:
            raw_meta_str = row["metadata_json"]
        except IndexError:
            raw_meta_str = "{}"
        try:
            meta = json.loads(raw_meta_str or "{}")
        except (TypeError, ValueError):
            meta = {}
        checkpoint_id = (
            str(meta["checkpoint_id"])
            if isinstance(meta.get("checkpoint_id"), str) and meta["checkpoint_id"]
            else None
        )
        raw_changed = meta.get("changed_files")
        changed_files = (
            [str(p) for p in raw_changed if isinstance(p, str)]
            if isinstance(raw_changed, list)
            else []
        )
        raw_stamp = meta.get("verification_stamp")
        verification_stamp = (
            raw_stamp
            if raw_stamp in {"passed", "failed", "none", "not_needed"}
            else None
        )
        return ChatMessage(
            id=row["id"],
            session_id=row["session_id"],
            role=row["role"],
            content=row["content"],
            thinking=row["thinking"],
            tool_calls=[ToolCallRecord.model_validate(tc) for tc in raw_tools],
            checkpoint_id=checkpoint_id,
            changed_files=changed_files,
            verification_stamp=verification_stamp,
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    @staticmethod
    def _row_to_project(row: aiosqlite.Row) -> Project:
        return Project(
            id=row["id"],
            name=row["name"],
            path=row["path"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    @staticmethod
    def _row_to_session(row: aiosqlite.Row) -> Session:
        raw_int = row["pending_interrupt_json"]
        pending = PendingInterrupt.model_validate_json(raw_int) if raw_int else None
        return Session(
            id=row["id"],
            project_id=row["project_id"],
            title=row["title"],
            workspace_path=row["workspace_path"],
            mode=RunMode(row["mode"]),
            status=SessionStatus(row["status"]),
            pending_interrupt=pending,
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )
