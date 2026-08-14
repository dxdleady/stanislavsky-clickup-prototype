"""Contract test suite для TrackerAdapter (adapters/base.py).

Protocol — structural typing, реализации (ClickUpTrackerAdapter, MemoryTrackerAdapter)
не наследуются от него (см. CLAUDE.md), поэтому ничего не мешает им незаметно
разойтись между собой. Этот файл — защита от такого расхождения на двух уровнях:

1. `isinstance(tracker, TrackerAdapter)` — ловит забытый метод (@runtime_checkable
   проверяет только имена методов, не сигнатуры и не поведение).
2. Один и тот же набор поведенческих тестов, параметризованный по обеим
   реализациям, — ловит расхождение в поведении (например, если один адаптер
   молча теряет parent_comment_id при post_reply, а другой — нет).

ClickUpTrackerAdapter тестируется без сети через httpx.MockTransport
(_FakeClickUpBackend ниже) — тот же принцип, что и tests/test_preflight.py
(monkeypatch tracker._client), см. CLAUDE.md "Сетевые вызовы... — мокать".
"""
from __future__ import annotations

import itertools
import json

import httpx
import pytest

from adapters.base import TrackerAdapter
from adapters.clickup import API_BASE, ClickUpTrackerAdapter
from adapters.memory import MemoryTrackerAdapter
from core.events import AgentEvent, LinkedTaskRequest
from core.state_machine import CAMERA_PIPELINE, WorkflowConfig

SOURCE_TASK_ID = "task-1"
LIST_ID = "list-1"


class _FakeClickUpBackend:
    """Ин-мемори двойник ClickUp REST API — реализует только то подмножество
    endpoint'ов, которое использует TrackerAdapter-протокол (не весь API)."""

    def __init__(self, workflow: WorkflowConfig) -> None:
        self._workflow = workflow
        self._tasks: dict[str, dict[str, str]] = {}
        self._comments: dict[str, dict[str, str | None]] = {}
        self._task_ids = itertools.count(1)
        self._comment_ids = itertools.count(1)

    def seed_task(self, task_id: str, list_id: str, stage: str) -> None:
        self._tasks[task_id] = {
            "list_id": list_id,
            "status": self._workflow.stages[stage].name,
            "description": "",
        }

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        # request.url.path включает путь из base_url (API_BASE = ".../api/v2") —
        # httpx строит абсолютный URL из base_url + relative url ещё до отправки в transport.
        method = request.method
        parts = request.url.path.removeprefix("/api/v2").strip("/").split("/")
        body = json.loads(request.read() or b"{}")

        if method == "GET" and len(parts) == 2 and parts[0] == "task":
            task = self._tasks[parts[1]]
            return httpx.Response(200, json={
                "status": {"status": task["status"]},
                "list": {"id": task["list_id"]},
                "text_content": task["description"],
            })

        if method == "GET" and len(parts) == 3 and parts[0] == "list" and parts[2] == "task":
            list_id = parts[1]
            tasks = [
                {"id": tid, "text_content": t["description"]}
                for tid, t in self._tasks.items() if t["list_id"] == list_id
            ]
            return httpx.Response(200, json={"tasks": tasks})

        if method == "POST" and len(parts) == 3 and parts[0] == "task" and parts[2] == "comment":
            comment_id = f"comment-{next(self._comment_ids)}"
            self._comments[comment_id] = {"task_id": parts[1], "parent_id": None}
            return httpx.Response(200, json={"id": comment_id})

        if method == "POST" and len(parts) == 3 and parts[0] == "comment" and parts[2] == "reply":
            parent_id = parts[1]
            comment_id = f"comment-{next(self._comment_ids)}"
            self._comments[comment_id] = {"task_id": self._comments[parent_id]["task_id"], "parent_id": parent_id}
            return httpx.Response(200, json={"id": comment_id})

        if method == "POST" and len(parts) == 3 and parts[0] == "list" and parts[2] == "task":
            list_id = parts[1]
            new_id = f"new-task-{next(self._task_ids)}"
            self._tasks[new_id] = {
                "list_id": list_id,
                "status": next(iter(self._workflow.stages.values())).name,
                "description": body.get("description", ""),
            }
            return httpx.Response(200, json={"id": new_id})

        if method == "PUT" and len(parts) == 2 and parts[0] == "task":
            self._tasks[parts[1]]["status"] = body["status"]
            return httpx.Response(200, json={})

        raise AssertionError(f"_FakeClickUpBackend: неожиданный запрос {method} {request.url.path}")


