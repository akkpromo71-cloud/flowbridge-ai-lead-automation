# Изолированный n8n для demo

Используется **n8n 2.40.7**, Node.js **24.19.0**, npm **11.17.0**. npm сообщает требование n8n `node >=24.0.0`. Версия зафиксирована вместе с зависимостями в `n8n/package-lock.json`.

## Что изолировано

- HTTP/editor и production webhooks: `http://127.0.0.1:5681`.
- Task broker: `127.0.0.1:5682`. Четыре workflow не используют Code nodes; отдельный task runner не требуется.
- npm runtime: `%LOCALAPPDATA%\AILeadAutomationPro\n8n-runtime`.
- Настройки, encryption key, PID и журналы: `%LOCALAPPDATA%\AILeadAutomationPro\n8n-demo`.
- Собственная PostgreSQL БД и роль: `ai_leads_n8n` на `127.0.0.1:15432`.

Скрипты проверяют маркер владельца каталогов и занятость портов. Они не обращаются к историческому экземпляру на 5679 и не используют `%USERPROFILE%\.n8n`. Windows service, PATH, firewall, системные пакеты не меняются. Большой npm runtime вынесен из OneDrive; версия, lock и управление остаются в проекте.

Процесс получает ограниченный набор переменных окружения. Credentials OpenAI/SMTP/Telegram/IMAP и параметры другого n8n из пользовательского shell не наследуются. Телеметрия, уведомления о версиях, шаблоны, community packages и неиспользуемые AI/MCP-модули отключены. HTTP cookie без Secure допустима только для этой локальной loopback-демонстрации.

## Установка, импорт и запуск

Из корня проекта в PowerShell:

```powershell
.\scripts\start-postgres.ps1
.\n8n\install-local.ps1
```

`install-local.ps1` использует `npm ci --ignore-scripts` с зафиксированным lock. Системный Node не заменяется. На проверенном Windows x64/Node 24 пакет isolated-vm содержит готовый native runtime; внешняя компиляция не нужна для выбранных workflow. На другой платформе runtime должен пройти отдельную проверку.

Сначала создайте `.env` командой настройки приложения из README. Нужны `MODE=demo` и два различных случайных значения `INTERNAL_TOKEN` и `N8N_WEBHOOK_TOKEN` длиной не менее 32 символов. Скрипты не показывают их в выводе.

```powershell
.\scripts\start-n8n.ps1 -Setup
```

`-Setup` импортирует два header-auth credential через временный файл вне OneDrive, удаляет этот файл, импортирует четыре workflow и публикует их по отдельным IDs. Credentials шифруются n8n отдельным локальным ключом. Затем запускается скрытый фоновый процесс. Первый запуск создаёт защищённую учётную запись владельца редактора; её уникальный пароль сохраняется только в `%LOCALAPPDATA%\AILeadAutomationPro\n8n-demo\owner-access.json`. Это отдельная учётная запись от оператора приложения.

Startup проверяет readiness и фактический статус каждого триггера через authenticated n8n API. Если уже опубликованная версия не активировала триггер после предыдущего сбоя, выполняется один ограниченный повтор публикации этой же версии. Намеренно снятый с публикации workflow автоматически не включается. PostgreSQL pool n8n увеличен до пяти соединений: стандартных двух оказалось недостаточно для одновременной первичной активации четырёх workflows.

Обычный последующий запуск:

```powershell
.\scripts\start-n8n.ps1
```

Остановка:

```powershell
.\scripts\stop-n8n.ps1
```

Сначала остановите worker приложения. На Windows скрипт завершает только PID, чей command line подтверждает этот project runtime. Незавершённые прикладные задания восстанавливает worker по PostgreSQL lease/generation; завершение процесса не является подтверждением внешней операции.

Для обновления export JSON: остановить project n8n, изменить workflow, выполнить `-Setup` и затем smoke test. Не импортировать эти IDs в другой существующий экземпляр: импорт по тому же ID обновляет соответствующую запись.

## Workflows и контракты

