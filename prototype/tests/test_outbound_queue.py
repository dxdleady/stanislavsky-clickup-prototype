"""Юнит-тесты core/outbound_queue.py — глобальный FIFO retry, останавливается
на первом всё ещё падающем событии (не перегоняет очередь)."""
from core.events import AgentEvent, DeliveryResult
from core.outbound_queue import OutboundQueue


def _event(correlation_id: str) -> AgentEvent:
    return AgentEvent(
        kind="report", agent_id="a", task_id="t", stage="backlog",
        correlation_id=correlation_id, event_seq=1,
    )


def test_submit_success_does_not_queue():
    queue = OutboundQueue(deliver=lambda e: DeliveryResult(ok=True))
    result = queue.submit(_event("c1"))
    assert result.ok is True
    assert queue.pending_count() == 0


def test_submit_failure_queues():
    queue = OutboundQueue(deliver=lambda e: DeliveryResult(ok=False, detail="outage"))
    result = queue.submit(_event("c1"))
    assert result.ok is False
    assert queue.pending_count() == 1


def test_flush_retries_in_fifo_order():
    delivered: list[str] = []
    outage = {"on": True}

    def deliver(event: AgentEvent) -> DeliveryResult:
        if outage["on"]:
            return DeliveryResult(ok=False, detail="outage")
        delivered.append(event.correlation_id)
        return DeliveryResult(ok=True)

    queue = OutboundQueue(deliver=deliver)
    for i in range(3):
        queue.submit(_event(f"c{i}"))
    assert queue.pending_count() == 3

    outage["on"] = False
    results = queue.flush()

    assert [r.ok for r in results] == [True, True, True]
    assert delivered == ["c0", "c1", "c2"]
    assert queue.pending_count() == 0


def test_submit_does_not_overtake_already_pending_events():
    """Регрессия (найдено ревью): раньше submit() всегда пробовал доставить
    новое событие немедленно, даже когда более ранние уже ждут в очереди —
    при частичном восстановлении соединения новое могло обогнать старые."""
    delivered: list[str] = []

    def deliver(event: AgentEvent) -> DeliveryResult:
        if event.correlation_id == "c0":
            return DeliveryResult(ok=False, detail="c0 всё ещё недоступен")
        delivered.append(event.correlation_id)
        return DeliveryResult(ok=True)

    queue = OutboundQueue(deliver=deliver)
    queue.submit(_event("c0"))  # падает, встаёт в очередь
    result = queue.submit(_event("c1"))  # сам по себе доставился бы успешно

    assert result.ok is False  # не обогнал c0
    assert delivered == []  # c1 не доставлен раньше c0
    assert queue.pending_count() == 2


def test_flush_stalls_at_first_still_failing_event():
    """Не перегоняет очередь — если первое событие всё ещё падает, остальные
    не пытаются доставиться раньше него."""
    delivered: list[str] = []
    state = {"outage": True}

    def deliver(event: AgentEvent) -> DeliveryResult:
        if state["outage"]:
            return DeliveryResult(ok=False, detail="outage")
        if event.correlation_id == "c0":
            return DeliveryResult(ok=False, detail="c0 конкретно всё ещё недоступен")
        delivered.append(event.correlation_id)
        return DeliveryResult(ok=True)

    queue = OutboundQueue(deliver=deliver)
    for i in range(3):
        queue.submit(_event(f"c{i}"))  # все три падают на outage, все встают в очередь
    assert queue.pending_count() == 3

    state["outage"] = False  # "восстановление" неполное — c0 конкретно всё ещё недоступен
    results = queue.flush()

    assert len(results) == 1
    assert results[0].ok is False
    assert delivered == []  # c1/c2 не тронуты, хотя сами по себе доставились бы
    assert queue.pending_count() == 3
