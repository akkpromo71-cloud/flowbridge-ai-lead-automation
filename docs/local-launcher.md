# Локальный запуск Flowbridge

Для установленного проекта: двойной клик по `START_FLOWBRIDGE.cmd` в корне.
После готовности всех сервисов откроется `http://127.0.0.1:5173/`.
Консоль можно закрыть клавишей: сервисы продолжают работать в фоне.
Для остановки — `STOP_FLOWBRIDGE.cmd`. Базы и runtime-файлы сохраняются.

Launcher использует существующие scripts и процессные markers. Повторный запуск
переиспользует подтверждённые процессы. Общий lock сериализует START/STOP;
если другой запуск ещё работает через 30 секунд, второй безопасно отказывается.
При занятом порте неизвестный процесс не останавливается и не заменяется.

Для PostgreSQL проверяются marker проекта, executable, время создания процесса,
`postmaster.pid`, data directory, владелец listener и соединение с `ai_leads_demo`.
Для n8n проверяются оба ownership markers, CLI path, PID, время создания и readiness.
API/frontend проверяются существующими markers, деревом процесса и HTTP readiness.
Worker переиспользуется по marker и идентичности процесса. При обнаружении
`app.worker` без marker запуск безопасно отказывается вместо создания копии.
Перед запуском через
START проверяется demo-конфигурация; API тоже должен сообщать mode=demo.
Настройки `.env` автоматически не переключаются в другой режим.

На этой Windows-машине холодная загрузка n8n превысила прежние 180 секунд.
`start-n8n.ps1` теперь явно передаёт ограниченное ожидание до 600 секунд.
Без этого параметра `manage-local.mjs`, включая controlled callers, сохраняет
прежние 180 секунд. Это readiness polling локального сервиса, а не provider retry.

## Бесплатные проверки

Из корня проекта:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/tests/local-launcher.tests.ps1
pwsh.exe -NoProfile -File scripts/tests/local-launcher.tests.ps1
node --check n8n/manage-local.mjs
node --test n8n/tests/local-readiness.test.mjs
git diff --check
```

Suite проверяет ownership, чужие порты/PID, переиспользование readiness,
выборочную остановку, lock, границы timeout, синтаксис и CMD entry points.
Реальный lifecycle проверяется отдельно на пустой synthetic demo-БД с временным
`DATABASE_URL` только в процессе теста. Основную demo-БД не очищать и не seed-ить;
worker может обработать уже ожидающие synthetic jobs при обычном полном START.

Business logic, схемы, scoring, UI и n8n workflows этим launcher не изменяются.
Live OpenAI/SMTP/Telegram/IMAP для такой проверки не нужны и не разрешены.
