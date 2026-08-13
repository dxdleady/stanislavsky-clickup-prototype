"""Юнит-тесты inbound/router.py — настоящий детерминированный RuleBasedRouter,
без сети и без LLM-моков (см. inbound/comment_router.py::CommentRouter и
tests/test_comment_router.py для узкого LLM-пути живого демо-среза)."""
from datetime import datetime

from core.events import InboundBatch, InboundEvent, TaskState
from core.state_machine import CAMERA_PIPELINE
from inbound.router import RuleBasedRouter


def _batch(task_id: str, bodies: list[str | None], comment_ids: list[str | None] | None = None) -> InboundBatch:
    comment_ids = comment_ids or [f"c{i}" for i in range(len(bodies))]
    events = [
        InboundEvent(
            clickup_task_id=task_id,
            clickup_comment_id=comment_id,
            author_user_id="human-1",
            kind="comment",
            body=body,
            received_at=datetime(2026, 8, 13, 9, 0),
        )
        for body, comment_id in zip(bodies, comment_ids)
    ]
    return InboundBatch(
        batch_id="batch_test",
        clickup_task_id=task_id,
        events=events,
        window_opened_at=datetime(2026, 8, 13, 9, 0),
        window_closed_at=datetime(2026, 8, 13, 9, 0),
    )


def _empty_batch(task_id: str = "SC-042/SHOT-07") -> InboundBatch:
    now = datetime(2026, 8, 13, 9, 0)
    return InboundBatch(batch_id="b", clickup_task_id=task_id, events=[], window_opened_at=now, window_closed_at=now)


router = RuleBasedRouter(CAMERA_PIPELINE)


def test_route_empty_batch_escalates():
    action = router.route(_empty_batch(), task_state=None)
    assert action.kind == "escalate"


def test_route_unknown_task_escalates():
    action = router.route(_batch("SC-999/UNKNOWN", ["любой текст"]), task_state=None)
    assert action.kind == "escalate"


def test_route_active_stage_no_mention_delivers_to_current_agent():
    state = TaskState(
        task_id="SC-042/SHOT-07", stage="in_progress",
        current_agent_id="camera.ivanov", known_agent_ids=frozenset({"camera.ivanov"}),
    )
    action = router.route(_batch("SC-042/SHOT-07", ["убери контровой"]), task_state=state)
    assert action.kind == "deliver_to_agent"
    assert action.target_agent_id == "camera.ivanov"
    assert action.payload["comment_text"] == "убери контровой"
    assert action.payload["thread_ref"] == "c0"


def test_route_active_stage_known_mention_overrides_current_agent():
    state = TaskState(
        task_id="SC-042/SHOT-07", stage="in_progress",
        current_agent_id="camera.ivanov", known_agent_ids=frozenset({"camera.ivanov", "continuity.checker"}),
    )
    action = router.route(_batch("SC-042/SHOT-07", ["@continuity.checker это точно ок?"]), task_state=state)
    assert action.kind == "deliver_to_agent"
    assert action.target_agent_id == "continuity.checker"


def test_route_active_stage_unknown_mention_escalates():
    state = TaskState(
        task_id="SC-042/SHOT-07", stage="in_progress",
        current_agent_id="camera.ivanov", known_agent_ids=frozenset({"camera.ivanov"}),
    )
    action = router.route(_batch("SC-042/SHOT-07", ["@craft_services что там с обедом"]), task_state=state)
    assert action.kind == "escalate"
    assert action.payload["reason"] == "mention of unknown agent_id"


def test_route_active_stage_no_current_agent_and_no_mention_escalates():
    state = TaskState(task_id="SC-042/SHOT-07", stage="in_progress", current_agent_id=None)
    action = router.route(_batch("SC-042/SHOT-07", ["текст"]), task_state=state)
    assert action.kind == "escalate"


def test_route_closed_stage_creates_new_ticket_with_hardcoded_priority():
    state = TaskState(
        task_id="SC-039/SHOT-03", stage="done",
        current_agent_id="camera.ivanov", known_agent_ids=frozenset({"camera.ivanov"}),
    )
    action = router.route(_batch("SC-039/SHOT-03", ["тут пальто было не то"]), task_state=state)
    assert action.kind == "new_ticket"
    assert action.target_agent_id == "camera.ivanov"
    assert action.payload["priority"] == "high"
    assert action.payload["source_task_id"] == "SC-039/SHOT-03"


def test_route_combines_multiple_events_in_one_batch():
    """Сценарий 04 — вся пачка комментариев обрабатывается как одно действие."""
    state = TaskState(
        task_id="SC-042/SHOT-07", stage="in_progress",
        current_agent_id="camera.ivanov", known_agent_ids=frozenset({"camera.ivanov"}),
    )
    action = router.route(_batch("SC-042/SHOT-07", ["1", "2", "3"]), task_state=state)
    assert action.kind == "deliver_to_agent"
    assert action.payload["comment_text"] == "1\n2\n3"
