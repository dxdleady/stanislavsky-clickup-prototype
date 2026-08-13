"""System — тестовый харнес, собирающий Queue+Filter+Router+Batcher поверх
MemoryTrackerAdapter (docs/04_test_plan.md "Общая инфраструктура тестов",
build_system(tracker, clock, filter_config)). Используется sim/scenarios/*.py
и tests/test_scenarios.py — не production-код.

Псевдокод в docs/04_test_plan.md — иллюстрация, не рабочий контракт; там, где
он расходится с тем, что реально существует в core/adapters (например
"blocked_pending_approval" — такой стадии нет ни в одном WorkflowConfig, или
`tracker.get_linked_payment_workflow` — у нас get_linked_workflow_task с
явным workflow_kind), эта реализация следует духу псевдокода, но использует
реальные имена. Отличия отмечены по месту.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from adapters.memory import MemoryTrackerAdapter
from agents import mock_actor, mock_cost
from agents._shared import build_event
from core.clock import Clock
from core.events import AgentEvent, DeliveryResult, InboundBatch, InboundEvent, LinkedTaskRequest, RouterAction, TaskState
from core.filter import DEFAULT_FILTER_CONFIG, Filter, FilterConfig, StageTracker
from core.ids import EventSeqCounter, new_correlation_id
from core.outbound_queue import OutboundQueue
from core.state_machine import APPROVAL_THRESHOLD_USD_DEFAULT, CAMERA_PIPELINE, WorkflowConfig
from inbound.batcher import Batcher
from inbound.router import RuleBasedRouter


@dataclass
class SeededTask:
    task_id: str


@dataclass
class RecordedAction:
    """RouterAction + сам батч, который к нему привёл — нужно тестам сценария
    04 (system.router.actions[0].batch.events)."""

    kind: str
    target_agent_id: str | None
    payload: dict
    batch: InboundBatch


@dataclass
class _EscalationLog:
    entries: list[dict] = field(default_factory=list)

    def record(self, task_id: str, reason: str) -> None:
        self.entries.append({"task_id": task_id, "reason": reason})


@dataclass
class _MockActorRecorder:
    """call_count не хранится в agents/mock_actor.py самом по себе (мок —
    чистая функция) — считает вызывающая сторона, здесь."""

    call_count: int = 0


class _RecordingRouter:
    """Оборачивает RuleBasedRouter — записывает каждый вызов, чтобы сценарии
    могли проверить, что реально произошло (docs/04_test_plan.md: `system.router.last_action`/`.actions`/`.calls`)."""

    def __init__(self, workflow: WorkflowConfig) -> None:
        self._router = RuleBasedRouter(workflow)
        self.actions: list[RecordedAction] = []
        self.calls = self.actions  # тот же список — "router.calls == []" (сценарий 01)

    def route(self, batch: InboundBatch, task_state: TaskState | None) -> RouterAction:
        action = self._router.route(batch, task_state)
        self.actions.append(
            RecordedAction(kind=action.kind, target_agent_id=action.target_agent_id, payload=action.payload, batch=batch)
        )
        return action

    @property
    def last_action(self) -> RecordedAction | None:
        return self.actions[-1] if self.actions else None


def build_system(
    tracker: MemoryTrackerAdapter,
    clock: Clock,
    filter_config: FilterConfig = DEFAULT_FILTER_CONFIG,
    workflow: WorkflowConfig = CAMERA_PIPELINE,
) -> "System":
    return System(tracker=tracker, clock=clock, filter_config=filter_config, workflow=workflow)


class System:
    def __init__(
        self,
        tracker: MemoryTrackerAdapter,
        clock: Clock,
        filter_config: FilterConfig = DEFAULT_FILTER_CONFIG,
        workflow: WorkflowConfig = CAMERA_PIPELINE,
    ) -> None:
        self.tracker = tracker
        self.clock = clock
        self.workflow = workflow
        tracker.attach_clock(clock)  # simulate_human_comment штампует время вызова, не flush_inbound()
        self._seq = EventSeqCounter()
        self._filter = Filter(filter_config)
        self._stage_tracker = StageTracker()
        self.router = _RecordingRouter(workflow)
        self.batcher = Batcher(clock)
        self.outbound_queue = OutboundQueue(deliver=tracker.publish)
        self.escalation_log = _EscalationLog()
        self.mock_actor = _MockActorRecorder()

        self._task_agent: dict[str, str] = {}
        self._known_agent_ids: dict[str, set[str]] = {}
        self._pending_step: dict[str, Callable[[], AgentEvent]] = {}
        self._payment_source: dict[str, str] = {}  # payment_task_id -> source_task_id

    # --- эмиссия событий (единственный путь наружу — Filter решает publish/drop) ---

    def _emit(self, event: AgentEvent) -> DeliveryResult | None:
        self._known_agent_ids.setdefault(event.task_id, set()).add(event.agent_id)
        context = self._stage_tracker.context_for(event)
        decision = self._filter.decide(context)
        if decision.action == "drop":
            return None
        # publish_digest_only не реализован в MVP (core/filter.py) — трактуем как publish.
        return self.outbound_queue.submit(event)

    def emit_agent_event(self, task_id: str, kind: str = "report", **kwargs) -> AgentEvent:
        stage = self.tracker.get_status(task_id) or "backlog"
        event = build_event(kind, "system.mock", task_id, stage, new_correlation_id(), self._seq.next(task_id), **kwargs)
        self._emit(event)
        return event

    # --- seeding ---

    def seed_task(self, task_id: str, stage: str, agent: str) -> SeededTask:
        event = build_event(
            "report", agent, task_id, stage, new_correlation_id(), self._seq.next(task_id),
            payload={"text": f"{agent}: seed at {stage}"},
        )
        self._task_agent[task_id] = agent
        self._emit(event)
        return SeededTask(task_id=task_id)

    def seed_backlog_task(self, task_id: str, agent: str) -> SeededTask:
        return self.seed_task(task_id, "backlog", agent)

    def seed_planned_task(self, task_id: str, agent: str) -> SeededTask:
        return self.seed_task(task_id, "planned", agent)

    def seed_in_progress_task(self, task_id: str, agent: str) -> SeededTask:
        return self.seed_task(task_id, "in_progress", agent)

    def seed_done_task(self, task_id: str, agent: str) -> SeededTask:
        return self.seed_task(task_id, "done", agent)

    # --- прогон агента ---

    def run_agent_to_completion(self, task_id: str) -> None:
        agent_id = self._task_agent[task_id]
        start_stage = self.tracker.get_status(task_id)
        events = mock_actor.run_to_completion(self.workflow, task_id, start_stage, self._seq, agent_id=agent_id)
        for event in events:
            self._emit(event)

    def mock_cost_estimate(self, task_id: str, cost_usd: float, blocks_stage: str, *, threshold_usd: float | None = None) -> None:
        """Готовит decision_request Cost-агента — реально публикуется на
        run_agent_step(), не здесь (см. docs/04_test_plan.md сценарий 06:
        отдельный вызов `system.run_agent_step` после этого)."""
        resolved_threshold = threshold_usd if threshold_usd is not None else APPROVAL_THRESHOLD_USD_DEFAULT
        self._pending_step[task_id] = lambda: mock_cost.estimate(
            task_id, blocks_stage, self._seq, cost_usd, threshold_usd=resolved_threshold
        )

    def run_agent_step(self, task_id: str) -> AgentEvent:
        factory = self._pending_step.pop(task_id)
        event = factory()
        self._emit(event)
        if event.kind == "decision_request" and event.requires_human:
            self._create_payment_workflow(task_id, event)
        return event

    def _create_payment_workflow(self, task_id: str, event: AgentEvent) -> str:
        request = LinkedTaskRequest(
            source_task_id=task_id,
            assignee_agent_id=event.agent_id,
            priority="high",
            comment_text=event.payload.get("text", ""),
            reasoning="оценка стоимости выше порога",
            workflow_kind="payment_approval",
        )
        payment_task_id = self.tracker.create_linked_task(request)
        self._payment_source[payment_task_id] = task_id
        return payment_task_id

    # --- inbound: комментарии человека + смена статуса ---

    def flush_inbound(self) -> None:
        for task_id, new_status, _author in self.tracker.pop_new_status_changes():
            self._handle_status_change(task_id, new_status)

        for comment in self.tracker.pop_new_human_comments():
            # "Тикаем" батчер ДО момента этого комментария — закрывает окна,
            # чья пауза/потолок истекли между предыдущим комментарием и этим.
            # Без этого шага два комментария, разделённых паузой >= WINDOW_SECONDS,
            # ошибочно склеились бы в один батч при обработке пачкой в конце —
            # в реальности flush_due тикает постоянно (webhook.py), а не только
            # в момент system.flush_inbound().
            for batch in self.batcher.flush_due(now=comment.received_at):
                self._process_batch(batch)
            self.batcher.add(
                InboundEvent(
                    clickup_task_id=comment.task_id,
                    clickup_comment_id=comment.comment_id,
                    author_user_id=comment.author,
                    kind="comment",
                    body=comment.body,
                    received_at=comment.received_at or self.clock.now(),
                ),
                now=comment.received_at,
            )

        for batch in self.batcher.flush_due(now=self.clock.now()):
            self._process_batch(batch)

    def _process_batch(self, batch: InboundBatch) -> None:
        task_state = self._task_state_for(batch.clickup_task_id)
        action = self.router.route(batch, task_state)
        self._react(action, batch)

    def _task_state_for(self, task_id: str) -> TaskState | None:
        status = self.tracker.get_status(task_id)
        if status is None:
            return None
        feed = self.tracker.get_feed(task_id)
        current_agent_id = feed[-1].agent_id if feed else self._task_agent.get(task_id)
        known = self._known_agent_ids.get(task_id, set())
        return TaskState(task_id=task_id, stage=status, current_agent_id=current_agent_id, known_agent_ids=frozenset(known))

    def _react(self, action: RouterAction, batch: InboundBatch) -> None:
        if action.kind == "deliver_to_agent":
            self._react_deliver(action)
        elif action.kind == "new_ticket":
            self._react_new_ticket(action)
        elif action.kind == "escalate":
            self.escalation_log.record(action.payload.get("task_id", batch.clickup_task_id), action.payload.get("reason", ""))
        # pipeline_command не используется этим harness'ом — не нужен ни одному из 7 сценариев

    def _react_deliver(self, action: RouterAction) -> None:
        task_id = action.payload["task_id"]
        thread_ref = action.payload.get("thread_ref")
        agent_id = action.target_agent_id
        stage = self.tracker.get_status(task_id)
        if thread_ref:
            self.tracker.post_reply(thread_ref, f"{agent_id}: принял правку.")
        event = build_event(
            "report", agent_id, task_id, stage, new_correlation_id(), self._seq.next(task_id),
            payload={"text": f"{agent_id}: принял правку."}, thread_ref=thread_ref,
        )
        self._emit(event)  # обычно drop (та же стадия, микросостояние) — реплика уже видна через post_reply

    def _react_new_ticket(self, action: RouterAction) -> None:
        source_task_id = action.payload["source_task_id"]
        request = LinkedTaskRequest(
            source_task_id=source_task_id,
            assignee_agent_id=action.target_agent_id or "",
            priority=action.payload.get("priority", "normal"),
            comment_text=action.payload.get("comment_text", ""),
            reasoning="комментарий на закрытой задаче — правка агенту прошлой итерации",
        )
        new_task_id = self.tracker.create_linked_task(request)
        action.payload["created_task_id"] = new_task_id  # тот же dict, что и в system.router.last_action.payload

    # Название approve-статуса — атрибут конкретного payment-workflow, не
    # WorkflowConfig текущего домена (approve не входит ни в CAMERA_PIPELINE,
    # ни в COSTUME_PIPELINE — это статус на СВЯЗАННОЙ задаче). Захардкожено
    # как единственная используемая литеральная строка ClickUp-статуса в
    # общем harness'е — найдено ревью, не выведено из конфига. Не критично,
    # т.к. payment-workflow как сущность сам по себе ещё не конфигурируется
    # per-домен нигде в прототипе.
    APPROVE_STATUS = "Approved"

    def _handle_status_change(self, task_id: str, new_status: str) -> None:
        if new_status != self.APPROVE_STATUS:
            return
        idempotency_key = f"approve:{task_id}"
        if self.tracker.is_processed(idempotency_key):
            return
        self.tracker.mark_processed(idempotency_key)

        source_task_id = self._payment_source.get(task_id)
        if source_task_id is None:
            return
        self.mock_actor.call_count += 1
        agent_id = self._task_agent.get(source_task_id, mock_actor.DEFAULT_AGENT_ID)
        current_stage = self.tracker.get_status(source_task_id)
        event = mock_actor.advance_to_next_stage(self.workflow, source_task_id, current_stage, self._seq, agent_id)
        self._emit(event)

    # --- outbound: очередь с подтверждением (сценарий 07) ---

    def flush_outbound(self) -> list[DeliveryResult]:
        return self.outbound_queue.flush()
