"""TrackerAdapter — интерфейс, за которым прячется конкретный трекер.

См. docs/01_architecture_plan.md §6 и docs/03_clickup_requirements.md §6.2
(post_comment / post_reply — разные endpoint'ы у ClickUp, не один параметризованный метод).
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from core.events import AgentEvent, DeliveryResult, LinkedTaskRequest, Stage

# DeliveryResult теперь определён в core/events.py (core/outbound_queue.py должен
# уметь его типизировать без импорта из adapters/) — реэкспортирован отсюда, чтобы
# существующие `from adapters.base import DeliveryResult` не потребовали правки.
__all__ = ["DeliveryResult", "TrackerAdapter"]


@runtime_checkable
class TrackerAdapter(Protocol):
    """Structural typing — реализации (adapters/clickup.py, adapters/memory.py)
    НЕ наследуются отсюда, см. CLAUDE.md. `@runtime_checkable` позволяет
    `isinstance(adapter, TrackerAdapter)` — проверяет наличие методов (не
    сигнатур: аргументы/типы isinstance не сверяет), используется в
    tests/test_tracker_adapter_contract.py как быстрый сигнал о забытом
    методе. Сигнатуры и поведение проверяет сам contract-test-suite там же."""

    def publish(self, event: AgentEvent) -> DeliveryResult: ...

    def create_linked_task(self, request: LinkedTaskRequest) -> str: ...

    def get_linked_workflow_task(self, task_id: str, workflow_kind: str) -> str | None: ...

    def get_status(self, task_id: str) -> Stage | None: ...

    def post_comment(self, task_id: str, body: str) -> str: ...

    def post_reply(self, parent_comment_id: str, body: str) -> str: ...

    def set_status(self, task_id: str, status: Stage) -> None: ...

    def set_custom_field(self, task_id: str, field: str, value: Any) -> None: ...

    def is_processed(self, correlation_id: str) -> bool: ...

    def mark_processed(self, correlation_id: str) -> None: ...
