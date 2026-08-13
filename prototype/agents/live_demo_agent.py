"""Реакция на живой карточке — эмитит настоящие AgentEvent (не голые вызовы
ClickUp API в обход контракта), см. CLAUDE.md "Демо-код — не исключение".

Последовательность (одобренный план, artifact 4af0647a):
Первый ассистент подтверждает адресата -> нужный агент отвечает по существу
и меняет статус. Если классификатор решил, что комментарий не по адресу
(same_task=false) -> вместо ответа в этой же задаче создаётся новая
(create_linked_task) для нужного агента — P3/R3 ТЗ, "новый тикет для агента,
которому реально адресовано".
"""
from __future__ import annotations

import asyncio
import logging

import openai

from adapters.clickup import ClickUpTrackerAdapter
from agents._shared import build_event
from core.events import LinkedTaskRequest, RouterAction
from core.ids import EventSeqCounter, new_correlation_id
from core.llm import complete_text
from core.state_machine import COSTUME_PIPELINE
from inbound.comment_router import AGENT_ROSTER, TASK_STARTING_STAGE, AgentId

logger = logging.getLogger(__name__)

_seq = EventSeqCounter()

AGENT_LABEL: dict[AgentId, str] = {
    "costume": "🎨 Художник по костюмам",
    "producer": "💰 Продюсер",
    "first_ad": "📋 Первый ассистент",
}

# Реальный переход COSTUME_PIPELINE (is_valid_transition, docs/07_grilled.md
# находка №2) после того, как нужный агент отработал правку — не произвольная строка.
STATUS_AFTER: dict[AgentId, str] = {
    "costume": "in_generation",
    "producer": "in_generation",
    "first_ad": "ready",
}

REPLY_TEXT: dict[AgentId, str] = {
    "costume": f"{AGENT_LABEL['costume']}: Принял правку, беру в новую итерацию.",
    "producer": f"{AGENT_LABEL['producer']}: Принял, согласовываю и запускаю в работу.",
    "first_ad": f"{AGENT_LABEL['first_ad']}: Принял, актуализирую график и передаю дальше.",
}

# Искусственная пауза между репликами — не техническая необходимость, а часть
# демо-эффекта (см. artifact/план: "не выглядело как две реплики разом").
PAUSE_SECONDS = 2.5

# Тот же таймаут, что и у классификатора (inbound/comment_router.py) — модель
# берётся из core/llm.py (Qwen), не задаётся здесь отдельно.
REPLY_TIMEOUT_SECONDS = 3.5


def generate_reply(
    agent_id: AgentId, comment_text: str, task_id: str, client: openai.OpenAI
) -> str:
    """Реальный сгенерированный ответ агента на конкретный комментарий — не
    маршрутизация ("кому"), а содержательная реакция ("что говорит"). Без
    этого агент понимал бы адресата, но отвечал одной и той же строкой
    независимо от текста — тот же провал "имитации понимания", которого
    просили избежать при роутинге (см. CLAUDE.md "Демо-код — не исключение").

    Тот же паттерн надёжности, что и classify_comment: жёсткий таймаут +
    безусловный fallback на REPLY_TEXT[agent_id], никогда не роняет демо.
    """
    if not comment_text:
        return REPLY_TEXT[agent_id]

    prompt = (
        f"Ты — {AGENT_LABEL[agent_id]} в системе управления кинопроизводством. "
        f'Человек-супервайзер написал комментарий к задаче {task_id}:\n\n"{comment_text}"\n\n'
        "Ответь одной короткой репликой (1-2 предложения, по-деловому, без приветствий и "
        "лишней вежливости) — подтверди, что понял суть именно этого комментария, и что "
        "делаешь дальше в рамках своей зоны ответственности. Не придумывай факты, которых "
        "нет в комментарии (даты, суммы, имена) — если конкретики не хватает, ответь по "
        "существу без неё. Не бери сам комментарий в кавычки и не повторяй его дословно."
    )
    try:
        text = complete_text(client, prompt, timeout=REPLY_TIMEOUT_SECONDS, max_tokens=200)
        return f"{AGENT_LABEL[agent_id]}: {text}" if text else REPLY_TEXT[agent_id]
    except Exception as e:  # noqa: BLE001 — намеренно широкий catch, тот же принцип, что у classify_comment
        logger.warning("generate_reply fallback (%s): %s", type(e).__name__, e)
        return REPLY_TEXT[agent_id]


