# Требования к интеграции с ClickUp

> Основано на прямом ресёрче актуальной (август 2026) документации ClickUp API: `developer.clickup.com/docs/webhooks`, `.../connect-an-ai-assistant-to-clickups-mcp-server`, `.../reference/getaccesstoken` (ссылки прислал пользователь) + custom fields, comments, statuses, rate limits, chat, биллинг. Полный список источников — в конце документа.

## 0. Главный вывод: MCP не используется, только прямой REST API

ClickUp MCP-сервер (ссылка, которую прислал пользователь) — публичная бета для AI-ассистентов (Claude/ChatGPT) с доступом к задачам/докам через естественный язык. **Не подходит для нашего контура**:
- Аутентификация MCP — **только OAuth**, документация прямо пишет: "you cannot authenticate using your own API keys or Auth access tokens". OAuth здесь требует интерактивного согласия человека — несовместимо с headless сервер-серверной интеграцией.
- Лимиты без AI-аддона — 50–300 вызовов/24ч. На порядки меньше потока от 7 агентов.

**Решение:** только прямой ClickUp REST API v2 (`api.clickup.com/api/v2`), MCP не рассматривается для production outbound/inbound контура. Это подтверждает (не меняет) то, что уже было в `02_architecture.md` — там сразу фигурировал `clickup.py` как прямой адаптер, не MCP-клиент.

## 1. Аутентификация: Personal API Token, не OAuth

| | OAuth2 | Personal API Token |
|---|---|---|
| Подходит для | multi-tenant SaaS с произвольными внешними пользователями | один известный воркспейс, известные заранее identity — **наш случай** |
| Требует интерактивности | Да (redirect + consent) | Нет, генерируется один раз в UI |
| Экспирация | не истекает (subject to change) | не истекает |

**Решение:** каждый агент (или единая интеграционная identity — см. §7, риск) получает **Personal API Token** (`pk_...`), сгенерированный вручную в Settings → Apps → API Token. Заголовок запроса: `Authorization: {token}` (без `Bearer`). Хранение — в секретнице сервиса (env/vault), не в коде и не в `docs/`.

## 2. Webhooks (inbound) — операционные требования, не только "что доступно"

### 2.1 Регистрация
`POST /team/{team_id}/webhook`, тело: `endpoint` (HTTPS URL), `events` (список либо `"*"`), опционально ровно один location-фильтр (`space_id`/`folder_id`/`list_id`/`task_id`). **Решение:** не фильтровать по location — подписываться на уровне всего team/workspace, чтобы не переносить логику фильтрации задач кинопроекта на уровень регистрации вебхука (она и так есть в нашем `Filter`/`Router`).

Ответ содержит `secret` — возвращается **один раз**, обязателен для проверки подписи (§2.4). Сохранить сразу при регистрации.

### 2.2 События, на которые подписываемся
`taskCreated`, `taskUpdated` (тело содержит `history_items`, включая custom field изменения — **отдельного события на custom field нет**, фильтровать по `history_items[].field == "custom_field"`), `taskStatusUpdated`, `taskCommentPosted`, `taskCommentUpdated`, `taskAssigneeUpdated`, `taskPriorityUpdated`.

**Нюанс, критичный для Router:** статус в payload передаётся **строкой-именем**, не стабильным ID (`before`/`after` = `{status, color, orderindex, type}`, `type ∈ open|custom|closed`). Переименование статуса в UI ClickUp **тихо ломает** маппинг Stage↔ClickUp-статус без ошибки API. **Требование к реализации:** `Router`/`clickup.py` должны логировать (и в идеале эскалировать) любой незнакомый `status` строку вместо падения — а не просто KeyError.

### 2.3 Обязательное требование к `inbound/webhook.py`: быстрый ответ + очередь

ClickUp-специфичная надёжность, отсутствующая в исходном ТЗ, но **обязательная**:
- Таймаут ответа нашего эндпоинта **> 7 секунд** = fail попытки, как и любой non-2xx.
- До **5 ретраев** на событие со стороны ClickUp, затем событие теряется безвозвратно (это не durable-очередь на их стороне).
- **HTTP 401 от нас мгновенно переводит вебхук в `suspended`** (не постепенная деградация) — доставка полностью прекращается до ручной реактивации (`PUT /webhook/{id}`).
- При `fail_count` = 100 (накопительно) — тоже `suspended`.

