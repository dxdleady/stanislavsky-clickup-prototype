# Живой демо-слой: архитектура и чек-лист

> Технический план среза, реализованного поверх основного прототипа (`02_prototype_plan.md`) специально под звонок с реальным round-trip через ClickUp. Перенесено из одноразового plan-файла (`gentle-bubbling-hennessy`, одобрен пользователем) в постоянное место. Не заменяет Фазу 6 общего плана прототипа — это параллельный, сознательно упрощённый путь к тому же `adapters/clickup.py`/`inbound/webhook.py`, см. §6 ниже и `02_prototype_plan.md` §"Живой демо-слой".

**Обновление (2026-08-13):** живой демо-слой слит с основной последовательностью фаз `02_prototype_plan.md` (не остаётся отдельным срезом), и LLM-провайдер сменился с Claude Haiku 4.5 на **Qwen** (Alibaba, OpenAI-совместимый эндпоинт `core/llm.py`) — у пользователя нет отдельного `ANTHROPIC_API_KEY` для рантайма. §5.1/5.1-бис/5.5/6/7 ниже обновлены по факту; исходное описание с Claude оставлено местами для истории решения, помечено явно.

## 1. Идея за 30 секунд

Перед звонком `python -m demo.seed` наполняет реальную ClickUp-доску 4 задачами в разных состояниях у разных агентов-хозяев. На звонке — открыть любую из активных, написать комментарий своими словами. В реальном времени:

1. **📋 Первый ассистент** отвечает первым — коротко подтверждает адресата (это и есть роутинг, просто показанный репликой персонажа).
2. **Нужный агент** отвечает по существу (текст реплики сгенерирован по содержанию именно вашего комментария, не одна и та же строка на все случаи — §5.1) и меняет статус карточки.

Это настоящий HTTP round-trip (ClickUp → вебхук → LLM-классификатор → LLM-генерация ответа → ClickUp API), не заготовленная лента и не ручной ввод.

## 2. Задачи на доске

| Задача | Хозяин | Стартовое состояние | Годится для живого комментария |
|---|---|---|---|
| SC-042/07 | 🎨 Художник по костюмам | `Accepted` | Нет — спокойная задача, для контраста |
| SC-042/08 | 🎨 Художник по костюмам (+Continuity) | `Regenerate` | Да |
| SC-042/09 | 💰 Продюсер | `Cost Approval` | Да |
| SC-041/02 | 📋 Первый ассистент | `Blocked` | Да |

Источник этой таблицы в коде — `TASK_OWNERS`/`TASK_STARTING_STAGE` в `inbound/router.py` (canonical, переиспользуется `demo/statuses.py` и `agents/live_demo_agent.py`, не дублируется). **Это и есть точка расширения**: пятая задача/агент — новая строка здесь, без изменений в `webhook.py`.

История комментариев (`SEED_COMMENTS` в `demo/statuses.py`) — реальные тексты из `docs/source/stanislavsky_costume_demo_dataset_v2.json` (события E034/E037, E038-E041, E028/E027/E022, E017), не выдуманы.

## 3. Ручной чек-лист (один раз, ~35-45 мин; перед каждым звонком — только пп. 5-6, ~10-15 мин)

