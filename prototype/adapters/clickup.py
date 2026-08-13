"""Реальный TrackerAdapter поверх ClickUp REST API.

Единственное место в репозитории, которое знает про конкретные endpoint'ы
ClickUp — см. docs/03_clickup_requirements.md. Реализует TrackerAdapter
(adapters/base.py) для реального использования Фазой демо-слоя, плюс
несколько demo-утилит (create_task/delete_task/list_tasks/get_authorized_*),
которые НЕ часть протокола — нужны только seed.py/discover_ids.py.
"""
from __future__ import annotations

from typing import Any

import httpx

from adapters.base import DeliveryResult
from core.events import AgentEvent, LinkedTaskRequest, Stage
from core.state_machine import WorkflowConfig

API_BASE = "https://api.clickup.com/api/v2"
DEMO_PREFIX = "[DEMO] "


class ClickUpTrackerAdapter:
    """Реализует TrackerAdapter (structural typing — Protocol, не наследование).

    Принимает WorkflowConfig извне (не хардкодит COSTUME_PIPELINE) — статусы
    ClickUp называются человекочитаемо ("In Generation"), а не внутренним id
    ("in_generation"); WorkflowConfig.stages[id].name даёт нужную строку для
    ЛЮБОГО домена, не только костюмного цеха.
    """

    def __init__(
        self,
        token: str,
        workflow: WorkflowConfig,
        label_to_real_id: dict[str, str] | None = None,
        timeout: float = 10.0,
    ) -> None:
        self._client = httpx.Client(
            base_url=API_BASE,
            headers={"Authorization": token},
            timeout=timeout,
        )
        self._workflow = workflow
        # router.py/agents/live_demo_agent.py оперируют человекочитаемыми метками
        # ("SC-042/07"), а ClickUp API — настоящим numeric id, который появляется
        # только после создания задачи (demo/seed.py пишет его в task_map.json).
        # Перевод меток в реальные id — забота ТОЛЬКО этого адаптера, чтобы
        # router/live_demo_agent вообще не знали про существование реальных id.
        self._label_to_real_id = label_to_real_id or {}
        self._processed_correlation_ids: set[str] = set()

    def _resolve(self, task_id: str) -> str:
        return self._label_to_real_id.get(task_id, task_id)

    def close(self) -> None:
        self._client.close()

    # --- TrackerAdapter protocol ---

    def publish(self, event: AgentEvent) -> DeliveryResult:
        try:
            if event.thread_ref:
                self.post_reply(event.thread_ref, self._render(event))
            else:
                self.post_comment(event.task_id, self._render(event))
            self.set_status(event.task_id, event.stage)
            return DeliveryResult(ok=True)
        except httpx.HTTPStatusError as e:
            return DeliveryResult(ok=False, detail=f"{e.response.status_code}: {e.response.text[:200]}")
        except httpx.HTTPError as e:
            # Найдено ревью: недоступность ClickUp (сценарий 07) — таймаут,
            # обрыв соединения, DNS-сбой — не HTTPStatusError (нет ответа
            # вообще), но публикация всё равно должна вернуть DeliveryResult(ok=False)
            # для core/outbound_queue.py, а не пробрасывать исключение наружу.
            return DeliveryResult(ok=False, detail=f"{type(e).__name__}: {e}")

    def create_linked_task(self, request: LinkedTaskRequest) -> str:
        real_source_id = self._resolve(request.source_task_id)
        list_id = self._list_id_of(real_source_id)
        # Маркер в описании — единственный способ найти связанную задачу обратно
        # (get_linked_workflow_task) без заведения custom field под workflow_kind,
        # см. ASSUMPTION ниже.
        marker = f"[{request.workflow_kind}:{request.source_task_id}]"
        body = {
            "name": f"{DEMO_PREFIX}Follow-up: {request.source_task_id}",
            "description": (
                f"{marker}\n"
                f"Автоматически создано из комментария на {request.source_task_id} — "
                f"адресован другому агенту ({request.assignee_agent_id}), не хозяину исходной задачи.\n\n"
                f'Исходный комментарий:\n"{request.comment_text}"\n\n'
                f"Почему передано сюда: {request.reasoning}\n"
                f"Приоритет: {request.priority}. Ссылка на исходную задачу: {request.source_task_id}."
            ),
        }
        r = self._client.post(f"/list/{list_id}/task", json=body)
        r.raise_for_status()
        return r.json()["id"]

    def get_status(self, task_id: str) -> str | None:
        """Реальный текущий статус задачи, переведённый обратно во внутренний
        id стадии через WorkflowConfig (обратная операция к set_status). Нужен
        agents/live_demo_agent.py, чтобы не полагаться на статичную стартовую
        стадию сида — задача могла уже сдвинуться с прошлой реакции."""
        real_id = self._resolve(task_id)
        r = self._client.get(f"/task/{real_id}")
        r.raise_for_status()
        display_name = r.json()["status"]["status"]
        for stage_id, definition in self._workflow.stages.items():
            if definition.name.lower() == display_name.lower():
                return stage_id
        return None

    def get_linked_workflow_task(self, task_id: str, workflow_kind: str) -> str | None:
        # ASSUMPTION(open_questions:В12): без custom field
        # под workflow_kind единственный способ отличить связанные задачи —
        # текстовый маркер в описании (см. create_linked_task). Не покрыто
        # тестами (сетевой вызов, как и весь этот адаптер) — сверить на первом
        # реальном прогоне Фазы 6.
        real_id = self._resolve(task_id)
        list_id = self._list_id_of(real_id)
        marker = f"[{workflow_kind}:{task_id}]"
        for task in self.list_tasks(list_id):
            if marker in (task.get("text_content") or ""):
                return str(task["id"])
        return None

    def post_comment(self, task_id: str, body: str) -> str:
        r = self._client.post(f"/task/{self._resolve(task_id)}/comment", json={"comment_text": body})
        r.raise_for_status()
        return str(r.json()["id"])

    def post_reply(self, parent_comment_id: str, body: str) -> str:
        r = self._client.post(f"/comment/{parent_comment_id}/reply", json={"comment_text": body})
        r.raise_for_status()
        return str(r.json()["id"])

    def set_status(self, task_id: str, status: Stage) -> None:
        display_name = self._workflow.stages[status].name
        r = self._client.put(f"/task/{self._resolve(task_id)}", json={"status": display_name})
        r.raise_for_status()

    def set_custom_field(self, task_id: str, field: str, value: Any) -> None:
        # Не используется в демо-срезе (custom fields не заведены) — реализовано
        # для полноты протокола, будет нужно в Фазе 6 (docs/02_prototype_plan.md).
        r = self._client.post(f"/task/{self._resolve(task_id)}/field/{field}", json={"value": value})
        r.raise_for_status()

    def is_processed(self, correlation_id: str) -> bool:
        return correlation_id in self._processed_correlation_ids

    def mark_processed(self, correlation_id: str) -> None:
        self._processed_correlation_ids.add(correlation_id)

    # --- Demo-утилиты (не часть TrackerAdapter-протокола) ---

    def create_task(self, list_id: str, name: str, status: str | None = None) -> str:
        """`status`, если задан — внутренний id стадии (например "regenerate"),
        переводится в отображаемое имя ClickUp тем же WorkflowConfig, что и set_status."""
        body: dict[str, Any] = {"name": f"{DEMO_PREFIX}{name}" if not name.startswith(DEMO_PREFIX) else name}
        if status:
            body["status"] = self._workflow.stages[status].name
        r = self._client.post(f"/list/{list_id}/task", json=body)
        r.raise_for_status()
        return str(r.json()["id"])

    def delete_task(self, task_id: str) -> None:
        r = self._client.delete(f"/task/{task_id}")
        if r.status_code not in (200, 204, 404):
            r.raise_for_status()

    def list_tasks(self, list_id: str) -> list[dict[str, Any]]:
        r = self._client.get(f"/list/{list_id}/task", params={"include_closed": "true"})
        r.raise_for_status()
        return r.json().get("tasks", [])

    def list_demo_tasks(self, list_id: str) -> list[dict[str, Any]]:
        return [t for t in self.list_tasks(list_id) if t.get("name", "").startswith(DEMO_PREFIX)]

    def get_authorized_user(self) -> dict[str, Any]:
        r = self._client.get("/user")
        r.raise_for_status()
        return r.json()["user"]

    def get_authorized_teams(self) -> list[dict[str, Any]]:
        r = self._client.get("/team")
        r.raise_for_status()
        return r.json().get("teams", [])

    def register_webhook(self, team_id: str, endpoint: str, events: list[str]) -> dict[str, Any]:
        r = self._client.post(
            f"/team/{team_id}/webhook",
            json={"endpoint": endpoint, "events": events},
        )
        r.raise_for_status()
        return r.json()["webhook"]

    # --- helpers ---

    def _list_id_of(self, task_id: str) -> str:
        r = self._client.get(f"/task/{task_id}")
        r.raise_for_status()
        return str(r.json()["list"]["id"])

    def _render(self, event: AgentEvent) -> str:
        # payload не типизирован (P2, docs/00_overview.md §4.1) — для демо
        # берём готовый текст реплики из payload, если агент его туда положил.
        text = event.payload.get("text")
        return text if text else f"[{event.agent_id}] {event.kind}: {event.stage}"
