"""Юнит-тесты чистых функций inbound/webhook.py — без сети, без реального
FastAPI-запроса. conftest.py гарантирует фиктивный CLICKUP_TOKEN до импорта
модуля (он создаёт ClickUpTrackerAdapter на уровне модуля)."""
import hashlib
import hmac

import inbound.webhook as wh


def test_extract_comment_text_from_top_level_field():
    assert wh._extract_comment_text({"comment_text": "привет"}) == "привет"


def test_extract_comment_text_from_after_dict():
    assert wh._extract_comment_text({"after": {"comment_text": "изменено"}}) == "изменено"


def test_extract_comment_text_missing_returns_none():
    assert wh._extract_comment_text({"unrelated": "field"}) is None


def test_extract_comment_text_from_real_confirmed_shape():
    """Подтверждено живым вебхуком 2026-08-13 (docs/06_open_questions.md В7):
    текст лежит в history_item["comment"]["text_content"], не на верхнем уровне."""
    history_item = {"comment": {"id": "90120252588096", "text_content": "убери контровой\n"}}
    assert wh._extract_comment_text(history_item) == "убери контровой"


def test_extract_comment_id_from_real_confirmed_shape():
    history_item = {"id": "5215131299874534469", "comment": {"id": "90120252588096"}}
    assert wh._extract_comment_id(history_item) == "90120252588096"  # не hist_id верхнего уровня


def test_extract_comment_id_falls_back_to_top_level_id():
    assert wh._extract_comment_id({"id": "c1"}) == "c1"


def test_extract_inbound_event_ignores_non_comment_events():
    payload = {"event": "taskStatusUpdated", "task_id": "real1"}
    assert wh._extract_inbound_event(payload) is None


def test_extract_inbound_event_ignores_unknown_task():
    payload = {
        "event": "taskCommentPosted",
        "task_id": "unknown-real-id",
        "history_items": [{"id": "c1", "user": {"id": "999"}, "comment_text": "x"}],
    }
    assert wh._extract_inbound_event(payload) is None


def test_extract_inbound_event_filters_echo_from_bot(monkeypatch):
    monkeypatch.setitem(wh._real_id_to_label, "real1", "SC-042/09")
    monkeypatch.setattr(wh.settings, "bot_user_id", "999")
    payload = {
        "event": "taskCommentPosted",
        "task_id": "real1",
        "history_items": [{"id": "c1", "user": {"id": "999"}, "comment_text": "эхо от бота"}],
    }
    assert wh._extract_inbound_event(payload) is None  # docs/00_overview.md §5.1 anti-echo


def test_extract_inbound_event_happy_path(monkeypatch):
    monkeypatch.setitem(wh._real_id_to_label, "real1", "SC-042/09")
    monkeypatch.setattr(wh.settings, "bot_user_id", "999")
    payload = {
        "event": "taskCommentPosted",
        "task_id": "real1",
        "history_items": [{"id": "c42", "user": {"id": "human-id"}, "comment_text": "уберите контровой"}],
    }
    event = wh._extract_inbound_event(payload)
    assert event is not None
    assert event.clickup_task_id == "SC-042/09"  # переведено в метку, не реальный id
    assert event.clickup_comment_id == "c42"
    assert event.body == "уберите контровой"


def test_verify_signature_matches_hmac_sha256(monkeypatch):
    monkeypatch.setattr(wh.settings, "clickup_webhook_secret", "s3cr3t")
    body = b'{"event": "taskCommentPosted"}'
    correct = hmac.new(b"s3cr3t", body, hashlib.sha256).hexdigest()
    assert wh._verify_signature(body, correct) is True
    assert wh._verify_signature(body, "wrong-signature") is False