def _memory_tracker() -> MemoryTrackerAdapter:
    tracker = MemoryTrackerAdapter(workflow=CAMERA_PIPELINE)
    tracker.set_status(SOURCE_TASK_ID, "backlog")
    return tracker


def _clickup_tracker() -> ClickUpTrackerAdapter:
    backend = _FakeClickUpBackend(CAMERA_PIPELINE)
    backend.seed_task(SOURCE_TASK_ID, list_id=LIST_ID, stage="backlog")
    tracker = ClickUpTrackerAdapter(token="test-token", workflow=CAMERA_PIPELINE)
    tracker._client = httpx.Client(base_url=API_BASE, transport=backend.transport())
    return tracker


_FACTORIES = {"memory": _memory_tracker, "clickup": _clickup_tracker}


@pytest.fixture(params=list(_FACTORIES))
def tracker(request: pytest.FixtureRequest) -> TrackerAdapter:
    return _FACTORIES[request.param]()


def _linked_task_request(workflow_kind: str = "follow_up") -> LinkedTaskRequest:
    return LinkedTaskRequest(
        source_task_id=SOURCE_TASK_ID,
        assignee_agent_id="agent-x",
        comment_text="правка адресована другому агенту",
        reasoning="упомянут agent-x, не хозяин исходной задачи",
        workflow_kind=workflow_kind,
    )


def test_adapter_satisfies_tracker_adapter_protocol(tracker: TrackerAdapter) -> None:
    # Ловит забытый метод (не сигнатуру/поведение — см. докстринг файла и base.py).
    assert isinstance(tracker, TrackerAdapter)


def test_post_reply_threads_under_the_comment_it_replies_to(tracker: TrackerAdapter) -> None:
    comment_id = tracker.post_comment(SOURCE_TASK_ID, "первый комментарий")
    reply_id = tracker.post_reply(comment_id, "ответ")
    assert reply_id != comment_id


def test_set_status_then_get_status_roundtrips(tracker: TrackerAdapter) -> None:
    tracker.set_status(SOURCE_TASK_ID, "planned")
    assert tracker.get_status(SOURCE_TASK_ID) == "planned"


def test_is_processed_reflects_mark_processed(tracker: TrackerAdapter) -> None:
    assert tracker.is_processed("corr-1") is False
    tracker.mark_processed("corr-1")
    assert tracker.is_processed("corr-1") is True


def test_create_linked_task_returns_a_new_task_id(tracker: TrackerAdapter) -> None:
    new_id = tracker.create_linked_task(_linked_task_request())
    assert new_id != SOURCE_TASK_ID


def test_get_linked_workflow_task_finds_task_created_for_that_workflow_kind(tracker: TrackerAdapter) -> None:
    new_id = tracker.create_linked_task(_linked_task_request(workflow_kind="follow_up"))
    assert tracker.get_linked_workflow_task(SOURCE_TASK_ID, "follow_up") == new_id
    # Другой workflow_kind не должен находить чужую связанную задачу.
    assert tracker.get_linked_workflow_task(SOURCE_TASK_ID, "payment_approval") != new_id


def test_publish_without_thread_ref_moves_status_to_event_stage(tracker: TrackerAdapter) -> None:
    event = AgentEvent(
        kind="report",
        agent_id="agent-x",
        task_id=SOURCE_TASK_ID,
        stage="planned",
        correlation_id="corr-1",
        event_seq=1,
        payload={"text": "готово"},
    )
    result = tracker.publish(event)
    assert result.ok is True
    assert tracker.get_status(SOURCE_TASK_ID) == "planned"
