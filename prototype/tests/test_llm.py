"""Юнит-тесты core/llm.py — фейковый openai-клиент, без сети. Обёртка не
глотает исключения (в отличие от classify_comment/generate_reply, которые
её оборачивают) — это здесь и проверяется."""
import pytest
from pydantic import BaseModel

from core.llm import complete_structured, complete_text


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
    def __init__(self, content: str | None = None, raises: Exception | None = None) -> None:
        self.chat = _FakeChat(content=content, raises=raises)

    def with_options(self, **kwargs):
        return self


class _Schema(BaseModel):
    agent_id: str
    same_task: bool


def test_complete_text_happy_path():
    client = _FakeClient(content="  привет  ")
    assert complete_text(client, "prompt", timeout=1.0) == "привет"


def test_complete_text_propagates_exceptions():
    client = _FakeClient(raises=RuntimeError("сеть недоступна"))
    with pytest.raises(RuntimeError):
        complete_text(client, "prompt", timeout=1.0)


def test_complete_structured_happy_path():
    client = _FakeClient(content='{"agent_id": "producer", "same_task": true}')
    result = complete_structured(client, "prompt", _Schema, timeout=1.0)
    assert result.agent_id == "producer"
    assert result.same_task is True


def test_complete_structured_malformed_json_propagates():
    client = _FakeClient(content="это не JSON")
    with pytest.raises(Exception):
        complete_structured(client, "prompt", _Schema, timeout=1.0)
