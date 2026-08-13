"""02 — Правка текущему агенту: реплика доставлена в контекст, агент ответил в треде, задача обновлена."""
from __future__ import annotations

from datetime import datetime

from adapters.memory import MemoryTrackerAdapter
from core.clock import FakeClock
from sim.system import build_system


def run() -> None:
    clock = FakeClock(start=datetime(2026, 8, 12, 9, 0))
    tracker = MemoryTrackerAdapter()
    system = build_system(tracker=tracker, clock=clock)

    task = system.seed_in_progress_task(task_id="SC-042/SHOT-07", agent="camera.ivanov")

    comment_id = tracker.simulate_human_comment(task.task_id, "убери контровой", author="human.producer")
    clock.advance(65)  # пауза закрывает окно батчера
    system.flush_inbound()

    action = system.router.last_action
    assert action is not None
    assert action.kind == "deliver_to_agent"
    assert action.target_agent_id == "camera.ivanov"

    replies = tracker.get_replies(comment_id)
    assert len(replies) == 1
    assert replies[0].parent_comment_id == comment_id  # ответ в ТОМ ЖЕ треде
    assert tracker.get_status(task.task_id) in {"in_progress"}  # новая итерация, не скачок стадии


if __name__ == "__main__":
    run()
