"""Mid-run steering inbox and before-model injection middleware."""

from __future__ import annotations

import threading
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import AgentState
from langchain_core.messages import HumanMessage
from langgraph.runtime import Runtime

from backend.app.domains.agent.run_context import RunContext


@dataclass(frozen=True)
class SteerItem:
    """Single user steering instruction queued for mid-run injection."""

    id: str
    raw_content: str
    pinned_skills: tuple[str, ...] = ()

    @property
    def formatted_content(self) -> str:
        if not self.pinned_skills:
            return self.raw_content
        skill_refs = ", ".join(f"`/skills/{s}/SKILL.md`" for s in self.pinned_skills)
        return f"[Pinned skills: {skill_refs}]\n\n{self.raw_content}"


@dataclass
class SteeringInbox:
    """Thread-safe per-run inbox drained at the next model boundary."""

    _items: deque[SteerItem] = field(default_factory=deque)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def enqueue(
        self,
        content: str,
        *,
        pinned_skills: list[str] | tuple[str, ...] | None = None,
    ) -> SteerItem:
        clean_pins = tuple(
            s.strip() for s in (pinned_skills or ()) if isinstance(s, str) and s.strip()
        )
        item = SteerItem(
            id=uuid.uuid4().hex[:12],
            raw_content=content,
            pinned_skills=clean_pins,
        )
        with self._lock:
            self._items.append(item)
        return item

    def drain(self) -> list[SteerItem]:
        with self._lock:
            if not self._items:
                return []
            drained = list(self._items)
            self._items.clear()
            return drained

    def has_pending(self) -> bool:
        with self._lock:
            return bool(self._items)


class SteeringQueueMiddleware(AgentMiddleware):
    """Drains pending mid-run user steering messages right before the next LLM call."""

    async def abefore_model(
        self,
        state: AgentState[Any],
        runtime: Runtime[Any],
    ) -> dict[str, Any] | None:
        del runtime
        catalog = RunContext.current_or_none()
        if catalog is None or catalog.steering_inbox is None:
            return None
        drained = catalog.steering_inbox.drain()
        if not drained:
            return None

        injected: list[HumanMessage] = []
        merged_pins: list[str] = list(state.get("pinned_skills") or [])
        for item in drained:
            injected.append(
                HumanMessage(
                    content=item.formatted_content,
                    additional_kwargs={
                        "lc_steer_id": item.id,
                        "lc_steer_raw": item.raw_content,
                        "lc_steer_pins": list(item.pinned_skills),
                    },
                )
            )
            for pin in item.pinned_skills:
                if pin not in merged_pins:
                    merged_pins.append(pin)

        update: dict[str, Any] = {"messages": injected}
        if merged_pins:
            update["pinned_skills"] = merged_pins
        return update
