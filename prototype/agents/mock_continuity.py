"""mock_continuity.py — flag о расхождении консистентности до приёмки, не после
(деки-трейс: "Оттенок пальто расходится с SC-039/03... до приёмки")."""
from __future__ import annotations

from agents._shared import build_event
from core.events import AgentEvent
from core.ids import EventSeqCounter, new_correlation_id

DEFAULT_AGENT_ID = "continuity.checker"


def flag_inconsistency(
    task_id: str,
    stage: str,
    seq_counter: EventSeqCounter,
    conflicting_task_id: str,
    *,
    detail: str = "",
    agent_id: str = DEFAULT_AGENT_ID,
) -> AgentEvent:
    text = f"{agent_id}: расхождение с {conflicting_task_id}."
    if detail:
        text = f"{text} {detail}"
    return build_event(
        kind="flag",
        agent_id=agent_id,
        task_id=task_id,
        stage=stage,
        correlation_id=new_correlation_id(),
        event_seq=seq_counter.next(task_id),
        payload={"text": text, "conflicting_task_id": conflicting_task_id},
        requires_human=True,
    )