1. Статусы в ClickUp (Settings → статусы списка): `In Generation`, `Regenerate`, `Cost Approval`, `Accepted`, `Blocked`, `Ready for Director` (полный набор из `core/state_machine.py::COSTUME_PIPELINE`, не только 4).
2. Отдельный ClickUp-аккаунт для бота — **Member, не Guest** (см. `03_clickup_requirements.md` §9 — Guest, возможно, не может выпустить Personal API Token). Если отдельного аккаунта нет (как в текущей демо-настройке) — echo-фильтр (`BOT_USER_ID`) не сможет отличить живой комментарий человека от ответа бота, см. известное ограничение в конце этого раздела.
3. `QWEN_API_KEY` (+ опционально `QWEN_BASE_URL`/`QWEN_MODEL`) для классификатора и генератора ответа — **не** `ANTHROPIC_API_KEY` (провайдер сменился, см. §5.1).
4. `prototype/.env` заполнен по `prototype/.env.example` (`CLICKUP_TOKEN`, `CLICKUP_LIST_ID`, `CLICKUP_TEAM_ID`, `BOT_USER_ID`, `QWEN_API_KEY`). `CLICKUP_TEAM_ID`/`BOT_USER_ID` — через `python -m demo.discover_ids` (нужен только `CLICKUP_TOKEN`).
5. **Сначала** `python -m demo.seed` — **потом** `uvicorn inbound.webhook:app --port 8000` (сервер читает `demo/task_map.json` один раз при старте, не перечитывает; обратный порядок — сервер работает со старым/пустым task_map весь звонок, см. `09_clickup_demo_script.md`).
6. `ngrok http 8000` → `python -m demo.register_webhook <ngrok-url>`, подписка только на `taskCommentPosted`, сохранить `secret` в `.env` (`CLICKUP_WEBHOOK_SECRET`), если решите включить проверку подписи (§5.5).
7. Написать один живой комментарий → проверить обе реакции и смену статуса.

**Известное ограничение (найдено 2026-08-13, живой прогон):** без отдельного бот-аккаунта `BOT_USER_ID` совпадает с аккаунтом человека, который пишет комментарии — echo-фильтр (§5.1 `00_overview.md`) в этом случае отфильтрует **все** комментарии с этого аккаунта, включая настоящие человеческие, не только эхо бота. Единственный протестированный обходной путь — временно оставить `BOT_USER_ID` пустым на время ОДНОГО контролируемого прогона и сразу остановить сервер после получения ответа (окно батчера, 60с, даёт запас на реакцию до того, как собственные реплики бота могут пойти на новый круг). Не оставлять `BOT_USER_ID` пустым на весь звонок без присмотра.

## 4. Поток данных

```
Живой комментарий на, например, SC-042/09
        │
        ▼
ClickUp webhook (taskCommentPosted) → POST /webhook/clickup
        │  (thin handler — отвечает 200 сразу, вся обработка в фоне,
        │   docs/03_clickup_requirements.md §2.3: >7с = suspend)
        ▼
_extract_inbound_event(): реальный ClickUp task_id → метка ("SC-042/09")
        │  через task_map.json (demo/seed.py пишет его при создании задач)
        ▼
Batcher.add() → RuleBasedRouter.route() → classify_comment()
        │  Claude Haiku 4.5, structured output, таймаут 3.5с + fallback
        ▼
RouterAction (deliver_to_agent | new_ticket | escalate)
        │
        ▼
agents/live_demo_agent.react():
   1) "Первый ассистент" публикует AgentEvent (дублирует текущую стадию)
   2) пауза 2.5с (демо-эффект — "агент реально что-то делает")
   3) generate_reply(): Claude Haiku 4.5 сочиняет реплику по тексту
      комментария (таймаут 3.5с + fallback на REPLY_TEXT[agent_id])
   4) нужный агент публикует AgentEvent с этой репликой (новая стадия,
      если переход валиден)
        │
        ▼
ClickUpTrackerAdapter.publish(): post_comment/post_reply + set_status
   (метка → реальный id только здесь, через _resolve())

Если classify_comment решил same_task=false — вместо шагов 3-4:
   create_linked_task(LinkedTaskRequest(...)) — новая задача с исходным
   текстом комментария и reasoning классификатора внутри (§5.1, §5.2).
```

## 5. Ключевые архитектурные решения

### 5.1 Классификация — реальное понимание, не роутинг по task_id

