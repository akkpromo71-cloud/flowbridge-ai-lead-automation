# Бесплатная portfolio-приёмка v1

Дата: 2026-10-01. v1 FROZEN. Live OpenAI/SMTP/Telegram/IMAP, controlled launchers, deploy и изменение demo/live data в этой приёмке запрещены и не выполняются.

## Baseline и безопасные изменения

До cleanup: 457 backend tests passed (77.70 s), Ruff check и pip check PASS; frontend build/lint/typecheck PASS, 16 unit tests passed. Ruff format check выявил 11 файлов. Существующие synthetic source tests зависели от `.local` owner report; зависимость заменена временным каталогом с публичной явно synthetic mock fixture, production reader/guards не изменены.

При адаптации test root было два промежуточных запуска с 3 failed/20 passed: сначала путь reader не совпадал с временным root, затем не хватало копии test config. Исправления только в fixture. После них 23 passed; Windows в Mock SDK тесте вывела диагностический WMI exception 0x8007000e, pytest всё же завершился exit 0. Это не live API call; диагностический вывод не скрыт. Дополнительный осмотр image metadata сначала не выполнился из-за отсутствия Pillow; зависимости не ставились, metadata проверены стандартной библиотекой.

11 Python файлов отформатированы Ruff. AST hashes всех 64 Python файлов до/после форматирования совпали; отдельное изменение test fixture выполнено до этой сверки. Production expressions, prompt strings, schema values, scoring и approval semantics не изменены.

## Final free verification

Финальный full backend в основной папке: **457 passed / 67.98 s**, 0 failed/skipped, exit 0. Дополнительный последний full run из clean Git-index export без .env/private reports: **457 passed / 102.47 s**, 0 failed/skipped, без WMI diagnostic. Экспорт использовал только staged files и существующие project dependencies; PYTHONPATH указывал на экспортированный backend, БД — только ai_leads_test. Ruff check PASS; format check — 64 files already formatted; pip check PASS. Frontend build/lint/typecheck PASS, 16 unit tests passed (383 ms), Vite 46 modules/1.16 s. Alembic check: No new upgrade operations detected, на проверенной test DB в read-only transaction. Node syntax checks обоих n8n launchers PASS. Python/PowerShell static checks перечислены в аудите. WMI diagnostic 0x8007000e повторился во время Mock SDK headers, несмотря на успешный pytest; системные настройки и зависимости ради него не менялись.

```powershell
$env:TEST_DATABASE_URL = 'postgresql+psycopg://ai_leads_test@127.0.0.1:15432/ai_leads_test'
.\.venv\Scripts\python.exe -m pytest backend/tests -q --tb=short
.\.venv\Scripts\python.exe -m ruff check backend
.\.venv\Scripts\python.exe -m ruff format --check backend
.\.venv\Scripts\python.exe -m pip check
npm.cmd --prefix frontend run build
npm.cmd --prefix frontend run lint
npm.cmd --prefix frontend test
npm.cmd --prefix frontend run typecheck
```

Тесты используют только allowlisted PostgreSQL ai_leads_test с identity verification до миграций/очистки; SQLite не подменяет PG. Alembic check исполняется с явно переданной проверенной test connection в read-only transaction, без Settings/.env fallback. Unit/provider tests используют fake/MockTransport; real controlled commands не запускаются. Playwright существует, но в этом этапе не выполняется, поскольку создаёт demo data. Никаких новых live requests.

## Историческая verification matrix

Real analysis VERIFIED controlled: ровно один real request, HTTP 200, gpt-4.1-mini-2025-04-14, schema/semantic pass, 95/HOT. Отдельный real draft VERIFIED controlled: один request, schema/content pass, $0.000626 по usage, draft/unapproved/unsent. SMTP/Telegram/IMAP не использовались. Subject-branding fix lead-draft-2 проверен бесплатно, без повторного OpenAI. Это не подтверждение качества на всех клиентах.

Scoring/human approval VERIFIED локально; SMTP/Telegram/IMAP IMPLEMENTED, live UNVERIFIED/BLOCKED. Исторические browser/worker/n8n/backup результаты не выдаются за повторный текущий прогон. Production HTTPS/target environment pending, Docker runtime UNVERIFIED. Подробная история сохранена local-only в `.local/portfolio-history`.

Публичные claims и live matrix — [FINAL_PROJECT_REPORT.md](../FINAL_PROJECT_REPORT.md). Git audit/security и точные финальные результаты — [PORTFOLIO_AUDIT.md](PORTFOLIO_AUDIT.md).
