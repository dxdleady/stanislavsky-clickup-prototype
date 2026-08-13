"""Юнит-тесты inbound/comment_router.py (LLM-путь живого демо-среза) — без сети.
Фейковый клиент мимикрирует openai.OpenAI ровно настолько, насколько нужно
core.llm.complete_structured: .with_options(...).chat.completions.create(...)
-> response.choices[0].message.content (JSON-строка)."""
from core.events import InboundBatch, InboundEvent
from inbound.comment_router import CommentClassification, CommentRouter, classify_comment
from datetime import datetime


class _FakeMessage:
    def __init__(self, content: str | None) -> None:
        self.content = content


class _FakeChoice:
    def __init__(self, content: str | None) -> None:
        self.message = _FakeMessage(content)


class _FakeCompletion:
    def __init__(self, content: str | None) -> None:
        self.choices = [_FakeChoice(content)]


class _FakeCompletions:
    def __init__(self, content: str | None = None, raises: Exception | None = None) -> None:
        self._content = content
        self._raises = raises

    def create(self, **kwargs):
        if self._raises:
            raise self._raises
        return _FakeCompletion(self._content)


class _FakeChat:
    def __init__(self, **kwargs) -> None:
        self.completions = _FakeCompletions(**kwargs)


class _FakeClient:
    """Мимикрирует openai.OpenAI: .with_options(...).chat.completions.create(...)."""

    def __init__(self, content: str | None = None, raises: Exception | None = None) -> None:
        self.chat = _FakeChat(content=content, raises=raises)

    def with_options(self, **kwargs):
        return self


def _batch(task_id: str, body: str | None, comment_id: str | None = "c1") -> InboundBatch:
    event = InboundEvent(
        clickup_task_id=task_id,
        clickup_comment_id=comment_id,
        author_user_id="human-1",
        kind="comment",
        body=body,
        received_at=datetime(2026, 8, 13, 9, 0),
    )
    return InboundBatch(
        batch_id="batch_test",
        clickup_task_id=task_id,
        events=[event],
        window_opened_at=event.received_at,
        window_closed_at=event.received_at,
    )


# --- classify_comment ---


def test_classify_comment_fallback_on_exception():
    client = _FakeClient(raises=RuntimeError("сеть недоступна"))
    result = classify_comment("что угодно", "SC-042/09", "producer", client)
    assert result.agent_id == "producer"
    assert result.same_task is True
    assert "fallback" in result.reasoning


def test_classify_comment_fallback_on_malformed_json():
    """Новый (по сравнению с Anthropic .parse()) режим отказа: JSON-режим может
    вернуть невалидный/не-по-схеме JSON — тоже должен уходить в fallback, не падать наружу."""
    client = _FakeClient(content="это не JSON вообще")
    result = classify_comment("что угодно", "SC-042/09", "producer", client)
    assert result.agent_id == "producer"
    assert result.same_task is True
    assert "fallback" in result.reasoning


def test_classify_comment_happy_path():
    expected = CommentClassification(agent_id="costume", same_task=False, reasoning="про костюм")
    client = _FakeClient(content=expected.model_dump_json())
    result = classify_comment("а что с пальто?", "SC-042/09", "producer", client)
    assert result.agent_id == "costume"
    assert result.same_task is False


# --- CommentRouter.route ---


def test_route_unknown_task_escalates():
    router = CommentRouter(client=_FakeClient())
    action = router.route(_batch("SC-999/UNKNOWN", "любой текст"))
    assert action.kind == "escalate"


def test_route_empty_batch_escalates():
    router = CommentRouter(client=_FakeClient())
    empty = InboundBatch(
        batch_id="b", clickup_task_id="SC-042/09", events=[],
        window_opened_at=datetime(2026, 8, 13, 9, 0), window_closed_at=datetime(2026, 8, 13, 9, 0),
    )
    action = router.route(empty)
    assert action.kind == "escalate"


def test_route_empty_body_escalates():
    router = CommentRouter(client=_FakeClient())
    action = router.route(_batch("SC-042/09", None))
    assert action.kind == "escalate"


def test_route_same_task_delivers_to_agent():
    classification = CommentClassification(agent_id="producer", same_task=True, reasoning="про деньги")
    router = CommentRouter(client=_FakeClient(content=classification.model_dump_json()))
    action = router.route(_batch("SC-042/09", "Одобряю доплату"))
    assert action.kind == "deliver_to_agent"
    assert action.target_agent_id == "producer"
    assert action.payload["task_id"] == "SC-042/09"
    assert action.payload["thread_ref"] == "c1"
    assert action.payload["comment_text"] == "Одобряю доплату"


def test_route_different_agent_creates_new_ticket():
    # комментарий на костюмной задаче, но реально про камеру/другого агента
    classification = CommentClassification(agent_id="first_ad", same_task=False, reasoning="про график")
    router = CommentRouter(client=_FakeClient(content=classification.model_dump_json()))
    action = router.route(_batch("SC-042/08", "А когда вообще снимаем эту сцену?"))
    assert action.kind == "new_ticket"
    assert action.target_agent_id == "first_ad"
    assert action.payload["source_task_id"] == "SC-042/08"
    assert action.payload["comment_text"] == "А когда вообще снимаем эту сцену?"
