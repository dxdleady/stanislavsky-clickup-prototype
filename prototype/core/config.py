"""Настройки живого демо-слоя — из .env, никогда не хардкод.

См. docs/11_live_demo_architecture.md — чек-лист того, откуда взять каждое значение.
"""
from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()


class Settings:
    """Всё опционально на уровне конструктора — намеренно.

    discover_ids.py нужен только clickup_token, seed.py нужен ещё list_id,
    webhook.py нужен ещё webhook_secret/bot_user_id. Требовать всё сразу
    при импорте модуля создало бы курицу-и-яйцо (discover_ids.py как раз
    и существует, чтобы узнать часть значений). Конкретный скрипт вызывает
    require(), когда ему реально нужно конкретное значение — тогда и падает
    с понятной ошибкой, а не раньше.
    """

    def __init__(self) -> None:
        self.clickup_token: str | None = os.environ.get("CLICKUP_TOKEN")
        self.clickup_list_id: str | None = os.environ.get("CLICKUP_LIST_ID")
        self.clickup_team_id: str | None = os.environ.get("CLICKUP_TEAM_ID")
        self.clickup_webhook_secret: str | None = os.environ.get("CLICKUP_WEBHOOK_SECRET")
        self.bot_user_id: str | None = os.environ.get("BOT_USER_ID")
        # LLM-провайдер для inbound/comment_router.py и agents/live_demo_agent.py —
        # Qwen (Alibaba, OpenAI-совместимый эндпоинт), не Anthropic (см. core/llm.py).
        self.qwen_api_key: str | None = os.environ.get("QWEN_API_KEY")
        self.qwen_base_url: str | None = os.environ.get("QWEN_BASE_URL")
        self.qwen_model: str | None = os.environ.get("QWEN_MODEL")
        self.require_webhook_signature: bool = _bool(
            os.environ.get("REQUIRE_WEBHOOK_SIGNATURE", "false")
        )

    def require(self, field: str) -> str:
        value = getattr(self, field, None)
        if not value:
            raise RuntimeError(
                f"{field} не задан в .env — см. чек-лист в docs/11_live_demo_architecture.md"
            )
        return value


def _bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


settings = Settings()
