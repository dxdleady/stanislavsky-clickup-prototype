"""CommentRouter — LLM-классификация комментария под живой демо-срез (3 демо-
агента, реальный ClickUp). Раньше жил в inbound/router.py под именем
RuleBasedRouter — имя было неточным (никаких правил, чистый LLM-путь), это
и есть техдолг из docs/11_live_demo_architecture.md §6, закрытый этим файлом:
`inbound/router.py` теперь — настоящий детерминированный RuleBasedRouter
(docs/00_overview.md §4.3), этот файл — узкий live-demo-специфичный путь.

LLM-провайдер — Qwen через core/llm.py (раньше был Anthropic Claude Haiku 4.5).
"""
from __future__ import annotations

import logging
from typing import Literal

import openai
from pydantic import BaseModel

from core.events import InboundBatch, RouterAction
from core.llm import complete_structured

logger = logging.getLogger(__name__)

AgentId = Literal["costume", "producer", "first_ad"]

# Реальные роли из docs/source/stanislavsky_costume_demo_dataset_v2.json — не выдумано.
AGENT_ROSTER: dict[AgentId, str] = {
    "costume": (
        "Художник по костюмам — костюмный плот, перемены, дубли, состояния износа, "
        "wardrobe_ref, время на перемену"
    ),
    "producer": "Продюсер — бриф, смета цехов, порог стоимости, утверждение КПП, приёмка",
    "first_ad": (
        "Первый ассистент — КПП, вызывной лист, порядок съёмки, очередь, дедлайны, переназначение"
    ),
}

# task_id -> хозяин по умолчанию. Ровно то место, куда добавляется 5-я/6-я
# задача с новым агентом — не нужно трогать ни webhook.py, ни этот файл.
TASK_OWNERS: dict[str, AgentId] = {
    "SC-042/07": "costume",
    "SC-042/08": "costume",
    "SC-042/09": "producer",
    "SC-041/02": "first_ad",
}

# Стартовая стадия каждой демо-задачи (internal id из COSTUME_PIPELINE,
# core/state_machine.py) — та же таблица, что задаёт seed.py при создании
# задач в ClickUp, чтобы не держать состояние отдельно от доски (источник
# истины — ClickUp, docs/00_overview.md §3). Один источник, не дублировать
# в agents/live_demo_agent.py и demo/seed.py по отдельности.
TASK_STARTING_STAGE: dict[str, str] = {
    "SC-042/07": "accepted",
    "SC-042/08": "regenerate",
    "SC-042/09": "cost_approval",
    "SC-041/02": "blocked",
}

CLASSIFY_TIMEOUT_SECONDS = 3.5


class CommentClassification(BaseModel):
    agent_id: AgentId
    same_task: bool
    reasoning: str


def classify_comment(
    comment_text: str,
    task_id: str,
    default_owner: AgentId,
    client: openai.OpenAI,
) -> CommentClassification:
    """Реальная LLM-классификация с безусловным fallback.

    Никогда не поднимает исключение — вызывается из webhook-хендлера, который
    обязан ответить ClickUp быстро (docs/03_clickup_requirements.md §2.3);
    сбой сети/таймаут/невалидный JSON не должны ронять ответ на вебхук.
    """
    roster_text = "\n".join(f"- {aid}: {desc}" for aid, desc in AGENT_ROSTER.items())
    prompt = (
        f'Комментарий человека к задаче {task_id} (сейчас закреплена за агентом "{default_owner}"):\n\n'
        f'"{comment_text}"\n\n'
        f"Роли агентов:\n{roster_text}\n\n"
        f"Кому на самом деле адресован комментарий? Если он по существу относится к "
        f'текущему хозяину задачи — agent_id="{default_owner}", same_task=true. '
        f"Если он явно про зону ответственности ДРУГОГО агента из списка выше — "
        f"agent_id этого другого агента, same_task=false."
    )
    try:
        return complete_structured(
            client, prompt, CommentClassification, timeout=CLASSIFY_TIMEOUT_SECONDS
        )
    except Exception as e:  # noqa: BLE001 — намеренно широкий catch, см. docstring
        logger.warning("classify_comment fallback (%s): %s", type(e).__name__, e)
        return CommentClassification(
            agent_id=default_owner, same_task=True, reasoning=f"fallback после сбоя классификатора: {e}"
        )


class CommentRouter:
    """Живой демо-срез: один путь (LLM-классификация), не набор правил — см.
    inbound/router.py для настоящего детерминированного RuleBasedRouter."""

    def __init__(self, client: openai.OpenAI) -> None:
        self._client = client

    def route(self, batch: InboundBatch) -> RouterAction:
        if not batch.events:
            return RouterAction(kind="escalate", payload={"reason": "empty batch"})

        # inbound/webhook.py подключает настоящий Batcher (60с окно) к этому пути —
        # батч может содержать больше одного события; раньше здесь бралось только
        # последнее ("без батчинга в демо"), из-за чего более ранние комментарии в
        # том же окне молча не классифицировались и не получали ответа. Тот же
        # принцип объединения, что и в inbound/router.py::RuleBasedRouter.
        combined_body = "\n".join(e.body for e in batch.events if e.body)
        last_comment_id = next(
            (e.clickup_comment_id for e in reversed(batch.events) if e.clickup_comment_id), None
        )
        task_id = batch.clickup_task_id
        default_owner = TASK_OWNERS.get(task_id)

        if default_owner is None or not combined_body:
            return RouterAction(
                kind="escalate",
                payload={"reason": "unknown task or empty comment", "task_id": task_id},
            )

        classification = classify_comment(combined_body, task_id, default_owner, self._client)

        if classification.same_task:
            return RouterAction(
                kind="deliver_to_agent",
                target_agent_id=classification.agent_id,
                payload={
                    "task_id": task_id,
                    "thread_ref": last_comment_id,
                    "reasoning": classification.reasoning,
                    "comment_text": combined_body,
                },
            )
        return RouterAction(
            kind="new_ticket",
            target_agent_id=classification.agent_id,
            payload={
                "source_task_id": task_id,
                "thread_ref": last_comment_id,
                "reasoning": classification.reasoning,
                "comment_text": combined_body,
            },
        )
