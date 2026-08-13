# План тестов: от сценариев ТЗ к assert'ам

> ТЗ §4 уже даёт 7 сценариев и "что проверяем" в одно предложение. Здесь — конкретные assert'ы и pytest-псевдокод, которые из этого предложения следуют. Все примеры используют `memory.py` + `FakeClock` (см. `01_architecture_plan.md` §5) — никакого реального времени и никакого токена ClickUp.

**Статус (2026-08-13): реализовано, `sim/system.py::System`/`build_system` — рабочий харнес, не только псевдокод.** Несколько мест ниже — иллюстрация, не буквальный рабочий контракт; расхождения и как они разрешены в реальном коде:
- `system.mock_actor.call_count` — считает `sim/system.py::System`, не сам `agents/mock_actor.py` (мок остаётся stateless).
- Сценарий 06: статус "`blocked_pending_approval`" не существует ни в одном `WorkflowConfig` — используется настоящая стадия `COSTUME_PIPELINE.cost_approval`; `tracker.get_linked_payment_workflow(...)` → `tracker.get_linked_workflow_task(task_id, "payment_approval")` (реальный метод, `workflow_kind`-параметризованный, не отдельный под каждый тип связи).
- Имена файлов сценариев — `s01_happy_path.py` … `s07_clickup_outage.py` (префикс `s`, не голая цифра — `01_x.py` не импортируется в Python).

## Общая инфраструктура тестов

```python
# tests/conftest.py
@pytest.fixture
def clock():
    return FakeClock(start=datetime(2026, 8, 12, 9, 0))

@pytest.fixture
def tracker():
    return MemoryTrackerAdapter()

@pytest.fixture
def system(clock, tracker):
    """Собранная система: queue + filter + router + batcher, все на memory/fake."""
    return build_system(tracker=tracker, clock=clock, filter_config=DEFAULT_FILTER_CONFIG)
```

## 01 — Happy path

**Проверяем:** Бэклог → Done без вмешательств; все смены стадий видны в ленте.

```python
def test_happy_path(system, tracker):
    task = system.seed_backlog_task(task_id="SC-042/SHOT-07", agent="actor.disciple_ivanov")
    system.run_agent_to_completion(task.task_id)

    feed = tracker.get_feed(task.task_id)
    stages_seen = [e.stage for e in feed if e.kind == "report"]
    assert stages_seen == ["backlog", "planned", "in_progress",
                            "ready_for_verification", "verification", "done"]
    assert tracker.get_status(task.task_id) == "done"
    # ни один InboundBatch не создан — вмешательств не было
    assert system.router.calls == []
```

## 02 — Правка текущему агенту

**Проверяем:** реплика доставлена в контекст, агент ответил в треде, задача обновлена.

```python
def test_reply_to_current_agent(system, tracker):
    task = system.seed_in_progress_task(task_id="SC-042/SHOT-07", agent="camera.ivanov")

    comment_id = tracker.simulate_human_comment(task.task_id, "убери контровой", author="human.producer")
    system.flush_inbound()  # обрабатывает всё, что накопилось (без реального ожидания окна)

    action = system.router.last_action
    assert action.kind == "deliver_to_agent"
    assert action.target_agent_id == "camera.ivanov"

    reply = tracker.get_replies(comment_id)
    assert len(reply) == 1
    assert reply[0].thread_ref == comment_id          # ответ в ТОМ ЖЕ треде
    assert tracker.get_status(task.task_id) in {"in_progress"}  # новая итерация, не скачок стадии
```

## 03 — Правка агенту прошлой итерации

**Проверяем:** новый тикет создан, приоритет назначен, ссылка на исходный, старая задача не мутирована.

```python
def test_reply_to_previous_iteration(system, tracker):
    old_task = system.seed_done_task(task_id="SC-039/SHOT-03", agent="camera.ivanov")

    tracker.simulate_human_comment(old_task.task_id, "тут пальто было не то", author="human.producer")
    system.flush_inbound()

    action = system.router.last_action
    assert action.kind == "new_ticket"

    new_task_id = action.payload["created_task_id"]
    new_task = tracker.get_task(new_task_id)
    assert new_task.priority == "high"
    assert new_task.linked_task_id == old_task.task_id
    assert new_task.assignee_agent_id == "camera.ivanov"   # prev_agent_id исходной задачи

    # старая задача НЕ переоткрыта
    assert tracker.get_status(old_task.task_id) == "done"
```

## 04 — Пачка комментариев (подряд и с паузой)

**Проверяем:** подряд — объединены в одно действие; после паузы — новое окно. Плюс потолок батча (допущение из `00_overview.md` §4.4).

```python
def test_batch_consecutive_comments_merged(system, tracker, clock):
    task = system.seed_in_progress_task("SC-042/SHOT-07", agent="camera.ivanov")

    tracker.simulate_human_comment(task.task_id, "1", author="human.producer")
    clock.advance(10)
    tracker.simulate_human_comment(task.task_id, "2", author="human.producer")
    clock.advance(10)
    tracker.simulate_human_comment(task.task_id, "3", author="human.producer")
    clock.advance(65)   # пауза > 60с — окно должно закрыться
    system.flush_inbound()

    assert len(system.router.actions) == 1                 # ОДНО согласованное действие
    assert len(system.router.actions[0].batch.events) == 3  # все три реплики внутри

def test_batch_pause_opens_new_window(system, tracker, clock):
    task = system.seed_in_progress_task("SC-042/SHOT-07", agent="camera.ivanov")

    tracker.simulate_human_comment(task.task_id, "1", author="human.producer")
    clock.advance(65)  # пауза
    tracker.simulate_human_comment(task.task_id, "2", author="human.producer")
    clock.advance(65)
    system.flush_inbound()

    assert len(system.router.actions) == 2   # два независимых батча/действия

def test_batch_hard_ceiling(system, tracker, clock):
    """Допущение прототипа: потолок 300с, если человек комментирует чаще раза в минуту бесконечно."""
    task = system.seed_in_progress_task("SC-042/SHOT-07", agent="camera.ivanov")
    for i in range(10):
        tracker.simulate_human_comment(task.task_id, str(i), author="human.producer")
        clock.advance(30)   # каждый раз внутри окна — sliding window никогда бы не закрылось само
    system.flush_inbound()

    assert len(system.router.actions) >= 1   # потолок принудительно закрыл батч раньше, чем через 10*30=300с непрерывного молчания
```

