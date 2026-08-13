"""Юнит-тесты agents/mock_actor.py, mock_camera.py, mock_continuity.py,
mock_cost.py — каждый эмитит корректный AgentEvent, ничего не публикует сам."""
from agents import mock_actor, mock_camera, mock_continuity, mock_cost
from core.ids import EventSeqCounter
from core.state_machine import CAMERA_PIPELINE


def test_mock_actor_advance_to_next_stage_follows_transitions():
    counter = EventSeqCounter()
    event = mock_actor.advance_to_next_stage(CAMERA_PIPELINE, "t", "backlog", counter)
    assert event.stage == "planned"
    assert event.kind == "report"
    assert event.event_seq == 1


def test_mock_actor_run_to_completion_reaches_closed_stage():
    counter = EventSeqCounter()
    events = mock_actor.run_to_completion(CAMERA_PIPELINE, "t", "backlog", counter)
    assert [e.stage for e in events] == [
        "planned", "in_progress", "ready_for_verification", "verification", "done",
    ]
    assert events[-1].stage in CAMERA_PIPELINE.closed_stages
    # event_seq монотонен и не переиспользуется
    assert [e.event_seq for e in events] == [1, 2, 3, 4, 5]


def test_mock_camera_recommend_coverage_requires_human():
    counter = EventSeqCounter()
    event = mock_camera.recommend_coverage("t", "in_progress", counter, recommendation="B")
    assert event.kind == "decision_request"
    assert event.requires_human is True
    assert event.payload["recommendation"] == "B"


def test_mock_continuity_flag_inconsistency():
    counter = EventSeqCounter()
    event = mock_continuity.flag_inconsistency("t", "in_progress", counter, conflicting_task_id="SC-039/03")
    assert event.kind == "flag"
    assert event.payload["conflicting_task_id"] == "SC-039/03"


def test_mock_cost_estimate_over_threshold_requires_human():
    counter = EventSeqCounter()
    event = mock_cost.estimate("t", "cost_approval", counter, cost_usd=18.0, threshold_usd=15.0)
    assert event.kind == "decision_request"
    assert event.requires_human is True
    assert event.cost_usd == 18.0


def test_mock_cost_estimate_under_threshold_does_not_require_human():
    counter = EventSeqCounter()
    event = mock_cost.estimate("t", "in_generation", counter, cost_usd=5.0, threshold_usd=15.0)
    assert event.requires_human is False
