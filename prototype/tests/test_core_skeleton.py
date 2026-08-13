"""Смоук-тесты скелета: доказывают, что контракты из docs/01_architecture_plan.md
реально стыкуются в коде, а не только на бумаге. Не заменяют полный test plan
(docs/04_test_plan.md, сценарии 01-07) — те пишутся в Фазах 1-5 (docs/02_prototype_plan.md).
"""
from datetime import datetime

import pytest

from adapters.memory import MemoryTrackerAdapter
from core.clock import FakeClock
from core.events import AgentEvent, LinkedTaskRequest
from core.ids import EventSeqCounter, new_batch_id, new_correlation_id
from core.state_machine import CAMERA_PIPELINE, COSTUME_PIPELINE


def make_event(task_id: str, stage: str, seq: int, **overrides) -> AgentEvent:
    defaults = dict(
        kind="report",
        agent_id="actor.disciple_ivanov",
        task_id=task_id,
        stage=stage,
        correlation_id=new_correlation_id(),
        event_seq=seq,
    )
    defaults.update(overrides)
    return AgentEvent(**defaults)


# --- core/state_machine.py ---

def test_valid_forward_transitions():
    assert CAMERA_PIPELINE.is_valid_transition("backlog", "planned")
    assert CAMERA_PIPELINE.is_valid_transition("verification", "done")
    assert CAMERA_PIPELINE.is_valid_transition("verification", "in_progress")  # Regenerate


def test_invalid_transition_rejected():
    assert not CAMERA_PIPELINE.is_valid_transition("backlog", "done")  # нельзя перепрыгнуть все стадии


def test_two_independent_workflow_configs_do_not_share_stages():
    # docs/08_dataset_analysis.md находка №1: два реальных домена, два разных набора статусов
    assert "in_progress" in CAMERA_PIPELINE.stages
    assert "in_progress" not in COSTUME_PIPELINE.stages
    assert "wardrobe_check" in COSTUME_PIPELINE.stages
    assert "wardrobe_check" not in CAMERA_PIPELINE.stages


def test_costume_pipeline_transitions_match_dataset_traces():
    # SC-042/08 в датасете: backlog -> in_generation -> qc -> regenerate -> qc -> accepted
    assert COSTUME_PIPELINE.is_valid_transition("backlog", "in_generation")
    assert COSTUME_PIPELINE.is_valid_transition("qc", "regenerate")
    assert COSTUME_PIPELINE.is_valid_transition("regenerate", "in_generation")
    assert COSTUME_PIPELINE.is_valid_transition("qc", "accepted")
    assert not COSTUME_PIPELINE.is_valid_transition("accepted", "qc")  # closed — не откатывается


def test_costume_pipeline_escalation_policies_match_dataset():
    # docs/source/stanislavsky_costume_demo_dataset_v2.json escalation_policies
    cost_policy = COSTUME_PIPELINE.escalation_for("cost_approval")
    assert cost_policy.timeout_min == 30
    assert cost_policy.action == "pause"

    blocked_policy = COSTUME_PIPELINE.escalation_for("blocked")
    assert blocked_policy.timeout_min == 240
    assert blocked_policy.repeat is True


def test_deferred_reachable_from_any_active_stage_and_returns():
    assert CAMERA_PIPELINE.is_valid_transition("in_progress", "deferred")
    assert CAMERA_PIPELINE.is_valid_transition("deferred", "in_progress")  # возврат в исходную стадию


def test_rework_branch_classification_matches_grilled_fix():
    # находка №4 (docs/07_grilled.md): backlog/planned — дешёвая правка, дальше — нужна новая генерация
    assert CAMERA_PIPELINE.classify_branch("backlog") == "cheap_edit"
    assert CAMERA_PIPELINE.classify_branch("planned") == "cheap_edit"
    assert CAMERA_PIPELINE.classify_branch("in_progress") == "needs_regeneration"
    assert CAMERA_PIPELINE.classify_branch("verification") == "needs_regeneration"


def test_waits_for_human_stage_has_no_rework_branch():
    # стадии ожидания человека — сами точка решения, вопрос branch там не имеет смысла
    with pytest.raises(ValueError):
        COSTUME_PIPELINE.classify_branch("cost_approval")


# --- core/clock.py ---

