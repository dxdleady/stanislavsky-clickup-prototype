"""Печатает BOT_USER_ID и доступные CLICKUP_TEAM_ID — заполнить .env перед
первым запуском (нужен только CLICKUP_TOKEN, остального ещё может не быть —
см. core/config.py, Settings намеренно не требует всё сразу).

Запуск: python -m demo.discover_ids
"""
from __future__ import annotations

from adapters.clickup import ClickUpTrackerAdapter
from core.config import settings
from core.state_machine import COSTUME_PIPELINE


def main() -> None:
    tracker = ClickUpTrackerAdapter(token=settings.require("clickup_token"), workflow=COSTUME_PIPELINE)

    user = tracker.get_authorized_user()
    print(f"BOT_USER_ID={user['id']}  ({user.get('username')}, {user.get('email')})")

    for team in tracker.get_authorized_teams():
        print(f"CLICKUP_TEAM_ID={team['id']}  ({team.get('name')})")


if __name__ == "__main__":
    main()
