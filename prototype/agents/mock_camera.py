"""mock_camera.py — decision_request с рекомендацией варианта покрытия
(деки-трейс: "Три варианта покрытия. Рекомендую B... подтвердите до 10:00").
"""
from __future__ import annotations

from datetime import datetime

from agents._shared import build_event
from core.events import AgentEvent
from core.ids import EventSeqCounter, new_correlation_id

DEFAULT_AGENT_ID = "camera.ivanov"


def recommend_coverage(
    task_id: str,
    stage: str,
    seq_counter: EventSeqCounter,
    *,
    recommendation: str = "B",
    deadline: datetime | None = None,
    agent_id: str = DEFAULT_AGENT_ID,
) -> AgentEvent:
    return build_event(
        kind="decision_request",
        agent_id=agent_id,
        task_id=task_id,
        stage=stage,
        correlation_id=new_correlation_id(),
        event_seq=seq_counter.next(task_id),
        payload={
            "text": f"{agent_id}: варианты покрытия готовы, рекомендую {recommendation}.",
            "recommendation": recommendation,
        },
        requires_human=True,
        deadline=deadline,
    )
