"""Контракты AgentEvent / InboundEvent / FilterDecision.

См. docs/01_architecture_plan.md §2 и docs/07_grilled.md (находка №1 —
FilterContext.stage_changed вычисляется ядром, не читается из payload агента).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel

Stage = str
"""Легальные значения и поведение стадии задаёт core.state_machine.WorkflowConfig,
не сам тип — см. docs/08_dataset_analysis.md находка №1 (разные домены = разные
наборы статусов ClickUp, единого фиксированного enum нет)."""

EventKind = Literal["report", "decision_request", "flag"]


class AgentEvent(BaseModel):
    """Единый конверт для разнородного аутпута агентов (P2/R2, ТЗ)."""

    kind: EventKind
    agent_id: str
    task_id: str
    stage: Stage
    correlation_id: str
    event_seq: int
    payload: dict[str, Any] = {}
    cost_usd: float | None = None
    requires_human: bool = False
    deadline: datetime | None = None
    prev_agent_id: str | None = None
    thread_ref: str | None = None
    version: str | None = None


class FilterContext(BaseModel):
    """То, что реально видит Filter — обогащённое ядром событие.

    stage_changed вычисляется сравнением event.stage с последней известной
    стадией task_id ДО того, как событие попадает в Filter — см.
    docs/01_architecture_plan.md §8 и docs/07_grilled.md находка №1.
    """

    event: AgentEvent
    stage_changed: bool


class FilterDecision(BaseModel):
    action: Literal["publish", "publish_digest_only", "drop"]
    reason: str


InboundKind = Literal["comment", "status_change", "field_edit", "mention"]


class InboundEvent(BaseModel):
    """Сырое событие из ClickUp webhook, ДО батчинга и роутинга."""

    source: Literal["clickup"] = "clickup"
    clickup_task_id: str
    clickup_comment_id: str | None = None
    author_user_id: str
    author_email: str | None = None
    kind: InboundKind
    body: str | None = None
    new_status: str | None = None
    field_name: str | None = None
    field_value: Any = None
    received_at: datetime


class InboundBatch(BaseModel):
    """Единица работы для Router — см. docs/00_overview.md §5.2."""

    batch_id: str
    clickup_task_id: str
    events: list[InboundEvent]
    window_opened_at: datetime
    window_closed_at: datetime


RouterActionKind = Literal["deliver_to_agent", "new_ticket", "pipeline_command", "escalate"]


class RouterAction(BaseModel):
    kind: RouterActionKind
    target_agent_id: str | None = None
    payload: dict[str, Any] = {}


class LinkedTaskRequest(BaseModel):
    """Контракт на создание новой задачи для другого агента (P3/R3 ТЗ — "новый
    тикет для агента, которому реально адресовано"). Явная модель, не россыпь
    позиционных параметров — исходный текст комментария и reasoning классификатора
    обязательны, чтобы принимающий агент понимал задачу по существу, а не только
    факт "кто-то что-то написал где-то ещё"."""

    source_task_id: str
    assignee_agent_id: str
    priority: Literal["low", "normal", "high", "urgent"] = "normal"
    comment_text: str
    reasoning: str
    # payment_approval — сценарий 06 (порог стоимости); follow_up — исходный P3/R3
    # случай (правка агенту прошлой итерации). Дефолт сохраняет обратную
    # совместимость с местами, которые ещё не различают типы связанных задач.
    workflow_kind: Literal["follow_up", "payment_approval"] = "follow_up"


class TaskState(BaseModel):
    """Контекст задачи, которым Router принимает решение — см. docs/00_overview.md
    §4.3 и docs/01_architecture_plan.md §7. Собирается вызывающей стороной
    (sim/system.py, webhook.py), не самим Router'ом: inbound/ не имеет права
    обращаться к adapters/ напрямую (см. CLAUDE.md "core/ не импортирует из adapters/",
    тот же принцип распространяется на inbound/router.py)."""

    task_id: str
    stage: Stage
    current_agent_id: str | None = None
    # Кому "@упоминание" вообще может быть адресовано на этой задаче — обычно
    # множество agent_id, уже оставивших событие в её ленте. Нет источника,
    # определяющего это структурно иначе.
    # ASSUMPTION(open_questions:В9): конвенция не описана ни в
    # ТЗ, ни в архитектуре — придумана здесь, чтобы "упоминание несуществующего
    # agent_id" (docs/00_overview.md §4.3, ветка 4) было проверяемо.
    known_agent_ids: frozenset[str] = frozenset()


class DeliveryResult(BaseModel):
    """Результат попытки доставки в TrackerAdapter.publish — раньше жил в
    adapters/base.py, переехал сюда, т.к. core/outbound_queue.py должен уметь
    типизировать его, а core/ не имеет права импортировать из adapters/."""

    ok: bool
    detail: str = ""
