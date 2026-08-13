"""Импорт inbound.webhook создаёт ClickUpTrackerAdapter и Qwen-клиент на уровне
модуля (settings.require("clickup_token")/("qwen_api_key")) — тестам нужны
фиктивные значения ДО этого импорта, иначе он падает ещё на сборе тестов.
`pytest` гарантированно загружает conftest.py раньше любого test_*.py."""
import os

os.environ.setdefault("CLICKUP_TOKEN", "test-token-for-pytest")
os.environ.setdefault("QWEN_API_KEY", "test-key-for-pytest")
