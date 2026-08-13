"""03 — Правка агенту прошлой итерации: новый тикет создан, приоритет назначен, ссылка на исходный, старая задача не мутирована."""
from __future__ import annotations

from datetime import datetime

from adapters.memory import MemoryTrackerAdapter
from core.clock import FakeClock
from sim.system import build_system


def run() -> None:
    clock = FakeClock(start=datetime(2026, 8, 12, 9, 0))
    tracker = MemoryTrackerAdapter()
    system = build_system(tracker=tracker, clock=clock)

    old_task = system.seed_done_task(task_id="SC-039/SHOT-03", agent="camera.ivanov")

    tracker.simulate_human_comment(old_task.task_id, "тут пальто было не то", author="human.producer")
    clock.advance(65)
    system.flush_inbound()

    action = system.router.last_action
    assert action is not None
    assert action.kind == "new_ticket"

    new_task_id = action.payload["created_task_id"]
    new_task = tracker.get_task(new_task_id)
    assert new_task is not None
    assert new_task.priority == "high"
    assert new_task.linked_task_id == old_task.task_id
    assert new_task.assignee_agent_id == "camera.ivanov"  # prev_agent_id исходной задачи

    # старая задача НЕ переоткрыта
    assert tracker.get_status(old_task.task_id) == "done"


if __name__ == "__main__":
    run()
