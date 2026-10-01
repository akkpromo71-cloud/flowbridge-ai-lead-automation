# Описание portfolio-проекта Flowbridge

## A. CV version

Разработал production-oriented AI Lead Automation System на Python/FastAPI/PostgreSQL/n8n/OpenAI: от validated intake и durable processing до deterministic scoring и черновика с human approval. Реальные OpenAI analysis и draft проверены отдельно в controlled synthetic environment; live SMTP/Telegram/IMAP остаются непроверенными. Проект v1 frozen, с бесплатными PostgreSQL integration tests и React/TypeScript интерфейсом.

## B. HH / LinkedIn version

Flowbridge — portfolio-система обработки входящих B2B-заявок для одной компании. FastAPI владеет бизнес-правилами, PostgreSQL хранит состояние, worker доставляет durable jobs в n8n. OpenAI извлекает structured facts с evidence; schema и semantic validators проверяют результат, а deterministic scoring рассчитывает HOT/WARM/COLD. Lease/generation/fencing и recovery защищают обработку от повторов и устаревших результатов. Ответ хранится как неизменяемая MessageVersion и требует human approval конкретного текста и получателя. Бесплатная приёмка включает 457 backend и 16 frontend unit tests, Ruff, build/lint/typecheck. Real OpenAI analysis и отдельный draft VERIFIED controlled на synthetic данных; production deployment и live почтовые/Telegram-интеграции не заявляются. v1 frozen, следующий этап ограничен клиентской приёмкой имеющейся реализации.

## C. Extended portfolio version

### Задача

В ручном процессе менеджер переносит заявки из формы, оценивает их приоритет и готовит ответы. Проект объединяет эти операции в проверяемый single-company workflow. Это инженерный portfolio-проект, без заявлений о реальных клиентах, выручке или коммерческом production-опыте.

### Архитектура и инженерные решения

FastAPI атомарно сохраняет Lead и Job в PostgreSQL. Worker захватывает задания с lease и generation; n8n запускает защищённые backend steps и расписания, не дублируя business rules. PostgreSQL — source of truth. Сетевые операции выполняются после закрытия транзакций; generation fencing отклоняет устаревшие результаты, но не обещает exactly-once внешних действий.

OpenAI возвращает Pydantic structured facts. Сервер проверяет schema, цитаты/evidence, согласованность service_fit и бюджетов; неизвестные и противоречивые данные остаются поводом для review. Decimal scoring применяет конфигурацию компании, не доверяя summary или модельному решению о приоритете. Draft использует существующий отдельный адаптер, approved claims и CTA; AI не может одобрять и отправлять.

### Надёжность и безопасность

Approval связан с immutable subject/body/recipient version; редактирование сбрасывает send permission. Неопределённый SMTP outcome сохраняется как delivery_unknown и требует ручной сверки без слепого retry. IMAP использует курсор и correlation; один follow-up требует отдельного approval и блокируется reply/opt-out/stop/stale inbox. Кабинет защищён авторизацией, CSRF/Origin и ограничениями запросов. Routine logs не содержат полные сообщения и credentials, demo/test изолированы от live secrets.

### Проверки и границы

457 backend tests на отдельной PostgreSQL и 16 frontend unit tests прошли бесплатную приёмку; Ruff check/format, pip check, frontend build/lint/typecheck зелёные. Playwright-сценарии имеются, но в этой приёмке не перезапускались. Portable synthetic fixture заменяет зависимость тестов от приватного historical report; она не выдаётся за live evidence.

Один real controlled анализ: HTTP 200, схема/семантика пройдены, 95/HOT. Отдельный real draft: модель gpt-4.1-mini-2025-04-14, schema/content passed, $0.000626 по usage, draft/unapproved/unsent. Sender/client subject defect исправлен локально в prompt lead-draft-2; повторного платного запроса не было. Маленькая выборка не гарантирует точность всех будущих обращений.

SMTP, Telegram и IMAP IMPLEMENTED, но live UNVERIFIED/BLOCKED. Локальная поставка проверена; production target, HTTPS/access roles, backup/restore/rollback и клиентская конфигурация/качество/privacy/cost acceptance ожидают отдельного пилота. v1 frozen: без новых features, multitenant SaaS, RAG или CRM expansion.
