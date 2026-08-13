"""RuleBasedRouter — настоящий детерминированный роутер (docs/00_overview.md
§4.3), без LLM и без сети. Для живого демо-среза (3 агента, реальный ClickUp,
LLM-классификация) — см. inbound/comment_router.py::CommentRouter, отдельный
файл (было — техдолг: этот класс раньше назывался так же, но был LLM-путём,
см. docs/11_live_demo_architecture.md §6, закрыт этим переименованием/сплитом).

Алгоритм (docs/00_overview.md §4.3, буквально):
1. Пустой батч → escalate.
2. Нет task_state (задача не резолвится) → escalate.
3. Активная для роутинга стадия (WorkflowConfig.is_active_for_routing):
   - есть @-упоминание конкретного agent_id → адресовано ему, если он входит
     в known_agent_ids задачи; иначе (упоминание несуществующего) → escalate.
   - иначе → адресовано текущему ведущему агенту (task_state.current_agent_id);
     если и его нет → escalate.
   → deliver_to_agent.
4. Стадия в closed_stages (задача закрыта и принята) → new_ticket, ссылка на
   исходную задачу, prev_agent_id = task_state.current_agent_id.
5. Ни то ни другое (не должно случаться при валидном WorkflowConfig, но не
   гарантировано типом) → escalate, как страховка.
"""
from __future__ import annotations

import re

from core.events import InboundBatch, RouterAction, TaskState
from core.state_machine import WorkflowConfig

_MENTION_RE = re.compile(r"@([\w.]+)")


class RuleBasedRouter:
    def __init__(self, workflow: WorkflowConfig) -> None:
        self._workflow = workflow

    def route(self, batch: InboundBatch, task_state: TaskState | None) -> RouterAction:
        if not batch.events:
            return RouterAction(kind="escalate", payload={"reason": "empty batch"})

        if task_state is None:
            return RouterAction(
                kind="escalate",
                payload={"reason": "unknown task", "task_id": batch.clickup_task_id},
            )

        # Сценарий 04 (пачка комментариев): весь батч — одно согласованное
        # действие, текст объединяется, а не берётся только последнее событие.
        combined_body = "\n".join(e.body for e in batch.events if e.body)
        last_comment_id = next(
            (e.clickup_comment_id for e in reversed(batch.events) if e.clickup_comment_id), None
        )
        mention = _MENTION_RE.search(combined_body)
        mentioned_agent_id = mention.group(1) if mention else None

        if self._workflow.is_active_for_routing(task_state.stage):
            if mentioned_agent_id is not None:
                if mentioned_agent_id not in task_state.known_agent_ids:
                    return RouterAction(
                        kind="escalate",
                        payload={
                            "reason": "mention of unknown agent_id",
                            "task_id": task_state.task_id,
                            "mentioned_agent_id": mentioned_agent_id,
                        },
                    )
                target = mentioned_agent_id
            else:
                target = task_state.current_agent_id

            if target is None:
                return RouterAction(
                    kind="escalate",
                    payload={"reason": "no current agent and no mention", "task_id": task_state.task_id},
                )

            return RouterAction(
                kind="deliver_to_agent",
                target_agent_id=target,
                payload={
                    "task_id": task_state.task_id,
                    "thread_ref": last_comment_id,
                    "comment_text": combined_body,
                },
            )

        if task_state.stage in self._workflow.closed_stages:
            # ASSUMPTION(open_questions:В8): docs/00_overview.md
            # §4.3 буквально оставляет приоритет как "эвристика?" — не решено.
            # Захардкожен "high" (тот же выбор, что уже сделан в
            # agents/live_demo_agent.py::_react_new_ticket для live-demo-среза),
            # не выведен из "как давно закрыта задача"/тона реплики.
            return RouterAction(
                kind="new_ticket",
                target_agent_id=task_state.current_agent_id,
                payload={
                    "source_task_id": task_state.task_id,
                    "comment_text": combined_body,
                    "priority": "high",
                    "prev_agent_id": task_state.current_agent_id,
                },
            )

        # Стадия ни активна для роутинга, ни закрыта — WorkflowConfig такого не
        # запрещает структурно, страховка на случай нестандартной конфигурации.
        return RouterAction(
            kind="escalate",
            payload={"reason": "stage neither active nor closed", "task_id": task_state.task_id},
        )
