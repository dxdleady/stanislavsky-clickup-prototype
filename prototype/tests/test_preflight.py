"""demo/preflight.py — без сети: monkeypatch подменяет ClickUpTrackerAdapter._client.get
(тот же приём, что test_webhook_extraction.py использует для _real_id_to_label —
реального ClickUp в юнит-тестах не бывает, см. CLAUDE.md)."""
from __future__ import annotations

import pytest

from adapters.clickup import ClickUpTrackerAdapter
from core.state_machine import COSTUME_PIPELINE
from demo.preflight import MissingStatusesError, check_list_statuses

ALL_STATUS_NAMES = [definition.name for definition in COSTUME_PIPELINE.stages.values()]


class _FakeListResponse:
    def __init__(self, status_names: list[str]) -> None:
        self._status_names = status_names

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict:
        return {"statuses": [{"status": name} for name in self._status_names]}


def _tracker_reporting(monkeypatch: pytest.MonkeyPatch, status_names: list[str]) -> ClickUpTrackerAdapter:
    tracker = ClickUpTrackerAdapter(token="test-token", workflow=COSTUME_PIPELINE)
    monkeypatch.setattr(tracker._client, "get", lambda *a, **kw: _FakeListResponse(status_names))
    return tracker


def test_check_list_statuses_passes_when_all_expected_present(monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = _tracker_reporting(monkeypatch, ALL_STATUS_NAMES)
    check_list_statuses(tracker, "list123")  # не должно поднять исключение


def test_check_list_statuses_is_case_insensitive(monkeypatch: pytest.MonkeyPatch) -> None:
    # ClickUp нередко возвращает статусы в нижнем регистре независимо от того,
    # как они выглядят в UI (см. ClickUpTrackerAdapter.get_status — тот же приём).
    tracker = _tracker_reporting(monkeypatch, [name.lower() for name in ALL_STATUS_NAMES])
    check_list_statuses(tracker, "list123")


def test_check_list_statuses_raises_naming_exactly_the_missing_ones(monkeypatch: pytest.MonkeyPatch) -> None:
    present = ["In Generation", "Accepted"]
    tracker = _tracker_reporting(monkeypatch, present)

    with pytest.raises(MissingStatusesError) as exc_info:
        check_list_statuses(tracker, "list123")

    message = str(exc_info.value)
    expected_missing = sorted(set(ALL_STATUS_NAMES) - set(present))
    for missing_name in expected_missing:
        assert missing_name in message
    for present_name in present:
        # Уже настроенные статусы не должны попадать в список "не хватает".
        assert f"— {present_name}" not in message and f", {present_name}" not in message

    assert "list123" in message
    assert "docs/11_live_demo_architecture.md" in message


def test_check_list_statuses_raises_when_list_has_no_expected_statuses(monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = _tracker_reporting(monkeypatch, ["to do", "complete"])

    with pytest.raises(MissingStatusesError) as exc_info:
        check_list_statuses(tracker, "list456")

    message = str(exc_info.value)
    for name in ALL_STATUS_NAMES:
        assert name in message
