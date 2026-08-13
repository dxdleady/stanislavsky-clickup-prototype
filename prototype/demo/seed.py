"""Идемпотентный seed демо-доски: сносит старые [DEMO]-задачи, создаёт 4 заново
с реальной историей комментариев и стартовым статусом, пишет task_map.json
(метка -> реальный ClickUp id) — источник для webhook.py/live_demo_agent.py.

Запускать перед каждым звонком: python -m demo.seed
"""
from __future__ import annotations

import json
from pathlib import Path

from adapters.clickup import ClickUpTrackerAdapter
from agents.live_demo_agent import AGENT_LABEL
from core.config import settings
from core.state_machine import COSTUME_PIPELINE
from demo.preflight import check_list_statuses
from demo.statuses import ACTIVE_TASK_LABELS, SEED_COMMENTS, TASK_OWNERS, TASK_STARTING_STAGE

TASK_MAP_PATH = Path(__file__).parent / "task_map.json"


def main() -> None:
    tracker = ClickUpTrackerAdapter(token=settings.require("clickup_token"), workflow=COSTUME_PIPELINE)
    list_id = settings.require("clickup_list_id")

    # Fail fast здесь, а не глубоко внутри create_task/set_status — see demo/preflight.py.
    check_list_statuses(tracker, list_id)

    print("Удаляю старые демо-задачи...")
    for task in tracker.list_demo_tasks(list_id):
        tracker.delete_task(task["id"])
        print(f"  удалена {task['id']} ({task['name']})")

    task_map: dict[str, str] = {}
    for label, owner in TASK_OWNERS.items():
        stage = TASK_STARTING_STAGE[label]
        # Роль в начале названия — единственный способ "разделить" карточки без
        # отдельных ClickUp-аккаунтов на агента (см. обсуждение в чате): человек
        # видит владельца прямо на доске, не открывая карточку и не читая комментарии.
        # На is_demo_task/task_map (метка -> id) это не влияет — внутри всё по label.
        display_name = f"{AGENT_LABEL[owner]} · {label}"
        real_id = tracker.create_task(list_id, name=display_name, status=stage)
        task_map[label] = real_id
        print(f"создана {label} -> {real_id}  (owner={owner}, stage={COSTUME_PIPELINE.stages[stage].name})")

        for comment in SEED_COMMENTS.get(label, []):
            tracker.post_comment(real_id, comment)

    TASK_MAP_PATH.write_text(json.dumps(task_map, ensure_ascii=False, indent=2))
    print(f"\ntask_map.json записан: {TASK_MAP_PATH}")
    print("Активные для живого комментария:", ACTIVE_TASK_LABELS)


if __name__ == "__main__":
    main()
