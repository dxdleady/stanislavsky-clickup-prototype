"""mock_cost.py — оценка стоимости + порог (сценарий 06). Не в исходной
структуре репозитория явно — добавлен по решению docs/00_overview.md §2
("без него сценарий 06 нечем сгенерировать")."""
from __future__ import annotations

from agents._shared import build_event
from core.events import AgentEvent
from core.ids import EventSeqCounter, new_correlation_id
from core.state_machine import APPROVAL_THRESHOLD_USD_DEFAULT

DEFAULT_AGENT_ID = "cost.estimator"


def estimate(
    task_id: str,
    stage: str,
    seq_counter: EventSeqCounter,
    cost_usd: float,
    *,
    threshold_usd: float = APPROVAL_THRESHOLD_USD_DEFAULT,
    agent_id: str = DEFAULT_AGENT_ID,
) -> AgentEvent:
    over_threshold = cost_usd > threshold_usd
    text = (
        f"{agent_id}: оценка ${cost_usd:.2f}, порог ${threshold_usd:.2f} — "
        + ("отправляю на approve." if over_threshold else "в пределах, продолжаю.")
    )
    return build_event(
        kind="decision_request",
        agent_id=agent_id,
        task_id=task_id,
        stage=stage,
        correlation_id=new_correlation_id(),
        event_seq=seq_counter.next(task_id),
        payload={"text": text},
        cost_usd=cost_usd,
        requires_human=over_threshold,
    )
