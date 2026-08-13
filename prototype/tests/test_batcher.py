"""Юнит-тесты inbound/batcher.py — реальное скользящее окно, FakeClock, без
реального sleep. Сценарные (04) прогоны — sim/scenarios/s04_batched_comments.py."""
from datetime import datetime

from core.clock import FakeClock
from core.events import InboundEvent
from inbound.batcher import Batcher


def _event(task_id: str, body: str, when: datetime) -> InboundEvent:
    return InboundEvent(clickup_task_id=task_id, author_user_id="u", kind="comment", body=body, received_at=when)


def test_single_event_closes_after_pause():
    clock = FakeClock(start=datetime(2026, 8, 13, 9, 0))
    batcher = Batcher(clock)
    batcher.add(_event("t", "1", clock.now()))

    assert batcher.flush_due() == []  # окно ещё не истекло
    clock.advance(60)
    closed = batcher.flush_due()
    assert len(closed) == 1
    assert closed[0].events[0].body == "1"


def test_window_extends_on_new_event_within_pause():
    clock = FakeClock(start=datetime(2026, 8, 13, 9, 0))
    batcher = Batcher(clock)
    batcher.add(_event("t", "1", clock.now()))
    clock.advance(30)
    batcher.add(_event("t", "2", clock.now()))
    clock.advance(30)  # 60с с первого события, но только 30с со второго

    assert batcher.flush_due() == []  # окно продлилось вторым событием, ещё не прошла пауза от него
    clock.advance(30)
    closed = batcher.flush_due()
    assert len(closed) == 1
    assert [e.body for e in closed[0].events] == ["1", "2"]


def test_hard_ceiling_forces_closure_without_pause():
    clock = FakeClock(start=datetime(2026, 8, 13, 9, 0))
    batcher = Batcher(clock)
    batcher.add(_event("t", "0", clock.now()))
    for i in range(1, 10):
        clock.advance(30)  # всегда внутри 60с окна — sliding window само не закрылось бы
        batcher.add(_event("t", str(i), clock.now()))

    assert batcher.flush_due() == []  # потолок (300с) ещё не достигнут (270с)
    clock.advance(30)  # ровно 300с с открытия
    closed = batcher.flush_due()
    assert len(closed) == 1
    assert len(closed[0].events) == 10


def test_distinct_tasks_do_not_cross_contaminate():
    clock = FakeClock(start=datetime(2026, 8, 13, 9, 0))
    batcher = Batcher(clock)
    batcher.add(_event("a", "1", clock.now()))
    batcher.add(_event("b", "1", clock.now()))
    clock.advance(60)
    closed = {batch.clickup_task_id: batch for batch in batcher.flush_due()}
    assert set(closed) == {"a", "b"}


def test_has_open_window():
    clock = FakeClock(start=datetime(2026, 8, 13, 9, 0))
    batcher = Batcher(clock)
    assert batcher.has_open_window("t") is False
    batcher.add(_event("t", "1", clock.now()))
    assert batcher.has_open_window("t") is True
    clock.advance(60)
    batcher.flush_due()
    assert batcher.has_open_window("t") is False
