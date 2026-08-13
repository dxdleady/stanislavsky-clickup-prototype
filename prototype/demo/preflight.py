"""Pre-flight проверка перед demo/seed.py: у целевого ClickUp List уже настроены
кастомные статусы, ожидаемые COSTUME_PIPELINE, ДО того, как сид начнёт удалять
и создавать задачи. ClickUp REST API не умеет создавать кастомные статусы
(docs/03_clickup_requirements.md §4) — это одноразовая ручная настройка в UI,
так что проверка не чинит доску, а фейлит быстро понятным списком того, чего
не хватает, вместо непонятной ошибки где-то в середине сида/вебхука.

Источник ожидаемого набора статусов — code (core/state_machine.py::COSTUME_PIPELINE),
не текстовые списки в docs/, по правилу CLAUDE.md "Stage — не enum": легальные
значения задаёт только WorkflowConfig. Расхождение между документами, которое
это решает: docs/03_clickup_requirements.md §4 перечисляет 7-стадийную generic-
модель (Backlog → Planned → In Progress → Ready for Verification → Verification →
Done + Deferred) — это статусы CAMERA_PIPELINE, другого домена, не применимые к
этому живому демо. docs/11_live_demo_architecture.md §3 п.1 в прозе называет
только 6 из 10 реальных стадий COSTUME_PIPELINE, хотя сам текст говорит "полный
набор... не только 4" — то есть проза документа уже отстала от кода. Здесь взят
код (COSTUME_PIPELINE.stages) как единственный источник истины.
"""
from __future__ import annotations

from adapters.clickup import ClickUpTrackerAdapter
from core.state_machine import COSTUME_PIPELINE, WorkflowConfig


class MissingStatusesError(RuntimeError):
    """List не настроен под ожидаемый workflow — готовое сообщение уже в .args[0]."""


def check_list_statuses(
    tracker: ClickUpTrackerAdapter,
    list_id: str,
    workflow: WorkflowConfig = COSTUME_PIPELINE,
) -> None:
    """Сверяет реальные статусы List с именами стадий workflow. Молча
    возвращается, если всё совпало; иначе поднимает MissingStatusesError с
    точным списком недостающих статусов и инструкцией, что сделать руками.

    Сравнение регистронезависимое — тот же принцип, что уже применяется в
    ClickUpTrackerAdapter.get_status() при обратном переводе статуса в Stage.
    """
    expected_names = sorted({definition.name for definition in workflow.stages.values()})
    actual_lower = {name.strip().lower() for name in tracker.get_list_statuses(list_id)}
    missing = [name for name in expected_names if name.lower() not in actual_lower]
    if not missing:
        return
    raise MissingStatusesError(
        f"ClickUp List {list_id} не настроен под workflow '{workflow.id}': "
        f"не хватает {len(missing)} кастомных статус(ов) — {', '.join(missing)}.\n"
        "ClickUp REST API не умеет создавать кастомные статусы (docs/03_clickup_requirements.md §4) — "
        "добавьте их вручную: ClickUp → список → Settings → Statuses. "
        "Полный чек-лист разовой настройки — docs/11_live_demo_architecture.md §3, п.1."
    )