**Прямое следствие для архитектуры (обновляет `01_architecture_plan.md` §1):** `inbound/webhook.py` обязан быть **тонким** — провалидировать подпись, положить сырое тело в `InboundEvent`-очередь и немедленно вернуть 200. Вся обработка (Batcher, Router) — асинхронно, вне HTTP-хендлера. Синхронная обработка внутри вебхук-хендлера — это то, что может тихо убить интеграцию на демо (suspend вебхука посреди презентации, если что-то на секунду подвиснет).

### 2.4 Верификация подлинности
Заголовок `X-Signature` = `hex(HMAC-SHA256(raw_body, secret))`. **Требование:** `webhook.py` пересчитывает HMAC от **сырого** тела запроса (до парсинга JSON) и сравнивает константным по времени сравнением (`hmac.compare_digest`), не `==`. Это отдельный уровень защиты от "фильтра по автору" (§6) — подпись подтверждает "запрос реально от ClickUp", а не "автор реплики — человек, не агент".

### 2.5 Лимиты вебхуков
Явного числового лимита "вебхуков на team" в документации не нашли (пункт для эксперимента, см. `06_open_questions.md`). Для прототипа это не блокер — регистрируем один webhook на весь team.

## 3. Custom Fields — важное операционное ограничение

**Создать custom field через публичный API нельзя** — только вручную в UI (Space/Folder/List settings). API умеет только читать существующие определения и писать значения.

**Прямое следствие:** перед фазой 6 плана прототипа (`02_prototype_plan.md`) должен быть **ручной шаг настройки** ClickUp Space: создать нужные custom fields руками. Это не разработческая задача, а **оперативная**, но она в критическом пути — без неё `clickup.py` не заработает, сколько бы кода ни было написано.

Маппинг AgentEvent → Custom Fields:

| Поле AgentEvent | Custom Field ClickUp | Тип | Примечание |
|---|---|---|---|
| `agent_id` | `Agent` | `drop_down` | Закрытый список из 7 (+cost) агентов; новый агент = ручное добавление опции в UI |
| `cost_usd` | `Cost (USD)` | `currency` | — |
| `correlation_id` | `Correlation ID` | `text` | Точный string match, для дебага/аудита, не для идемпотентности в коде (та — в памяти/сторадже адаптера, не в ClickUp) |
| `deadline` (decision_request) | `Decision Deadline` | `date` | Отдельно от нативного `due_date` задачи — due_date это про сдачу шота, не про "успеть ответить агенту" |
| `version` | `Artifact Version` | `text` | — |

**stage не дублируется отдельным custom field** — используем нативный ClickUp Status (Backlog/Planned/…/Done/Deferred), поскольку это то, что рендерится доской "бесплатно" (deck §7 — "штатными средствами ClickUp"). Дублирование в custom field добавило бы источник рассинхрона без пользы.

**Установка значения:** `POST /task/{task_id}/field/{field_id}` с `{"value": ...}`, формат зависит от типа (dropdown — UUID опции, не строка; date — unix ms). **Нет batch-обновления** — каждое поле = отдельный HTTP-вызов. Один AgentEvent с 4 заполненными полями → минимум 5 запросов (1 создание/обновление задачи + 4 поля) + возможный вызов для комментария = до 6 запросов на событие. **Учитывать в оценке нагрузки на rate limit** (§5).

## 4. Task Statuses — тоже ручная настройка

Как и custom fields, **кастомные статусы нельзя создать через API** (подтверждено структурой `Create Space` endpoint — нет поля `statuses` в теле запроса). Настройка стадий `Backlog → Planned → In Progress → Ready for Verification → Verification → Done` + `Deferred` — ручная операция в UI один раз при разворачивании прототипа, до фазы 6.

**Риск:** ClickUp "Status templates" не гарантируют синхронизацию между List — после первого расхождения статус живёт отдельно как "Custom". Если проект расширяется на несколько List/Space (несколько кинопроектов параллельно), нужно **вручную** поддерживать идентичность набора статусов, либо `Router` должен быть терпим к дрейфу (см. §2.2).

## 5. Rate Limits и стратегия троттлинга

