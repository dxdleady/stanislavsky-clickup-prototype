"""Общий билдер AgentEvent для mock-агентов — то же, что уже было
приватным `_event()` в live_demo_agent.py, вынесено, чтобы не плодить
пятую копию одного и того же конструктора события."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from core.events import AgentEvent, EventKind, Stage


def build_event(
    kind: EventKind,
    agent_id: str,
    task_id: str,
    stage: Stage,
    correlation_id: str,
    event_seq: int,
    *,
    payload: dict[str, Any] | None = None,
    cost_usd: float | None = None,
    requires_human: bool = False,
    deadline: datetime | None = None,
    thread_ref: str | None = None,
) -> AgentEvent:
    return AgentEvent(
        kind=kind,
        agent_id=agent_id,
        task_id=task_id,
        stage=stage,
        correlation_id=correlation_id,
        event_seq=event_seq,
        payload=payload or {},
        cost_usd=cost_usd,
        requires_human=requires_human,
        deadline=deadline,
        thread_ref=thread_ref,
    )