- **Модель:** Qwen `qwen3.6-flash` (`core/llm.py`, эндпоинт — OpenAI-совместимый Model Studio workspace) — закрытая классификация из 3 известных агентов, не открытый вопрос; быстрая модель здесь так же надёжна, как крупная, а на звонке важна скорость. **Было** Claude Haiku 4.5 (Anthropic) — сменилось не по техническим причинам, а по доступу: у пользователя нет отдельного `ANTHROPIC_API_KEY` для этого рантайма.
- **Структурированный вывод:** `core/llm.py::complete_structured` — JSON-режим (`response_format={"type":"json_object"}`) + валидация схемой `CommentClassification` (`agent_id`, `same_task`, `reasoning`) на своей стороне, не строгий server-side structured-output (Anthropic-специфичное `.messages.parse`/OpenAI-специфичное `.beta...parse` — неизвестно, поддерживает ли DashScope compatible-mode такое расширение). # ASSUMPTION(open_questions:В-qwen-json-mode) — см. `06_open_questions.md`.
- **Реальный контекст:** текст комментария + текущий хозяин задачи + `AGENT_ROSTER` (зоны ответственности всех трёх агентов).
- **Таймаут + fallback:** любое исключение (сеть, таймаут, отказ модели, невалидный/не-по-схеме JSON — новый режим отказа под JSON-режим, которого не было у Anthropic `.parse()`) → `classify_comment` возвращает `CommentClassification(agent_id=default_owner, same_task=True, reasoning="fallback после сбоя классификатора: ...")`. Демо никогда не виснет из-за сетевого сбоя (см. `test_comment_router.py::test_classify_comment_fallback_on_exception`, `::test_classify_comment_fallback_on_malformed_json`).
- **Если комментарий реально адресован другому агенту** (`same_task=False`) — не ответ в этой же задаче, а `tracker.create_linked_task(...)`: новая задача с нужным хозяином, ссылкой на исходную (контракт — §5.2).

### 5.1-бис Генерация ответа — тоже реальное понимание, не только маршрутизация

Изначально текст реплики нужного агента был фиксированной строкой на агента (`REPLY_TEXT`) — классификатор реально понимал, **кому** адресован комментарий, но агент отвечал одинаково независимо от того, **что** там написано. Пользователь заметил этот разрыв в разговоре (не в исходном плане) — тот же принцип "не имитировать понимание", который уже применён к роутингу (§5.1), должен применяться и к ответу.

`agents/live_demo_agent.py::generate_reply()` — второй реальный вызов LLM (та же модель, тот же таймаут-паттерн):
- Промпт: текст комментария человека + task_id + персона агента (`AGENT_LABEL`), просьба ответить 1-2 короткими деловыми предложениями, явный запрет придумывать факты (даты/суммы/имена), которых нет в комментарии.
- Пустой `comment_text` (не должно случаться в норме, но защита есть) — короткое замыкание без вызова LLM, сразу `REPLY_TEXT[agent_id]`.
- Тот же паттерн надёжности, что у `classify_comment`: таймаут 3.5с + безусловный fallback на `REPLY_TEXT[agent_id]` при любом исключении — вторая живая LLM-генерация на комментарий не увеличивает риск зависания демо, только суммарную задержку в пределах того же порядка (секунды, не десятки секунд).

### 5.2 Контракт на создание связанной задачи — `LinkedTaskRequest`

До этой правки `create_linked_task(source_task_id, priority, assignee_agent_id)` не передавал исходный текст комментария внутрь новой задачи — принимающий агент видел только факт "что-то было адресовано не туда", без содержания. Пользователь прямо запросил "определённый контракт на создание задачи, чтобы агент правильно всё понял".

`core/events.py::LinkedTaskRequest` (pydantic-модель, не россыпь позиционных параметров — тот же принцип, что и `AgentEvent`, для данных, пересекающих границу `agents/*` → `adapters/*`):

```python
class LinkedTaskRequest(BaseModel):
    source_task_id: str
    assignee_agent_id: str
    priority: Literal["low", "normal", "high", "urgent"] = "normal"
    comment_text: str
    reasoning: str
```

`TrackerAdapter.create_linked_task(request: LinkedTaskRequest) -> str` — протокол (`adapters/base.py`) и обе реализации (`adapters/clickup.py`, `adapters/memory.py`) обновлены синхронно. В ClickUp-реализации `comment_text`/`reasoning` идут прямо в описание новой задачи (дословная цитата комментария + объяснение классификатора, зачем задача создана) — не только заголовок с номером исходной задачи, как было раньше.

`RouterAction.payload` для обеих веток (`deliver_to_agent` и `new_ticket`) теперь всегда несёт `comment_text` (`inbound/comment_router.py`) — источник и для `generate_reply()`, и для `LinkedTaskRequest`.

