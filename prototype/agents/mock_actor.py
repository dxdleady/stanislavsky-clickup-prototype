"""mock_actor.py — единственный мок, реально гоняющий задачу по пайплайну
(сценарий 01, happy path). Остальные три мока эмитят единичные события.
"""
from __future__ import annotations

from agents._shared import build_event
from core.events import AgentEvent
from core.ids import EventSeqCounter, new_correlation_id
from core.state_machine import WorkflowConfig

DEFAULT_AGENT_ID = "actor.disciple_ivanov"


def advance_to_next_stage(
    workflow: WorkflowConfig,
    task_id: str,
    current_stage: str,
    seq_counter: EventSeqCounter,
    agent_id: str = DEFAULT_AGENT_ID,
) -> AgentEvent:
    """Один шаг "вперёд" по пайплайну.

    # ASSUMPTION(open_questions:В11): "вперёд" — первый
    # элемент WorkflowConfig.transitions[current_stage], а не отдельно
    # помеченное свойство перехода (WorkflowConfig такого не различает). Для
    # CAMERA_PIPELINE и COSTUME_PIPELINE это совпадает с содержательным "не в
    # deferred/blocked" по тому, как заведён порядок transitions в
    # core/state_machine.py — не гарантировано структурой типа, при добавлении
    # домена с другим порядком эвристика может перестать работать.
    """
    next_stage = workflow.transitions[current_stage][0]
    return build_event(
        kind="report",
        agent_id=agent_id,
        task_id=task_id,
        stage=next_stage,
        correlation_id=new_correlation_id(),
        event_seq=seq_counter.next(task_id),
        payload={"text": f"{agent_id}: {current_stage} -> {next_stage}"},
    )


def run_to_completion(
    workflow: WorkflowConfig,
    task_id: str,
    start_stage: str,
    seq_counter: EventSeqCounter,
    agent_id: str = DEFAULT_AGENT_ID,
) -> list[AgentEvent]:
    """Генерирует события от start_stage до ближайшей closed_stage. Не публикует
    их сама — это дело вызывающей стороны (sim/system.py), мок только эмитит."""
    events: list[AgentEvent] = []
    stage = start_stage
    while stage not in workflow.closed_stages:
        event = advance_to_next_stage(workflow, task_id, stage, seq_counter, agent_id)
        events.append(event)
        stage = event.stage
    return events
