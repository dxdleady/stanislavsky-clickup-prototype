"""FastAPI-вебхук живого демо-слоя. Тонкий хендлер — отвечает ClickUp за
доли секунды (<7с жёсткое требование ClickUp, иначе деградация/suspend
вебхука, docs/03_clickup_requirements.md §2.3), вся обработка (роутер,
LLM-классификация, реакция агента) уходит в фоновую asyncio-задачу.

⚠️ Точная форма payload ClickUp не подтверждена документацией на 100%
(docs/03_clickup_requirements.md §2.2). Сырое тело логируется безусловно на
INFO — при первом реальном комментарии сверить лог с извлечёнными полями
ниже и поправить _extract_inbound_event, если что-то не совпало. Это не
забытый TODO, а сознательная стратегия из плана (docs/11_live_demo_architecture.md).

Запуск: uvicorn inbound.webhook:app --port 8000
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request

from adapters.clickup import ClickUpTrackerAdapter
from agents.live_demo_agent import AGENT_LABEL, react
from core.clock import RealClock
from core.config import settings
from core.events import InboundEvent
from core.llm import build_client
from core.state_machine import COSTUME_PIPELINE
from inbound.batcher import Batcher
from inbound.comment_router import CommentRouter

# Тик фонового цикла, закрывающего окна батчера (см. _flush_loop) — не из ТЗ,
# выбран так, чтобы окно (60с) и потолок (300с) закрывались с разумной
# точностью, не нагружая событием каждую секунду.
FLUSH_TICK_SECONDS = 5

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("webhook")


@asynccontextmanager
async def _lifespan(app: FastAPI):
    asyncio.create_task(_flush_loop())  # noqa: F821 — _flush_loop определён ниже, разрешается во время вызова
    yield


app = FastAPI(lifespan=_lifespan)

TASK_MAP_PATH = Path(__file__).parent.parent / "demo" / "task_map.json"


def _load_task_map() -> dict[str, str]:
    if not TASK_MAP_PATH.exists():
        logger.warning("demo/task_map.json не найден — запустите `python -m demo.seed` сначала")
        return {}
    return json.loads(TASK_MAP_PATH.read_text())


_label_to_real_id = _load_task_map()
_real_id_to_label = {real_id: label for label, real_id in _label_to_real_id.items()}

_clock = RealClock()
_tracker = ClickUpTrackerAdapter(
    token=settings.require("clickup_token"),
    workflow=COSTUME_PIPELINE,
    label_to_real_id=_label_to_real_id,
)
_batcher = Batcher(clock=_clock)
# Один клиент на классификацию (comment_router.py) и на генерацию ответа агента
# (live_demo_agent.py) — не заводить второй клиент без необходимости.
_llm_client = build_client()
_router = CommentRouter(client=_llm_client)


@app.get("/healthz")
async def healthz() -> dict:
    return {"ok": True, "known_tasks": list(_label_to_real_id)}


@app.post("/webhook/clickup")
async def clickup_webhook(request: Request) -> dict:
    raw = await request.body()
    logger.info("raw webhook body: %s", raw[:2000].decode("utf-8", errors="replace"))

    if settings.require_webhook_signature:
        if not _verify_signature(raw, request.headers.get("x-signature", "")):
            # 401 роняет вебхук в suspended мгновенно (docs/03_clickup_requirements.md
            # §2.3) — поэтому на невалидную подпись отвечаем 200 и просто игнорируем,
            # а не 401.
            logger.warning("невалидная подпись вебхука — игнорирую событие")
            return {"ok": True}

    try:
        payload = json.loads(raw)
        event = _extract_inbound_event(payload)
    except Exception as e:  # noqa: BLE001 — намеренно широкий catch, см. docstring модуля
        logger.warning("не смог разобрать webhook payload (%s): %s", type(e).__name__, e)
        return {"ok": True}

    if event is None:
        return {"ok": True}

    asyncio.create_task(_handle(event))
    return {"ok": True}


async def _handle(event: InboundEvent) -> None:
    """Только кладёт событие в окно батчера — реальный sliding window (60с)
    означает, что маршрутизация и реакция агента больше не происходят
    синхронно с приходом комментария, см. _flush_loop."""
    try:
        _batcher.add(event)
    except Exception:  # noqa: BLE001 — фоновая задача, некому вернуть ошибку
        logger.exception("сбой при постановке события в батчер для задачи %s", event.clickup_task_id)


async def _flush_loop() -> None:
    """Единственное место в кодовой базе, которому разрешено держать реальный
    цикл с ожиданием (webhook.py — impure IO-граница, не core/inbound в
    архитектурном смысле CLAUDE.md — тот же RealClock, что и у Batcher).
    Закрывает готовые окна и реагирует на каждый батч.

    Известное ограничение (найдено ревью, не исправлено — риск регресса
    выше пользы при текущем объёме тестов): `_router.route`/`react` внутри
    используют синхронные клиенты (openai.OpenAI, httpx.Client), не
    asyncio.to_thread — блокируют event loop на время LLM+HTTP вызовов
    (секунды). `POST /webhook/clickup` сам по себе всё ещё отвечает мгновенно
    (кладёт событие в батчер и выходит) — под риском конкретно "здоровье
    /healthz и параллельная обработка второго батча, пока этот цикл занят",
    не под риском нарушения 7-секундного таймаута ClickUp на сам webhook-ответ.
    """
    while True:
        await asyncio.sleep(FLUSH_TICK_SECONDS)
        for batch in _batcher.flush_due():
            try:
                action = _router.route(batch)
                await react(action, _tracker, _llm_client)
            except Exception:  # noqa: BLE001 — фоновый цикл, некому вернуть ошибку
                logger.exception("сбой при обработке батча для задачи %s", batch.clickup_task_id)


def _extract_inbound_event(payload: dict) -> InboundEvent | None:
    if payload.get("event") != "taskCommentPosted":
        return None

    real_task_id = payload.get("task_id")
    if not real_task_id:
        history_items = payload.get("history_items") or [{}]
        real_task_id = history_items[0].get("parent_id")
    if not real_task_id or real_task_id not in _real_id_to_label:
        logger.info("комментарий не на известной демо-задаче (task_id=%s) — игнорирую", real_task_id)
        return None
    label = _real_id_to_label[real_task_id]

    history_items = payload.get("history_items") or [{}]
    item = history_items[0]

    author_id = str((item.get("user") or {}).get("id", ""))
    if settings.bot_user_id and author_id == settings.bot_user_id:
        logger.info("эхо от собственного аккаунта бота — игнорирую (защита от эха)")
        return None

    comment_text = _extract_comment_text(item)
    if not comment_text:
        logger.warning("не нашёл текст комментария в history_items[0]: %s", item)
        return None

    # ASSUMPTION(open_questions:В13): вторичный сигнал эха по текстовому префиксу
    # агента — страхует именно тот случай, когда BOT_USER_ID временно пуст
    # (обходной путь для звонка, см. docs/11_live_demo_architecture.md §3, из-за
    # общего с супервайзером ClickUp-аккаунта, docs/06_open_questions.md В13):
    # проверка по author_id выше в этом режиме выключена, и без этого сигнала
    # бот получил бы вебхук на собственный ответ и мог зациклиться.
    if any(comment_text.startswith(label) for label in AGENT_LABEL.values()):
        logger.info("текст начинается с префикса агента — вероятное эхо собственного ответа")
        return None

    comment_id = _extract_comment_id(item)

    return InboundEvent(
        clickup_task_id=label,
        clickup_comment_id=comment_id,
        author_user_id=author_id,
        kind="comment",
        body=comment_text,
        received_at=_clock.now(),
    )


def _extract_comment_id(history_item: dict) -> str | None:
    """Подтверждено живым вебхуком (2026-08-13, см. docs/06_open_questions.md
    В7): реальный id комментария (нужен post_reply для треда) — внутри
    вложенного history_item["comment"]["id"], НЕ history_item["id"] (это id
    самого history item/hist_id, для reply бесполезен)."""
    comment = history_item.get("comment")
    if isinstance(comment, dict) and comment.get("id"):
        return str(comment["id"])
    return str(history_item.get("id") or "") or None


def _extract_comment_text(history_item: dict) -> str | None:
    """Подтверждено живым вебхуком (2026-08-13, см. docs/06_open_questions.md
    В7): текст лежит в history_item["comment"]["text_content"] (уже собранная
    строка) — не в history_item["comment_text"]/["comment"] на верхнем уровне,
    как предполагалось до первого реального прогона. Остальные кандидаты
    оставлены как fallback на случай другой формы payload (не удалены — не
    исключено, что ClickUp присылает разные формы для разных типов действий)."""
    comment = history_item.get("comment")
    if isinstance(comment, dict):
        text_content = comment.get("text_content")
        if isinstance(text_content, str) and text_content.strip():
            return text_content.strip()
    for path in ("comment_text", "comment"):
        value = history_item.get(path)
        if isinstance(value, str) and value:
            return value
    after = history_item.get("after")
    if isinstance(after, dict):
        for path in ("comment_text", "comment"):
            value = after.get(path)
            if isinstance(value, str) and value:
                return value
    return None


def _verify_signature(raw_body: bytes, signature_header: str) -> bool:
    secret = settings.clickup_webhook_secret or ""
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header)