### 5.3 Метка vs реальный ClickUp id — где проходит граница

`TASK_OWNERS`, `TASK_STARTING_STAGE`, `AgentEvent.task_id`, `RouterAction.payload["task_id"]` — везде человекочитаемая метка (`"SC-042/09"`). Реальный ClickUp id известен только после `demo/seed.py` создаёт задачу. Перевод — **только внутри `ClickUpTrackerAdapter._resolve()`**, через `label_to_real_id`, загруженный из `demo/task_map.json`. `router.py`/`live_demo_agent.py` о реальных id не знают вообще — та же граница, что и в остальной архитектуре ("адаптер — единственное место, знающее про конкретный трекер").

`inbound/webhook.py` строит и обратный словарь (`real_id → label`) — реальный ClickUp `task_id` из вебхук-payload переводится в метку сразу на входе (`_extract_inbound_event`), дальше по пайплайну снова только метки.

### 5.4 Статусы — через `WorkflowConfig`, не хардкод

Внутренние id стадий (`"in_generation"`) переводятся в отображаемое имя ClickUp (`"In Generation"`) через `self._workflow.stages[status].name` внутри `ClickUpTrackerAdapter` — `WorkflowConfig` инжектится (сейчас `COSTUME_PIPELINE`), адаптер не завязан на конкретный домен. Переходы, которые предлагает `live_demo_agent.py`, проверяются через `COSTUME_PIPELINE.is_valid_transition(...)` — если переход недопустим, стадия остаётся прежней (лог-warning, не падение).

### 5.5 Обновление (2026-08-13): батчинг и очередь теперь настоящие

Раньше здесь было написано, что батчинг и очередь с ретраями сознательно не построены — оба теперь есть, слиты из основного плана:

- **Батчинг** — `inbound/batcher.py::Batcher`, настоящее скользящее окно 60с (продлевается каждым новым комментарием) + жёсткий потолок 300с. Честная поправка к тому, что было написано здесь: обещание "тот же вызовный контракт, сигнатура не изменится" **не выполнено буквально** — `Batcher.add()` больше не возвращает готовый батч синхронно (это физически несовместимо со sliding window — окно должно закрываться и без нового события). Добавлен `flush_due()`, вызываемый фоновым `asyncio`-тиком в `webhook.py` (`_flush_loop`, каждые 5с). Меняет UX живого демо: ответ агента больше не приходит мгновенно, у него теперь окно — такое же, как у сценария 04.
- **Очередь с ретраями** — `core/outbound_queue.py::OutboundQueue`, глобальный FIFO, используется `sim/system.py` для сценария 07. В `webhook.py`/`live_demo_agent.py` пока не подключена (публикация там всё ещё прямая через `ClickUpTrackerAdapter.publish`) — подключение туда осталось бы отдельным шагом, не блокирующим демо.
- **Проверка HMAC-подписи по умолчанию всё ещё выключена** (`REQUIRE_WEBHOOK_SIGNATURE=false` в `.env`) — риск сломать демо-стенд перед звонком по-прежнему выше риска подделки запроса на коротком тестовом прогоне.

## 6. Расширяемость — почему это не одноразовый хак

