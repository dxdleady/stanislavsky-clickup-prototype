"""04 — Пачка комментариев: подряд объединены в одно действие, после паузы — новое окно, потолок 300с закрывает батч принудительно."""
from __future__ import annotations

from datetime import datetime

from adapters.memory import MemoryTrackerAdapter
from core.clock import FakeClock
from sim.system import build_system


def _consecutive_comments_merged() -> None:
    clock = FakeClock(start=datetime(2026, 8, 12, 9, 0))
    tracker = MemoryTrackerAdapter()
    system = build_system(tracker=tracker, clock=clock)
    task = system.seed_in_progress_task("SC-042/SHOT-07", agent="camera.ivanov")

    tracker.simulate_human_comment(task.task_id, "1", author="human.producer")
    clock.advance(10)
    tracker.simulate_human_comment(task.task_id, "2", author="human.producer")
    clock.advance(10)
    tracker.simulate_human_comment(task.task_id, "3", author="human.producer")
    clock.advance(65)  # пауза > 60с — окно должно закрыться
    system.flush_inbound()

    assert len(system.router.actions) == 1  # ОДНО согласованное действие
    assert len(system.router.actions[0].batch.events) == 3  # все три реплики внутри


def _pause_opens_new_window() -> None:
    clock = FakeClock(start=datetime(2026, 8, 12, 9, 0))
    tracker = MemoryTrackerAdapter()
    system = build_system(tracker=tracker, clock=clock)
    task = system.seed_in_progress_task("SC-042/SHOT-07", agent="camera.ivanov")

    tracker.simulate_human_comment(task.task_id, "1", author="human.producer")
    clock.advance(65)  # пауза
    tracker.simulate_human_comment(task.task_id, "2", author="human.producer")
    clock.advance(65)
    system.flush_inbound()

    assert len(system.router.actions) == 2  # два независимых батча/действия


def _hard_ceiling() -> None:
    """Допущение прототипа: потолок 300с, если человек комментирует чаще раза в минуту бесконечно."""
    clock = FakeClock(start=datetime(2026, 8, 12, 9, 0))
    tracker = MemoryTrackerAdapter()
    system = build_system(tracker=tracker, clock=clock)
    task = system.seed_in_progress_task("SC-042/SHOT-07", agent="camera.ivanov")
    for i in range(10):
        tracker.simulate_human_comment(task.task_id, str(i), author="human.producer")
        clock.advance(30)  # каждый раз внутри окна — sliding window никогда бы не закрылось само
    system.flush_inbound()

    # потолок принудительно закрыл батч раньше, чем через 10*30=300с непрерывного молчания
    assert len(system.router.actions) >= 1


def run() -> None:
    _consecutive_comments_merged()
    _pause_opens_new_window()
    _hard_ceiling()


if __name__ == "__main__":
    run()
