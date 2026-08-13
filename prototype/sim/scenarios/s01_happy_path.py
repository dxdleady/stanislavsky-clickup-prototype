"""01 — Happy path: Backlog → Done без вмешательств, все смены стадий видны в ленте."""
from __future__ import annotations

from datetime import datetime

from adapters.memory import MemoryTrackerAdapter
from core.clock import FakeClock
from sim.system import build_system


def run() -> None:
    clock = FakeClock(start=datetime(2026, 8, 12, 9, 0))
    tracker = MemoryTrackerAdapter()
    system = build_system(tracker=tracker, clock=clock)

    task = system.seed_backlog_task(task_id="SC-042/SHOT-07", agent="actor.disciple_ivanov")
    system.run_agent_to_completion(task.task_id)

    feed = tracker.get_feed(task.task_id)
    stages_seen = [e.stage for e in feed if e.kind == "report"]
    assert stages_seen == [
        "backlog", "planned", "in_progress", "ready_for_verification", "verification", "done",
    ], stages_seen
    assert tracker.get_status(task.task_id) == "done"
    # ни один InboundBatch не создан — вмешательств не было
    assert system.router.calls == []


if __name__ == "__main__":
    run()
