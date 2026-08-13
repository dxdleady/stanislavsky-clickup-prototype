"""Clock — абстракция времени, без которой sim/тесты на батчинг и дедлайны флакуют.

См. docs/01_architecture_plan.md §5. Ничто в core/ и inbound/ не должно
вызывать datetime.now()/time.sleep() напрямую — только через Clock.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime: ...


class RealClock:
    def now(self) -> datetime:
        return datetime.now()


class FakeClock:
    """Управляемое время для sim/tests — sliding window и дедлайны без реального sleep."""

    def __init__(self, start: datetime) -> None:
        self._now = start

    def now(self) -> datetime:
        return self._now

    def advance(self, seconds: float) -> datetime:
        self._now += timedelta(seconds=seconds)
        return self._now
