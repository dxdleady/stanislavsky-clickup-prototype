"""Юнит-тесты agents/live_demo_agent.py — без сети (фейковый openai-клиент,
как и в test_comment_router.py) и без реальной паузы (asyncio.sleep замокан)."""
import pytest

from adapters.memory import MemoryTrackerAdapter
from agents.live_demo_agent import (
    AGENT_LABEL,
    REPLY_TEXT,
    _react_new_ticket,
    generate_reply,
    react,
)
from core.events import RouterAction
from core.state_machine import COSTUME_PIPELINE


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
    """Мимикрирует openai.OpenAI ровно настолько, насколько нужно
    generate_reply: .with_options(...).chat.completions.create(...)."""

    def __init__(self, text: str | None = None, raises: Exception | None = None) -> None:
        self.chat = _FakeChat(content=text, raises=raises)

    def with_options(self, **kwargs):
        return self


async def _noop_sleep(*args, **kwargs) -> None:
    return None


# --- generate_reply ---


def test_generate_reply_happy_path():
    client = _FakeClient(text="Беру костюм в переделку, референс уже поменяла.")
    result = generate_reply("costume", "поменяйте, пожалуйста, референс на другой", "SC-042/08", client)
    assert result == f"{AGENT_LABEL['costume']}: Беру костюм в переделку, референс уже поменяла."


def test_generate_reply_fallback_on_exception():
    client = _FakeClient(raises=RuntimeError("сеть недоступна"))
    result = generate_reply("producer", "одобряю доплату", "SC-042/09", client)
    assert result == REPLY_TEXT["producer"]


def test_generate_reply_empty_comment_short_circuits_without_calling_llm():
    client = _FakeClient(raises=AssertionError("generate_reply не должен звать LLM на пустом комментарии"))
    result = generate_reply("first_ad", "", "SC-041/02", client)
    assert result == REPLY_TEXT["first_ad"]


# --- react() / _react_same_task ---


@pytest.mark.asyncio
async def test_react_same_task_uses_generated_reply(monkeypatch):
    monkeypatch.setattr("agents.live_demo_agent.asyncio.sleep", _noop_sleep)
    tracker = MemoryTrackerAdapter(workflow=COSTUME_PIPELINE)
    client = _FakeClient(text="Меняю референс, беру в новую итерацию.")
    action = RouterAction(
        kind="deliver_to_agent",
        target_agent_id="costume",
        payload={
            "task_id": "SC-042/08",
            "thread_ref": "c1",
            "reasoning": "про костюм",
            "comment_text": "поменяйте референс на эталонный",
        },
    )

    await react(action, tracker, client)

    feed = tracker.get_feed("SC-042/08")
    assert len(feed) == 2
    assert feed[0].agent_id == "first_ad"  # диспетчерская реплика первой
    assert feed[1].agent_id == "costume"
    assert feed[1].payload["text"] == f"{AGENT_LABEL['costume']}: Меняю референс, беру в новую итерацию."


@pytest.mark.asyncio
async def test_react_same_task_falls_back_when_llm_fails(monkeypatch):
    monkeypatch.setattr("agents.live_demo_agent.asyncio.sleep", _noop_sleep)
    tracker = MemoryTrackerAdapter(workflow=COSTUME_PIPELINE)
    client = _FakeClient(raises=RuntimeError("таймаут"))
    action = RouterAction(
        kind="deliver_to_agent",
        target_agent_id="producer",
        payload={
            "task_id": "SC-042/09",
            "thread_ref": "c1",
            "reasoning": "про деньги",
            "comment_text": "одобряю доплату",
        },
    )

    await react(action, tracker, client)

    feed = tracker.get_feed("SC-042/09")
    assert feed[1].payload["text"] == REPLY_TEXT["producer"]


# --- _react_new_ticket ---


@pytest.mark.asyncio
async def test_react_new_ticket_builds_linked_task_request(monkeypatch):
    tracker = MemoryTrackerAdapter(workflow=COSTUME_PIPELINE)
    captured: dict = {}
    original_create = tracker.create_linked_task

    def _capture(request):
        captured["request"] = request
        return original_create(request)

    monkeypatch.setattr(tracker, "create_linked_task", _capture)

    action = RouterAction(
        kind="new_ticket",
        target_agent_id="first_ad",
        payload={
            "source_task_id": "SC-042/08",
            "reasoning": "комментарий про график, не про костюм",
            "comment_text": "когда вообще снимаем эту сцену?",
        },
    )

    await _react_new_ticket(action, tracker)

    request = captured["request"]
    assert request.source_task_id == "SC-042/08"
    assert request.assignee_agent_id == "first_ad"
    assert request.comment_text == "когда вообще снимаем эту сцену?"
    assert request.reasoning == "комментарий про график, не про костюм"
    assert request.priority == "high"

    feed = tracker.get_feed("SC-042/08")
    assert len(feed) == 1
    assert feed[0].agent_id == "first_ad"
