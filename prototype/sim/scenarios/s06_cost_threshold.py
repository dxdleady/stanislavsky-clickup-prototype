"""06 — Превышение порога стоимости: генерация заблокирована, payment-workflow создан, повторный approve не запускает дубль.

Адаптация от docs/04_test_plan.md: статус "blocked_pending_approval" из псевдокода
не существует ни в одном реальном WorkflowConfig — используется настоящая стадия
COSTUME_PIPELINE "cost_approval" (единственный конфиг с реальной cost-стадией,
CAMERA_PIPELINE её не имеет). `tracker.get_linked_payment_workflow(...)` заменён на
`tracker.get_linked_workflow_task(task_id, "payment_approval")` — метод, который
реально реализован (core/events.py::LinkedTaskRequest.workflow_kind).
"""
from __future__ import annotations

from datetime import datetime

from adapters.memory import MemoryTrackerAdapter
from core.clock import FakeClock
from core.state_machine import COSTUME_PIPELINE
from sim.system import build_system


def run() -> None:
    clock = FakeClock(start=datetime(2026, 8, 12, 9, 0))
    tracker = MemoryTrackerAdapter(workflow=COSTUME_PIPELINE)
    system = build_system(tracker=tracker, clock=clock, workflow=COSTUME_PIPELINE)

    task = system.seed_task("SC-050/SHOT-01", stage="ready", agent="cost.estimator")
    system.mock_cost_estimate(task.task_id, cost_usd=18.0, blocks_stage="cost_approval")  # > $15 дефолтный порог

    system.run_agent_step(task.task_id)  # Cost-агент шлёт decision_request

    assert tracker.get_status(task.task_id) == "cost_approval"
    payment_task_id = tracker.get_linked_workflow_task(task.task_id, "payment_approval")
    assert payment_task_id is not None

    generation_calls_before = system.mock_actor.call_count

    tracker.simulate_status_change(payment_task_id, "Approved", author="human.timur")
    system.flush_inbound()
    assert system.mock_actor.call_count == generation_calls_before + 1

    # повторный approve (двойной клик / повторная доставка webhook)
    tracker.simulate_status_change(payment_task_id, "Approved", author="human.timur")
    system.flush_inbound()
    assert system.mock_actor.call_count == generation_calls_before + 1  # НЕ +2


if __name__ == "__main__":
    run()
