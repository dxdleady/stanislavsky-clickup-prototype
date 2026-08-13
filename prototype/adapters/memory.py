"""In-memory TrackerAdapter — для sim/, тестов и демо без токена (ТЗ §4, критерий сдачи).

Реализует полный TrackerAdapter-протокол (adapters/base.py). Batcher/Router/Filter
здесь НЕТ — это Фаза 2-3 плана (docs/02_prototype_plan.md), не часть этого скелета.
Этот адаптер даёт достаточно, чтобы построить outbound happy path (сценарий 01)
и юнит-тесты на core/.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from adapters.base import DeliveryResult, TrackerAdapter
from core.clock import Clock
from core.events import AgentEvent, LinkedTaskRequest, Stage
from core.state_machine import CAMERA_PIPELINE, WorkflowConfig


@dataclass
class _Task:
    task_id: str
    status: Stage = "backlog"
    custom_fields: dict[str, Any] = field(default_factory=dict)
    feed: list[AgentEvent] = field(default_factory=list)
    linked_task_id: str | None = None
    workflow_kind: str = "follow_up"
    priority: str | None = None
    assignee_agent_id: str | None = None


@dataclass
class _Comment:
    comment_id: str
    task_id: str
    body: str
    parent_comment_id: str | None = None
    author: str = "system"
    received_at: datetime | None = None


class OutageError(RuntimeError):
    """Симуляция недоступности ClickUp (сценарий 07)."""


class MemoryTrackerAdapter:
    """Реализует TrackerAdapter (structural typing — Protocol, не наследование)."""

    def __init__(self, workflow: WorkflowConfig = CAMERA_PIPELINE) -> None:
        self._workflow = workflow
        self._tasks: dict[str, _Task] = {}
        self._comments: dict[str, _Comment] = {}
        self._comment_ids = itertools.count(1)
        self._task_ids = itertools.count(1)
        self._processed_correlation_ids: set[str] = set()
        self._delivery_log: list[str] = []  # correlation_id в порядке фактической доставки
        self._outage = False
        # Накопители "человек написал/сменил статус" для sim/system.py::System.flush_inbound() —
        # не часть TrackerAdapter-протокола, только для sim.
        self._new_human_comment_ids: list[str] = []
        self._new_status_changes: list[tuple[str, str, str]] = []
        # Нужен, чтобы simulate_human_comment штамповал реальное (fake-clock) время
        # СРАЗУ в момент вызова, а не в момент flush_inbound() — иначе clock.advance()
        # между "человек написал" и "система обработала" не отражается на батчинге.
        # System.__init__ подключает его сам (attach_clock) — конструктор адаптера
        # не меняется для мест, где он создаётся без System (существующие тесты).
        self._clock: Clock | None = None

    # --- TrackerAdapter protocol ---

    def publish(self, event: AgentEvent) -> DeliveryResult:
        if self._outage:
            return DeliveryResult(ok=False, detail="simulated outage")
        is_new_task = event.task_id not in self._tasks
        task = self._tasks.setdefault(event.task_id, _Task(task_id=event.task_id, status=event.stage))
        if not is_new_task and task.status != event.stage:
            if not self._workflow.is_valid_transition(task.status, event.stage):
                return DeliveryResult(ok=False, detail=f"invalid transition {task.status}->{event.stage}")
            task.status = event.stage
        task.feed.append(event)
        self._delivery_log.append(event.correlation_id)
        return DeliveryResult(ok=True)

    def create_linked_task(self, request: LinkedTaskRequest) -> str:
        new_id = f"linked-{next(self._task_ids)}"
        self._tasks[new_id] = _Task(
            task_id=new_id,
            status="backlog",
            linked_task_id=request.source_task_id,
            workflow_kind=request.workflow_kind,
            priority=request.priority,
            assignee_agent_id=request.assignee_agent_id,
        )
        return new_id

    def get_linked_workflow_task(self, task_id: str, workflow_kind: str) -> str | None:
        for candidate_id, task in self._tasks.items():
            if task.linked_task_id == task_id and task.workflow_kind == workflow_kind:
                return candidate_id
        return None

    def post_comment(self, task_id: str, body: str) -> str:
        comment_id = f"c{next(self._comment_ids)}"
        self._comments[comment_id] = _Comment(comment_id=comment_id, task_id=task_id, body=body)
        return comment_id

    def post_reply(self, parent_comment_id: str, body: str) -> str:
        parent = self._comments[parent_comment_id]
        comment_id = f"c{next(self._comment_ids)}"
        self._comments[comment_id] = _Comment(
            comment_id=comment_id, task_id=parent.task_id, body=body, parent_comment_id=parent_comment_id
        )
        return comment_id

    def set_status(self, task_id: str, status: Stage) -> None:
        self._tasks.setdefault(task_id, _Task(task_id=task_id)).status = status

    def set_custom_field(self, task_id: str, field_name: str, value: Any) -> None:
        self._tasks.setdefault(task_id, _Task(task_id=task_id)).custom_fields[field_name] = value

    def is_processed(self, correlation_id: str) -> bool:
        return correlation_id in self._processed_correlation_ids

    def mark_processed(self, correlation_id: str) -> None:
        self._processed_correlation_ids.add(correlation_id)

    # --- Sim/test helpers (не часть TrackerAdapter-протокола, но нужны для sim/tests) ---

    def simulate_outage(self, on: bool) -> None:
        self._outage = on

    def attach_clock(self, clock: Clock) -> None:
        """Вызывается System.__init__ — дальше simulate_human_comment штампует
        реальное время вызова (fake или real), не время flush_inbound()."""
        self._clock = clock

    def simulate_human_comment(self, task_id: str, body: str, author: str) -> str:
        """Человек оставил комментарий — попадает в очередь на flush_inbound(),
        не публикуется напрямую (в отличие от post_comment, который пишет от
        имени системы/агента)."""
        comment_id = f"c{next(self._comment_ids)}"
        received_at = self._clock.now() if self._clock is not None else None
        self._comments[comment_id] = _Comment(
            comment_id=comment_id, task_id=task_id, body=body, author=author, received_at=received_at
        )
        self._new_human_comment_ids.append(comment_id)
        return comment_id

    def simulate_status_change(self, task_id: str, new_status: str, author: str) -> None:
        """Человек сменил статус задачи руками (например approve payment-workflow)
        — попадает в очередь на flush_inbound(), обрабатывается отдельно от
        комментариев (не через Batcher — это команда, не чат, docs/00_overview.md §3)."""
        self._new_status_changes.append((task_id, new_status, author))

    def pop_new_human_comments(self) -> list[_Comment]:
        comments = [self._comments[cid] for cid in self._new_human_comment_ids]
        self._new_human_comment_ids = []
        return comments

    def pop_new_status_changes(self) -> list[tuple[str, str, str]]:
        changes = list(self._new_status_changes)
        self._new_status_changes = []
        return changes

    def get_task(self, task_id: str) -> _Task | None:
        return self._tasks.get(task_id)

    def get_status(self, task_id: str) -> Stage | None:
        task = self._tasks.get(task_id)
        return task.status if task else None

    def get_feed(self, task_id: str) -> list[AgentEvent]:
        task = self._tasks.get(task_id)
        return list(task.feed) if task else []

    def get_feed_all(self) -> list[AgentEvent]:
        """Все события по всем задачам — сценарий 07 (недоступность ClickUp)
        проверяет, что во время outage ничего не долетело ни до одной ленты."""
        events: list[AgentEvent] = []
        for task in self._tasks.values():
            events.extend(task.feed)
        return events

    def get_delivery_log(self) -> list[str]:
        return list(self._delivery_log)

    def get_replies(self, parent_comment_id: str) -> list[_Comment]:
        return [c for c in self._comments.values() if c.parent_comment_id == parent_comment_id]

    def snapshot(self, task_id: str) -> tuple[Stage | None, int]:
        task = self._tasks.get(task_id)
        return (task.status, len(task.feed)) if task else (None, 0)