| Тариф | Лимит |
|---|---|
| Free / Unlimited / Business | 100 req/min |
| **Business Plus** (целевой, по деке) | **1000 req/min** |
| Enterprise | 10 000 req/min |

Лимит — **per token**, не per workspace (не подтверждено официально, есть ли доп. воркспейс-wide потолок сверху — см. `06_open_questions.md`). При 429 — заголовки `X-RateLimit-Remaining`/`X-RateLimit-Reset` (unix timestamp), `Retry-After` не документирован.

**Требование к `adapters/clickup.py`:** проактивный троттлинг по `X-RateLimit-Remaining` (не дожидаться 429), плюс экспоненциальный backoff с джиттером как fallback на сетевые 5xx/таймауты. Это прямое продолжение "очереди с подтверждением" из архитектуры — worker очереди должен уметь притормозить сам, не полагаясь только на ретраи после отказа.

## 6. Comments API — создание, треды, упоминания

### 6.1 Базовое создание
`POST /task/{task_id}/comment`, поле `comment_text` (простой текст, **без поддержки @-упоминаний**) либо `comment` (rich-массив, нужен для форматирования/тегов). Ответ `{id, hist_id, date}` — id автора не возвращается (нам и не нужен, мы сами знаем, от чьего токена пишем).

### 6.2 Треды — отдельный endpoint, не параметр
Ключевое уточнение к `01_architecture_plan.md` §6 (интерфейс `TrackerAdapter`): threaded reply — **не** параметр `post_comment`, а отдельные вызовы:
- `POST /comment/{comment_id}/reply` — создать ответ
- `GET /comment/{comment_id}/reply` — получить тред

**Также:** обычный `Get Task Comments` **не возвращает** reply-комментарии (только top-level). Значит `TrackerAdapter`-интерфейс нужно скорректировать:

```python
# adapters/base.py — уточнение
def post_comment(self, task_id: str, body: str) -> str: ...              # top-level
def post_reply(self, parent_comment_id: str, body: str) -> str: ...       # thread — ДРУГОЙ endpoint
```

`AgentEvent.thread_ref`, если заполнен → `post_reply`, иначе → `post_comment`. Это меняет реализацию (не контракт `AgentEvent`), правка локализована в `adapters/clickup.py`.

### 6.3 @-упоминания — только через rich-формат
`comment_text` не поддерживает mentions. Нужен `comment` (массив), элемент `{"type": "tag", "user": {"id": <clickup_user_id>}}`. **Требование:** `clickup.py` должен резолвить `agent_id`/`human` из наших доменных идентификаторов в ClickUp `user.id` (статическая таблица маппинга, заполняется при провижининге аккаунтов — та же таблица, что и allowlist для anti-echo, §7).

Синтаксис упоминания **группы** (не отдельного пользователя) не подтверждён документацией — см. `06_open_questions.md`.

## 7. Anti-echo — обновляет `00_overview.md` §5.1

Ресёрч подтверждает: **нативного поля "это бот/интеграция" в ClickUp нет** (открытый feature request в их публичном трекере, не реализован). Домен-эвристика, которую я предложил в `00_overview.md` как основной механизм, **должна стать вторичной**, а не основной — потому что email в webhook payload `history_items[].user` **отсутствует** (там только `id`+`username`), и его получение требует дополнительного GET-запроса (расход rate-limit бюджета на каждое входящее событие ради проверки, которую решает более дешёвый способ).

**Обновлённая схема (заменяет §5.1 в overview):**

```
1. (основной, дёшево) webhook.history_items[].user.id ∈ ALLOWLIST_AGENT_USER_IDS
     → эхо, не роутить как человеческую реплику. ID стабильны, известны заранее при провижининге.
2. (fallback, дороже — доп. GET запрос) при необходимости сверки по домену — Get Task Comments
     возвращает user.email, можно свериться с доменом agents.<...> при разборе неоднозначных случаев.
```

Это не меняет решение в целом (агенты фильтруются как эхо), меняет **приоритет механизмов** — user_id allowlist вместо email-домена как источник истины на горячем пути.

## 8. ClickUp Chat API — для roadmap #1, не MVP

API существует, но официально помечен **"experimental and subject to change at any time"**. Покрывает создание channel/message/reply. **Решение:** не проектировать под это в MVP-контуре, реализовывать (roadmap "Дайджесты Conductor-агента") отдельным изолированным адаптером, который можно безболезненно переписать при breaking change со стороны ClickUp — не завязывать на него ничего в `core/`.

