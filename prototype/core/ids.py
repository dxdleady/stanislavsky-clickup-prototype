"""Генерация id.

correlation_id (outbound-идемпотентность) и batch_id (inbound-группировка) —
РАЗНЫЕ пространства id, см. docs/00_overview.md §5.2. Не путать и не переиспользовать.
"""
from __future__ import annotations

import uuid
from collections import defaultdict


def new_correlation_id() -> str:
    return f"corr_{uuid.uuid4().hex}"


def new_batch_id() -> str:
    return f"batch_{uuid.uuid4().hex}"


class EventSeqCounter:
    """Монотонный event_seq per task_id — не переиспользуется параллельными агентами на одной задаче."""

    def __init__(self) -> None:
        self._counters: dict[str, int] = defaultdict(int)

    def next(self, task_id: str) -> int:
        self._counters[task_id] += 1
        return self._counters[task_id]