def test_fake_clock_advances_deterministically():
    clock = FakeClock(start=datetime(2026, 8, 12, 9, 0, 0))
    t0 = clock.now()
    clock.advance(65)
    assert (clock.now() - t0).total_seconds() == 65


# --- core/ids.py ---

def test_correlation_and_batch_ids_are_distinct_namespaces():
    corr = new_correlation_id()
    batch = new_batch_id()
    assert corr.startswith("corr_")
    assert batch.startswith("batch_")
    assert corr != batch


def test_event_seq_counter_monotonic_per_task():
    counter = EventSeqCounter()
    assert [counter.next("SC-042/SHOT-07") for _ in range(3)] == [1, 2, 3]
    assert counter.next("SC-999/SHOT-01") == 1  # другой task_id — свой счётчик


# --- adapters/memory.py: мини-версия сценария 01 (happy path) ---

def test_memory_adapter_happy_path_feed_order():
    tracker = MemoryTrackerAdapter()
    seq = EventSeqCounter()
    task_id = "SC-042/SHOT-07"

    stages = ["backlog", "planned", "in_progress", "ready_for_verification", "verification", "done"]
    for stage in stages:
        result = tracker.publish(make_event(task_id, stage, seq.next(task_id)))
        assert result.ok, result.detail

    feed = tracker.get_feed(task_id)
    assert [e.stage for e in feed] == stages
    assert tracker.get_status(task_id) == "done"


def test_memory_adapter_costume_pipeline_regenerate_loop():
    # SC-042/08 в датасете: backlog -> in_generation -> qc -> regenerate -> qc -> accepted,
    # в ОДНОЙ задаче — docs/08_dataset_analysis.md находка №3 (В4 подтверждён датасетом).
    tracker = MemoryTrackerAdapter(workflow=COSTUME_PIPELINE)
    seq = EventSeqCounter()
    task_id = "SC-042/08"

    for stage in ["backlog", "in_generation", "qc", "regenerate", "in_generation", "qc", "accepted"]:
        result = tracker.publish(make_event(task_id, stage, seq.next(task_id)))
        assert result.ok, result.detail

    assert tracker.get_status(task_id) == "accepted"
    assert len(tracker.get_feed(task_id)) == 7  # включая проход через Regenerate — не новый task_id


def test_memory_adapter_idempotent_approve():
    tracker = MemoryTrackerAdapter()
    corr_id = new_correlation_id()

    assert not tracker.is_processed(corr_id)
    tracker.mark_processed(corr_id)
    assert tracker.is_processed(corr_id)
    # повторная попытка — тот же correlation_id, вызывающий код должен пропустить обработку
    assert tracker.is_processed(corr_id)


def test_memory_adapter_outage_blocks_publish_without_raising():
    tracker = MemoryTrackerAdapter()
    seq = EventSeqCounter()
    task_id = "SC-050/SHOT-01"

    tracker.simulate_outage(True)
    result = tracker.publish(make_event(task_id, "backlog", seq.next(task_id)))
    assert not result.ok
    assert tracker.get_feed(task_id) == []  # ничего не доставлено, но и не упало

    tracker.simulate_outage(False)
    result = tracker.publish(make_event(task_id, "backlog", seq.next(task_id)))
    assert result.ok
    assert len(tracker.get_feed(task_id)) == 1


def test_memory_adapter_threaded_reply():
    tracker = MemoryTrackerAdapter()
    top = tracker.post_comment("SC-042/SHOT-07", "убери контровой")
    reply = tracker.post_reply(top, "принято, новая итерация")

    replies = tracker.get_replies(top)
    assert len(replies) == 1
    assert replies[0].comment_id == reply


def test_memory_adapter_linked_task_for_previous_iteration():
    tracker = MemoryTrackerAdapter()
    new_task_id = tracker.create_linked_task(
        LinkedTaskRequest(
            source_task_id="SC-039/SHOT-03",
            assignee_agent_id="camera.ivanov",
            priority="high",
            comment_text="давайте пересъедём эту сцену — свет не совпадает с предыдущим дублем",
            reasoning="комментарий по существу про камеру, не про костюм",
        )
    )
    assert new_task_id != "SC-039/SHOT-03"
    assert tracker.get_status(new_task_id) == "backlog"
