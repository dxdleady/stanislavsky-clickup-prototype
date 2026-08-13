"""Очередь с подтверждением — сценарий 07 (недоступность ClickUp).

Глобальный FIFO, без per-task партиционирования — см. docs/01_architecture_plan.md
§5.3 и docs/00_overview.md, явный предел прототипа (см. CLAUDE.md/06_open_questions.md).
Не импортирует из adapters/ — принимает произвольный `deliver`-callable, не
TrackerAdapter напрямую (core/ не знает о adapters/).
"""
from __future__ import annotations

from collections import deque
from typing import Callable

from core.events import AgentEvent, DeliveryResult


class OutboundQueue:
    def __init__(self, deliver: Callable[[AgentEvent], DeliveryResult]) -> None:
        self._deliver = deliver
        self._pending: deque[AgentEvent] = deque()

    def submit(self, event: AgentEvent) -> DeliveryResult:
        """Пробует доставить сразу; при неудаче — ставит в хвост очереди.

        Если очередь уже не пуста, новое событие сразу встаёт в хвост, не
        пытаясь доставиться раньше того, что уже ждёт — иначе нарушается
        глобальный FIFO (найдено ревью: старая версия пробовала доставить
        новое событие немедленно, даже когда более ранние всё ещё в очереди,
        и могла обогнать их при частичном восстановлении соединения)."""
        if self._pending:
            self._pending.append(event)
            return DeliveryResult(ok=False, detail="queued behind pending events")
        result = self._deliver(event)
        if not result.ok:
            self._pending.append(event)
        return result

    def pending_count(self) -> int:
        return len(self._pending)

    def flush(self) -> list[DeliveryResult]:
        """Досылает в исходном порядке (FIFO). Останавливается на первом всё
        ещё падающем событии — не перегоняет очередь (не переставляет местами
        то, что уже ждёт своей очереди)."""
        results: list[DeliveryResult] = []
        while self._pending:
            event = self._pending[0]
            result = self._deliver(event)
            results.append(result)
            if not result.ok:
                break
            self._pending.popleft()
        return results
