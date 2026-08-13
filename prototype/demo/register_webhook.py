"""Регистрирует webhook на текущий публичный URL (ngrok). Перерегистрировать
при каждой смене URL (перезапуск ngrok без зарезервированного домена).

Подписка только на taskCommentPosted — не на "*", чтобы не разбирать формы
payload, которые нам не нужны (docs/03_clickup_requirements.md §2.2).

Запуск: python -m demo.register_webhook https://xxxx.ngrok-free.app
"""
from __future__ import annotations

import sys

from adapters.clickup import ClickUpTrackerAdapter
from core.config import settings
from core.state_machine import COSTUME_PIPELINE


def main() -> None:
    if len(sys.argv) != 2:
        print("Использование: python -m demo.register_webhook <https-url от ngrok>")
        raise SystemExit(1)

    endpoint = sys.argv[1].rstrip("/") + "/webhook/clickup"

    tracker = ClickUpTrackerAdapter(token=settings.require("clickup_token"), workflow=COSTUME_PIPELINE)
    team_id = settings.require("clickup_team_id")

    webhook = tracker.register_webhook(team_id, endpoint, events=["taskCommentPosted"])
    print(f"Webhook зарегистрирован: {webhook['id']} -> {endpoint}")
    secret = webhook.get("secret")
    if secret:
        print(f"CLICKUP_WEBHOOK_SECRET={secret}  (впишите в .env, если включаете REQUIRE_WEBHOOK_SIGNATURE=true)")
    else:
        print("secret не вернулся в ответе — на день звонка не критично (подпись выключена по умолчанию)")


if __name__ == "__main__":
    main()
