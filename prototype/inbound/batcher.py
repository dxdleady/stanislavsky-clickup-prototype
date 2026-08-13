"""Batcher — реальное скользящее окно 60с + жёсткий потолок 300с
(docs/00_overview.md §4.4), не заглушка.

Честная поправка к тому, что раньше обещал докстринг заглушки ("форма
InboundEvent -> InboundBatch не меняется"): это физически невозможно — окно
должно закрываться и БЕЗ нового входящего события (пауза/потолок), а
синхронный add() не может знать заранее, придёт ли ещё одно событие. Поэтому
add() больше не возвращает батч сразу — окно закрывается через flush_due(),
которую вызывает внешний driver: webhook.py — фоновый asyncio-тик,
sim/system.py — System.flush_inbound() + FakeClock.advance(). Сам Batcher
таймер не держит и не спит — никакого time.sleep()/datetime.now() внутри
(CLAUDE.md), только Clock.now().
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from core.clock import Clock
from core.events import InboundBatch, InboundEvent
from core.ids import new_batch_id

WINDOW_SECONDS = 60
# Потолок — не из ТЗ, добавлен по умолчанию (docs/00_overview.md §4.4): без него
# человек, комментирующий раз в 55с бесконечно, никогда не получит ответа —
# окно продлевалось бы вечно.
MAX_WAIT_SECONDS = 300


@dataclass
class _OpenWindow:
    opened_at: datetime
    last_event_at: datetime
    events: list[InboundEvent] = field(default_factory=list)


class Batcher:
    def __init__(self, clock: Clock) -> None:
        self._clock = clock
        self._open: dict[str, _OpenWindow] = {}

    def add(self, event: InboundEvent, now: datetime | None = None) -> None:
        """Кладёт событие в окно его задачи — открывает новое, если его ещё
        нет, иначе продлевает существующее (last_event_at = сейчас).

        `now` — явное время события (например, реальное время его прихода,
        если оно обрабатывается позже момента получения); по умолчанию —
        текущее время Clock."""
        now = now if now is not None else self._clock.now()
        window = self._open.get(event.clickup_task_id)
        if window is None:
            window = _OpenWindow(opened_at=now, last_event_at=now)
            self._open[event.clickup_task_id] = window
        window.events.append(event)
        window.last_event_at = now

    def flush_due(self, now: datetime | None = None) -> list[InboundBatch]:
        """Закрывает все окна, чья пауза с последнего события >= WINDOW_SECONDS
        ИЛИ чей возраст с открытия >= MAX_WAIT_SECONDS. Вызывается внешним
        driver'ом на каждом тике/шаге — сам Batcher не планирует время."""
        current = now if now is not None else self._clock.now()
        closed: list[InboundBatch] = []
        for task_id in list(self._open):
            window = self._open[task_id]
            paused = (current - window.last_event_at).total_seconds() >= WINDOW_SECONDS
            expired = (current - window.opened_at).total_seconds() >= MAX_WAIT_SECONDS
            if paused or expired:
                closed.append(
                    InboundBatch(
                        batch_id=new_batch_id(),
                        clickup_task_id=task_id,
                        events=window.events,
                        window_opened_at=window.opened_at,
                        window_closed_at=current,
                    )
                )
                del self._open[task_id]
        return closed

    def has_open_window(self, task_id: str) -> bool:
        """Нужно дедлайн-чекеру (docs/00_overview.md §5.4, находка №3 грилла):
        если по task_id есть открытый батч, дедлайн формально не наступил,
        даже если ещё не обработан — политика молчания не должна сработать
        поверх уже пришедшего, но не долетевшего до Router ответа."""
        return task_id in self._open
