"""Единая обёртка над LLM-провайдером (Qwen, OpenAI-совместимый эндпоинт) —
общее место для agents/live_demo_agent.py и inbound/comment_router.py, чтобы
конструирование клиента не дублировалось по файлам (см. CLAUDE.md: любой новый
LLM-клиент — сюда, не инлайном в agents/inbound).

Раньше здесь был Anthropic (Claude Haiku 4.5, structured output через
`.messages.parse`) — переведено на Qwen, т.к. у пользователя нет отдельного
Anthropic-ключа, только Qwen (Model Studio, workspace-эндпоинт).
"""
from __future__ import annotations

from typing import TypeVar

import openai
from pydantic import BaseModel

from core.config import settings

DEFAULT_BASE_URL = "https://ws-2brwesz49c63ucih.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1"
DEFAULT_MODEL = "qwen3.6-flash"

T = TypeVar("T", bound=BaseModel)


def build_client() -> openai.OpenAI:
    return openai.OpenAI(
        api_key=settings.require("qwen_api_key"),
        base_url=settings.qwen_base_url or DEFAULT_BASE_URL,
    )


def _model() -> str:
    return settings.qwen_model or DEFAULT_MODEL


def complete_text(
    client: openai.OpenAI, prompt: str, *, timeout: float, max_tokens: int = 200
) -> str:
    """Возвращает свободный текст ответа. Не глотает исключения — таймаут/сеть/
    отказ модели остаются заботой вызывающего кода (у него уже есть фоллбек)."""
    response = client.with_options(timeout=timeout, max_retries=0).chat.completions.create(
        model=_model(),
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    return (response.choices[0].message.content or "").strip()


def complete_structured(
    client: openai.OpenAI, prompt: str, schema: type[T], *, timeout: float, max_tokens: int = 256
) -> T:
    """Структурированный вывод через JSON-режим, не через строгий structured-output
    (`.beta.chat.completions.parse` — OpenAI-специфичное расширение). Неизвестно,
    поддерживает ли DashScope compatible-mode строгую схему server-side.

    # ASSUMPTION(open_questions:В10): не подтверждено живым тестом
    строгого structured-output на этом эндпоинте — используется более простой и
    более совместимый `response_format={"type": "json_object"}` + валидация
    схемой на своей стороне. Если модель вернёт не-JSON или не по схеме —
    `model_validate_json` бросит исключение, вызывающий код обязан это ловить
    (тот же паттерн, что уже есть у classify_comment/generate_reply).
    """
    json_prompt = (
        f"{prompt}\n\n"
        f"Ответь строго валидным JSON без пояснений и без markdown-обрамления, "
        f"соответствующим этой схеме:\n{schema.model_json_schema()}"
    )
    response = client.with_options(timeout=timeout, max_retries=0).chat.completions.create(
        model=_model(),
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": json_prompt}],
        response_format={"type": "json_object"},
    )
    content = response.choices[0].message.content or "{}"
    return schema.model_validate_json(content)
