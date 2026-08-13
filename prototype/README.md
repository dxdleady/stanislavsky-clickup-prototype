# Workflow Control Layer — прототип

Интеграция мультиагентной системы (Stanislavsky) с ClickUp: человек видит работу агентов и может вмешаться на любом этапе. Полная документация — в `../docs/`:

| Документ | Что там |
|---|---|
| `docs/00_overview.md` | Нюансная документация — синтез ТЗ/архитектуры/деки, явные допущения |
| `docs/01_architecture_plan.md` | Контракты, data models, sequence-диаграммы, интерфейсы |
| `docs/02_prototype_plan.md` | Порядок сборки по фазам |
| `docs/03_clickup_requirements.md` | Требования к ClickUp API (webhooks, custom fields, auth, лимиты) |
| `docs/04_test_plan.md` | 7 сценариев валидации → assert'ы |
| `docs/05_stack.md` | Стек, включая разбор "нужен ли websocket" (нет) |
| `docs/06_open_questions.md` | Открытые вопросы к пользователю/владельцу Stanislavsky |
| `docs/07_grilled.md` | Самопроверка плана — найденные и исправленные баги логики |
| `docs/08_dataset_analysis.md` | Разбор демо-датасета костюмного цеха — что подтвердил и что поменял в архитектуре |
| `docs/11_live_demo_architecture.md` | Как устроен живой демо-слой поверх реального ClickUp |

## Статус

Прототип собран полностью по `docs/02_prototype_plan.md` — все 7 сценариев ТЗ зелёные на memory-адаптере, плюс живой раунд-трип подтверждён на настоящей ClickUp-доске.

- **`core/`** — контракты и общая логика, ничего не знает про ClickUp/inbound/agents: `events.py` (`AgentEvent`, `TaskState`, `DeliveryResult`), `state_machine.py` (конфигурируемая per-домен `WorkflowConfig`, реализации `CAMERA_PIPELINE`/`COSTUME_PIPELINE`), `filter.py` (безопасный AST-интерпретатор условий, без `eval()`), `outbound_queue.py` (FIFO с ретраями), `clock.py`/`ids.py`, `llm.py` (единая обёртка над Qwen).
- **`adapters/`** — `base.py` (интерфейс `TrackerAdapter`), `memory.py` (in-memory для тестов/sim, `FakeClock`), `clickup.py` (реальный ClickUp REST API v2).
- **`inbound/`** — `router.py` (детерминированный `RuleBasedRouter` по алгоритму `docs/00_overview.md` §4.3), `comment_router.py` (LLM-классификация для живого демо), `batcher.py` (sliding-window 60с + жёсткий потолок 300с), `webhook.py` (FastAPI-приёмник для живого демо).
- **`agents/`** — 4 мок-агента (`mock_actor/camera/continuity/cost.py`), эмитящие `AgentEvent`, плюс `live_demo_agent.py` для реального раунд-трипа.
- **`sim/`** — харнесс сценариев (`system.py`, `runner.py`) и все 7 сценариев ТЗ в `scenarios/s01..s07`.
- **`demo/`** — обвязка живого демо: `seed.py` (наполнение доски), `register_webhook.py`, `discover_ids.py`, `statuses.py`.
- **`tests/`** — 92 теста (юнит + сим-сценарии), сеть — только фейки/моки (`adapters/memory.py`, `core/clock.py::FakeClock`).

## Запуск тестов

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/pytest tests/ -v
```

## Прогон всех сценариев ТЗ (memory-адаптер, без сети)

```bash
.venv/bin/python -m sim.runner
```

## Живой демо на реальном ClickUp

См. `docs/11_live_demo_architecture.md` и `docs/09_clickup_demo_script.md` — нужен `.env` (см. `.env.example`), туннель (cloudflared/ngrok) и ручной safety-протокол для echo-фильтра (единственный ClickUp-аккаунт совпадает с ботом).

## Точки расширения (держать в голове при любом изменении)

- Новый агент → адаптер в `agents/`, отдающий `AgentEvent` — `core/` не меняется.
- Новый трекер (Linear/Jira) → новый класс, реализующий `TrackerAdapter` (`adapters/base.py`) — `core/`, `inbound/` не меняются.
- Новая политика фильтра/роутинга → конфиг `Filter` или новая реализация `Router` за тем же интерфейсом — не рефакторинг вызывающего кода.

Если для очередной фичи требуется трогать `core/` — сигнал, что где-то нарушена изоляция (см. `docs/01_architecture_plan.md` §9, `CLAUDE.md`).