## 9. КРИТИЧЕСКИЙ РИСК: лицензирование агентских аккаунтов

Дека утверждает: агентские аккаунты на отдельном домене, вне корпоративного SSO → не платные места (Guest-квота), экономика "$96/мес за агентов, 3 служебных бесплатно". Ресёрч это **не опровергает полностью, но находит слабое место**:

- Подтверждено (косвенно, не строкой в официальном API-доке ClickUp, а через связанные материалы поддержки/биллинга): раздел **Settings → Apps** (где генерируется Personal API Token) может быть **недоступен роли Guest**. Если это так — Guest-аккаунт физически не может выпустить себе токен для нашей REST-интеграции.
- Это прямое противоречие: план "агент = бесплатный Guest" vs требование "у каждого агента свой Personal API Token для REST-вызовов от своего имени".

**Это не отменяет архитектуру — три равноценных выхода уже заложены в интерфейс `TrackerAdapter` (никакой из них не меняет ядро):**

| Вариант | Что меняется | Стоимость |
|---|---|---|
| (a) Принять 7 платных Member-мест | Ничего в архитектуре — просто 7 отдельных identity, как и планировалось | ~7 × $19–29/мес (Business Plus per-seat, ориентировочно) вместо "$96 суммарно за агентов" из деки |
| (b) Один общий Member/Limited Member аккаунт | `agent_id` различается только через custom field + текстовый префикс в комментарии, не через ClickUp-identity; anti-echo тривиализируется (весь трафик от одного известного user_id — эхо) | 1 платное место — дешевле, чем в деке, но теряется "агент = такой же участник, видимый как коллега" (ключевой тезис деки "Агент — обычный пользователь") |
| (c) Подтвердить, что Guest всё-таки может | Ничего не меняется, дека верна как есть | $0 доп. — но требует 30-минутного эксперимента ДО старта фазы 6 |

**Рекомендация:** сделать (c) первым шагом (спайк на 30 минут — завести один тестовый Guest-аккаунт на отдельном домене, попробовать сгенерировать токен в Settings → Apps) **до** начала фазы 6 плана прототипа, а лучше — прямо сейчас, параллельно с фазами 0-5 (не блокирует разработку на `memory.py`, но блокирует **экономику** решения, которую стоит знать до защиты). См. `02_prototype_plan.md` (добавлена Фаза -1) и `07_grilled.md`.

## 10. Итоговый чек-лист перед фазой 6 (ручные операции, не код)

1. Спайк: Guest + Personal API Token (см. §9) — 30 мин.
2. Создать в UI Space: статусы Backlog/Planned/In Progress/Ready for Verification/Verification/Done/Deferred (§4).
3. Создать в UI custom fields: Agent (dropdown), Cost (USD) (currency), Correlation ID (text), Decision Deadline (date), Artifact Version (text) (§3).
4. Завести агентские ClickUp-аккаунты (7, либо 1 — по итогам §9), сгенерировать Personal API Token каждому.
5. Зарегистрировать webhook на team, сохранить `secret` (§2.1, §2.4).
6. Заполнить таблицу маппинга `agent_id ↔ clickup_user_id` (нужна и для @-упоминаний §6.3, и для allowlist anti-echo §7).

## Источники

Webhooks: developer.clickup.com/docs/webhooks, .../reference/createwebhook, .../docs/webhooksignature, .../docs/webhookhealth, .../docs/webhooktaskpayloads
Auth: .../docs/connect-an-ai-assistant-to-clickups-mcp-server, .../reference/getaccesstoken, .../docs/authentication
Custom Fields: .../docs/customfields, .../reference/setcustomfieldvalue
Comments: .../docs/comments, .../docs/comment-formatting, .../reference/createtaskcomment, .../reference/gettaskcomments, .../reference/createthreadedcomment
Прочее: .../reference/createspace, .../docs/rate-limits, .../docs/chat
Help/биллинг: help.clickup.com — Manage task statuses, Statuses FAQ, Status templates, Intro to billing, Guest-type user roles
Community: feedback.clickup.com — "Api Apps should have a 'bot' setting", "Create API endpoint to create Statuses", "Accessing replies to comments via API", "Using mentions with the API"