## 05 — Рандомный коммент вне контекста

**Проверяем:** производство не тронуто, реплика залогирована и эскалирована.

```python
def test_out_of_context_noise(system, tracker):
    task = system.seed_in_progress_task("SC-042/SHOT-07", agent="camera.ivanov")
    state_before = tracker.snapshot(task.task_id)

    tracker.simulate_human_comment(task.task_id, "а что с обедом сегодня", author="human.producer")
    system.flush_inbound()

    action = system.router.last_action
    assert action.kind == "escalate"
    assert tracker.snapshot(task.task_id) == state_before   # НИЧЕГО в производстве не изменилось
    assert len(system.escalation_log.entries) == 1
```

*Нюанс для реализации (реализовано 2026-08-13, формулировка ужесточена по факту):* под настоящим детерминированным роутером (`inbound/router.py::RuleBasedRouter`) "тема комментария нерелевантна" не является структурным сигналом вообще — реплика без @-упоминания на активной задаче с известным текущим агентом ВСЕГДА резолвится в `deliver_to_agent`, что бы в ней ни было написано (см. `docs/07_grilled.md` находка №7). Единственный детерминированный способ гарантировать `escalate` в тесте — структурная, не смысловая причина: **@-упоминание agent_id, которого нет среди `known_agent_ids` задачи** (см. `sim/scenarios/s05_out_of_context_noise.py`), либо комментарий на неизвестном `task_id`. Прежняя формулировка ("реплика, которая точно не матчится ни под одно правило") была слишком мягкой и допускала хрупкий тест, построенный на теме — исправлено здесь на конкретный рецепт.

## 06 — Превышение порога стоимости

**Проверяем:** генерация заблокирована, payment-workflow создан, повторный approve не запускает дубль.

```python
def test_cost_threshold_blocks_and_approve_unblocks(system, tracker):
    task = system.seed_planned_task("SC-050/SHOT-01", agent="cost.estimator")
    system.mock_cost_estimate(task.task_id, gpu_minutes=42, cost_usd=18.0)  # > $15 дефолтный порог

    system.run_agent_step(task.task_id)   # Cost-агент шлёт decision_request

    assert tracker.get_status(task.task_id) == "blocked_pending_approval"
    payment_task_id = tracker.get_linked_payment_workflow(task.task_id)
    assert payment_task_id is not None

    generation_calls_before = system.mock_actor.call_count

    tracker.simulate_status_change(payment_task_id, "Approved", author="human.timur")
    system.flush_inbound()
    assert system.mock_actor.call_count == generation_calls_before + 1

    # повторный approve (двойной клик / повторная доставка webhook)
    tracker.simulate_status_change(payment_task_id, "Approved", author="human.timur")
    system.flush_inbound()
    assert system.mock_actor.call_count == generation_calls_before + 1   # НЕ +2
```

## 07 — Недоступность ClickUp

**Проверяем:** очередь копится, после восстановления досылается в исходном порядке.

```python
def test_clickup_outage_queue_and_replay(system, tracker):
    tracker.simulate_outage(True)

    events_sent = []
    for i in range(5):
        e = system.emit_agent_event(task_id=f"SC-0{i}/SHOT-01", kind="report")
        events_sent.append(e.correlation_id)

    assert tracker.get_feed_all() == []               # ничего не доставлено
    assert system.outbound_queue.pending_count() == 5   # производство не блокируется — очередь копится

    tracker.simulate_outage(False)
    system.flush_outbound()

    delivered_order = [e.correlation_id for e in tracker.get_delivery_log()]
    assert delivered_order == events_sent   # порядок доставки == порядку постановки
```

## Матрица покрытия (что тестируем ПОМИМО happy-path сценариев ТЗ)

Семь сценариев ТЗ — приёмочные (acceptance), не заменяют юнит-тесты на границы:

| Модуль | Юнит-тесты сверх сценариев |
|---|---|
| `state_machine.py` | недопустимый переход (например `Backlog → Done` напрямую) кидает ошибку; `Deferred` из каждой стадии возвращает в исходную |
| `filter.py` | каждое правило YAML по отдельности; `default_action: drop` для событий, не подпадающих ни под одно правило |
| `router.py` (echo-фильтр) | `00_overview.md` §5.1: событие от `*.agents.stanislavsky.ai` не долетает до Router вообще (тест на уровне `webhook.py`, не Router) |
| `batcher.py` | пустой батч не создаётся; единственный комментарий без последующих тоже закрывает окно и создаёт батч из одного элемента |
| `ids.py` | `event_seq` монотонен и не переиспользуется при параллельных агентах на одном task_id |

## Критерий сдачи (совпадает с ТЗ §4, зафиксировано здесь как runbook)

```bash
make demo   # прогоняет sim/scenarios/01..07 на memory-адаптере, печатает нарратив, exit code 0 = все зелёные
pytest prototype/ -q   # юнит + сценарии как обычные pytest-тесты (sim/scenarios импортируются в tests/test_scenarios.py)
```