async def react(action: RouterAction, tracker: ClickUpTrackerAdapter, client: openai.OpenAI) -> None:
    if action.kind == "deliver_to_agent":
        await _react_same_task(action, tracker, client)
    elif action.kind == "new_ticket":
        await _react_new_ticket(action, tracker)
    else:
        logger.info("live_demo_agent: нет реакции для action.kind=%s", action.kind)


async def _react_same_task(
    action: RouterAction, tracker: ClickUpTrackerAdapter, client: openai.OpenAI
) -> None:
    task_id = action.payload["task_id"]
    thread_ref = action.payload.get("thread_ref")
    comment_text = action.payload.get("comment_text") or ""
    target = action.target_agent_id
    assert target is not None, "deliver_to_agent без target_agent_id — баг в router.py"

    current_stage = _current_stage(tracker, task_id)

    dispatch_text = f"{AGENT_LABEL['first_ad']}: Вижу правку на {task_id} — это {AGENT_LABEL[target]}."
    tracker.publish(build_event("report", "first_ad", task_id, current_stage, new_correlation_id(), _seq.next(task_id),
                                 payload={"text": dispatch_text}, thread_ref=thread_ref))

    await asyncio.sleep(PAUSE_SECONDS)

    new_stage = STATUS_AFTER[target]
    if not COSTUME_PIPELINE.is_valid_transition(current_stage, new_stage):
        logger.warning(
            "недопустимый переход %s -> %s для %s (см. COSTUME_PIPELINE.transitions), "
            "оставляю стадию без изменений", current_stage, new_stage, task_id,
        )
        new_stage = current_stage

    reply_text = generate_reply(target, comment_text, task_id, client)
    tracker.publish(build_event("report", target, task_id, new_stage, new_correlation_id(), _seq.next(task_id),
                                 payload={"text": reply_text}, thread_ref=thread_ref))


async def _react_new_ticket(action: RouterAction, tracker: ClickUpTrackerAdapter) -> None:
    source_task_id = action.payload["source_task_id"]
    thread_ref = action.payload.get("thread_ref")
    target = action.target_agent_id
    assert target is not None, "new_ticket без target_agent_id — баг в router.py"

    current_stage = _current_stage(tracker, source_task_id)
    dispatch_text = (
        f"{AGENT_LABEL['first_ad']}: Это не ко мне на этой задаче — "
        f"создаю отдельную задачу для {AGENT_LABEL[target]}."
    )
    # thread_ref — тот же треד, что видел исходный комментарий (было хардкожено
    # None, из-за чего эта ветка отвечала новым топ-level комментарием, не в
    # тред, в отличие от _react_same_task).
    tracker.publish(build_event("report", "first_ad", source_task_id, current_stage,
                                 new_correlation_id(), _seq.next(source_task_id),
                                 payload={"text": dispatch_text}, thread_ref=thread_ref))

    tracker.create_linked_task(
        LinkedTaskRequest(
            source_task_id=source_task_id,
            assignee_agent_id=target,
            # ASSUMPTION(open_questions:В8): "high" захардкожен, не выведен из
            # реального сигнала (docs/00_overview.md §4.3 оставляет приоритет
            # как "эвристика?" — не решено). Тот же выбор, что в
            # inbound/router.py::RuleBasedRouter для сценария 03.
            priority="high",
            comment_text=action.payload.get("comment_text") or "",
            reasoning=action.payload.get("reasoning") or "",
        )
    )


def _current_stage(tracker: ClickUpTrackerAdapter, task_id: str) -> str:
    """Реальный текущий статус из ClickUp, не статичный TASK_STARTING_STAGE —
    задача могла уже сдвинуться с прошлой реакции (найдено ревью: раньше
    всегда бралась стартовая стадия сида, что могло откатить статус назад при
    втором комментарии на ту же задачу). Фоллбек на TASK_STARTING_STAGE только
    если ClickUp недоступен — демо не должно падать из-за сетевого сбоя."""
    try:
        stage = tracker.get_status(task_id)
    except Exception as e:  # noqa: BLE001 — тот же принцип надёжности, что у classify_comment
        logger.warning("не удалось прочитать текущий статус %s из ClickUp (%s) — использую стартовый", task_id, e)
        stage = None
    return stage or TASK_STARTING_STAGE.get(task_id, "in_generation")


assert set(AGENT_ROSTER) == set(AGENT_LABEL) == set(REPLY_TEXT) == set(STATUS_AFTER), (
    "ростер агентов разъехался между router.py и live_demo_agent.py"
)
