"""07 — Недоступность ClickUp: очередь копится, после восстановления досылается в исходном порядке."""
from __future__ import annotations

from datetime import datetime

from adapters.memory import MemoryTrackerAdapter
from core.clock import FakeClock
from sim.system import build_system


def run() -> None:
    clock = FakeClock(start=datetime(2026, 8, 12, 9, 0))
    tracker = MemoryTrackerAdapter()
    system = build_system(tracker=tracker, clock=clock)

    tracker.simulate_outage(True)

    events_sent = []
    for i in range(5):
        e = system.emit_agent_event(task_id=f"SC-0{i}/SHOT-01", kind="report")
        events_sent.append(e.correlation_id)

    assert tracker.get_feed_all() == []  # ничего не доставлено
    assert system.outbound_queue.pending_count() == 5  # производство не блокируется — очередь копится

    tracker.simulate_outage(False)
    system.flush_outbound()

    delivered_order = list(tracker.get_delivery_log())
    assert delivered_order == events_sent  # порядок доставки == порядку постановки


if __name__ == "__main__":
    run()
