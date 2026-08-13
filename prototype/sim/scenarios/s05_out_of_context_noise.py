"""05 — Рандомный коммент вне контекста: производство не тронуто, реплика залогирована и эскалирована.

docs/04_test_plan.md, нюанс реализации: под настоящим детерминированным (без NLP)
роутером "вне контекста" нельзя определить по теме реплики — только по структуре:
@-упоминание agent_id, которого нет среди known_agent_ids задачи. Тема комментария
("а что с обедом") — просто человеческая деталь трейса, не то, на чём строится assert.
"""
from __future__ import annotations

from datetime import datetime

from adapters.memory import MemoryTrackerAdapter
from core.clock import FakeClock
from sim.system import build_system


def run() -> None:
    clock = FakeClock(start=datetime(2026, 8, 12, 9, 0))
    tracker = MemoryTrackerAdapter()
    system = build_system(tracker=tracker, clock=clock)
    task = system.seed_in_progress_task("SC-042/SHOT-07", agent="camera.ivanov")
    state_before = tracker.snapshot(task.task_id)

    tracker.simulate_human_comment(
        task.task_id, "а что с обедом сегодня, @craft_services в курсе?", author="human.producer"
    )
    clock.advance(65)
    system.flush_inbound()

    action = system.router.last_action
    assert action is not None
    assert action.kind == "escalate"
    assert tracker.snapshot(task.task_id) == state_before  # НИЧЕГО в производстве не изменилось
    assert len(system.escalation_log.entries) == 1


if __name__ == "__main__":
    run()