- Реакция каждого агента — настоящий `AgentEvent` (`agents/live_demo_agent.py::_event`), проходит через тот же `ClickUpTrackerAdapter.publish()`, что и остальная архитектура — не отдельный вызов ClickUp API в обход контракта.
- **Техдолг закрыт (2026-08-13):** класс, который классифицирует через LLM, теперь называется `CommentRouter` и живёт в отдельном файле `inbound/comment_router.py` — не `RuleBasedRouter` в `inbound/router.py`. Это имя теперь принадлежит настоящему детерминированному Router'у (`docs/00_overview.md` §4.3), общему для `sim/`/приёмочных сценариев.
- Таблица `TASK_OWNERS` (`inbound/comment_router.py`) — единственное место, куда добавляется 5-я/6-я задача с новым агентом; `webhook.py`/`live_demo_agent.py` не требуют изменений (только `AGENT_ROSTER`/`AGENT_LABEL`/`REPLY_TEXT`/`STATUS_AFTER` — тоже табличные расширения, с assert'ом в `live_demo_agent.py`, что ростеры не разъехались).
- Новый трекер (не ClickUp) — через тот же `TrackerAdapter`-протокол (`adapters/base.py`), уже проверено вторым доменом (camera/costume) на уровне `core/`.

## 7. Файловая карта

| Файл | Роль |
|---|---|
| `core/config.py` | `.env`-конфиг, ленивая валидация (`settings.require(...)`) |
| `core/llm.py` | Обёртка над Qwen (OpenAI-совместимый клиент) — единственное место конструирования LLM-клиента |
| `core/filter.py` | `Filter`/`StageTracker` — publish/drop, безопасный AST-интерпретатор условий |
| `core/outbound_queue.py` | `OutboundQueue` — глобальный FIFO retry (сценарий 07) |
| `adapters/clickup.py` | Реальный `TrackerAdapter` + demo-утилиты (create/delete/list task, webhook registration) |
| `inbound/router.py` | `RuleBasedRouter` — настоящий детерминированный Router, без LLM |
| `inbound/comment_router.py` | `CommentRouter` (LLM-классификация, Qwen), `TASK_OWNERS`/`TASK_STARTING_STAGE`/`AGENT_ROSTER` — источник истины для живого демо |
| `inbound/batcher.py` | `Batcher` — реальное скользящее окно 60с + потолок 300с |
| `inbound/webhook.py` | FastAPI-эндпоинт, тонкий хендлер + фоновая обработка + `_flush_loop` |
| `agents/live_demo_agent.py` | Реакция агентов через `AgentEvent`; `generate_reply()` — генерация текста ответа по содержанию комментария (Qwen) |
| `sim/system.py` | Тестовый харнес (Queue+Filter+Router+Batcher на `memory.py`) для всех 7 сценариев ТЗ |
| `demo/statuses.py` | `SEED_COMMENTS`, `ACTIVE_TASK_LABELS` |
| `demo/seed.py` | Идемпотентный сид доски (сносит `[DEMO]`-задачи, создаёт заново) |
| `demo/discover_ids.py` | Печатает `BOT_USER_ID`/`CLICKUP_TEAM_ID` для `.env` |
| `demo/register_webhook.py` | Регистрирует вебхук на `taskCommentPosted`, печатает `secret` |

## 8. Тесты и проверка

- `pytest prototype/` — все тесты зелёные (юнит + 7 сценариев ТЗ через `tests/test_scenarios.py`), без сети (LLM и ClickUp — фейки/моки: `_FakeClient` в `test_comment_router.py`/`test_live_demo_agent.py`/`test_llm.py` мимикрирует `openai.OpenAI`, не `anthropic.Anthropic`; чистые функции в `test_webhook_extraction.py`; `asyncio.sleep` замокан в тестах на `_react_same_task`, чтобы не ждать реальные 2.5с паузы).
- `python -m sim.runner` (он же `make demo`) — все 7 сценариев ТЗ зелёные на `memory.py`, независимо от живого ClickUp-пути.
- Хук на обновление документации (`.claude/settings.json`, Stop-хук) — проверяет `git status --porcelain --untracked-files=all` на `prototype/**/*.py` vs `docs/**/*.md`; блокирует завершение ответа, если ≥3 незакоммиченных `.py`-файла без единой правки в `docs/`.
- Живой прогон — требует ручного чек-листа (§3), не может быть выполнен автономно; минимум 2 раза подряд перед звонком: `demo.seed` → живой комментарий → обе реакции + смена статуса.

## 9. Открытый пункт

Точная форма JSON-payload вебхука ClickUp для `taskCommentPosted` (имя поля с текстом комментария, точное место `task_id`) не подтверждена на 100% — см. `06_open_questions.md` В7. `webhook.py` логирует сырое тело безусловно и пробует несколько вариантов полей — это осознанная стратегия смягчения, не забытый TODO; поправить `_extract_comment_text`/`_extract_inbound_event` по факту первого реального события.