| Файл | ID | Триггер | Шаги |
|---|---|---|---|
| `n8n/workflows/lead-processing.json` | `aiLeadProcessing` | POST `/webhook/ai-lead-processing` | analyze → draft → finish |
| `n8n/workflows/communication.json` | `aiLeadCommunication` | POST `/webhook/ai-lead-communication` | notification: notify → finish; email_send: send → finish; followup_prepare: draft → finish |
| `n8n/workflows/mail-sync.json` | `aiLeadMailSync` | Каждую минуту | POST `/internal/v1/mailboxes/default/sync` |
| `n8n/workflows/followup-check.json` | `aiLeadFollowupCheck` | Каждые 5 минут | POST `/internal/v1/followups/check` |

Webhook принимает `job_id`, `generation`, `kind` и требует `X-Webhook-Token`. Ответ 202 подтверждает получение, а не выполнение. Backend step вызывается по `/internal/v1/jobs/{job_id}/steps/{step}` с JSON `{"generation": ...}` и отдельным `X-Internal-Token`. Backend проверяет вид задания, актуальность generation, atomically claims step и права на отправку.

n8n не вычисляет score, не одобряет версии писем и не пишет прикладную БД. Ошибка HTTP прерывает workflow; повторов внешних действий на уровне HTTP node нет. Повторами и неопределёнными результатами управляет backend. Неизвестный `kind` приводит к Stop And Error.

Через workflow не передаются обращения, почтовые адреса и тела писем. Сохранение execution payload выключено для successful, failed и manual executions, поскольку входящий Webhook node также содержит HTTP-заголовки. Метаданные исполнения и прикладной audit остаются доступны раздельно. Export JSON содержит только ссылки на credential IDs, без самих токенов и pinned data.

Используются штатные Webhook 2.1, HTTP Request 4.4, If 2.3, Schedule Trigger 1.3 и Stop And Error 1. Параметры сверены с установленным исходным кодом n8n 2.40.7. `n8n/build-workflows.mjs` воспроизводит JSON. Для Compose задаются `BUILD_BACKEND_URL=http://app:8000` и `BUILD_WORKFLOW_OUTPUT=.local/compose-workflows`; локальные exports при этом не заменяются.

## Штатные CLI-операции

Менеджер передаёт каждый CLI-вызов только в изолированный runtime с его БД и encryption key:

```text
import:credentials --input=<temporary-file-outside-repository>
import:workflow --separate --input=<project>/n8n/workflows
publish:workflow --id=aiLeadProcessing
publish:workflow --id=aiLeadCommunication
publish:workflow --id=aiLeadMailSync
publish:workflow --id=aiLeadFollowupCheck
export:workflow --id=<workflow-id> --pretty --output=<file>
```

Не вызывайте глобальный `n8n` без явного окружения: он может использовать данные другого экземпляра. Чтобы экспортировать текущие workflows без credentials:

```powershell
node n8n/manage-local.mjs export
```

Экспорты появятся в `%LOCALAPPDATA%\AILeadAutomationPro\n8n-demo\verified-exports`. После импорта/публикации нужен перезапуск процесса. Это поведение подтверждено [официальной CLI-документацией](https://docs.n8n.io/deploy/host-n8n/configure-n8n/use-the-command-line/) и кодом установленной версии.

## Состояние проверки

**IMPLEMENTED:** изоляция runtime/data/PostgreSQL, credential import, четыре JSON, публикация и команды управления.

**VERIFIED:** npm устанавливает 2.40.7; `n8n --version` возвращает 2.40.7; требование Node выполнено; native isolated-vm импортируется; использованные node versions присутствуют в установленном runtime. Выполнены реальные CLI import двух credentials и четырёх workflows, publish, export. После export проверено полное совпадение nodes/connections/settings всех четырёх workflows с исходными JSON.

**VERIFIED:** `/healthz/readiness` возвращает 200; оба webhook без токена отклоняют запрос с 403; четыре триггера имеют фактический статус `activated`. У редактора настроен owner, публичная первичная регистрация закрыта. Проверка доступна командой:

```powershell
node n8n/manage-local.mjs verify
```

Результаты сквозного backend smoke перечислены в `docs/verification.md`. Live внешние отправки и credentials этим локальным n8n не проверяются. У выбранного n8n выводится предупреждение о будущем прекращении установки вне контейнера; текущая проверенная npm-версия зафиксирована, автоматических major-обновлений нет.
