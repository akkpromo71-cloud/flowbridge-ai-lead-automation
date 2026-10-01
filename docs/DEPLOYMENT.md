# Поставка, восстановление и обновление

Windows local dev VERIFIED отдельно от Docker/Linux. Подготовленные файлы не означают состоявшийся публичный deploy. Все адреса, credentials, тарифы и тестовые получатели клиента согласуются перед запуском.

## Windows

Команды первого запуска находятся в [README](../README.md). Штатный `start-dev.ps1` использует существующий portable PostgreSQL, миграции, четыре published n8n workflow, API, frontend и worker. Процессы зарегистрированы проектом; занятые чужие порты не освобождаются. `stop-dev.ps1` не удаляет БД. `check-restore.py` создаёт свою scratch database, проверяет восстановление и удаляет только её, сохраняя исходную БД.

## Docker demo

Корневой `compose.yaml` — отдельная demo-поставка, все providers fake. Сначала создаются секреты/внутренние workflows и imports, затем owner и operator, и только потом worker. Подробные команды — [operations.md](operations.md). `docker compose config --quiet` проверяет конфигурацию; это не container smoke. Не применять `down -v` к данным, которые нужно сохранять.

## Малый Linux server

`deploy/compose.live.yaml` сохраняет ту же архитектуру: HTTPS proxy → app; worker → private n8n → app; PostgreSQL и n8n в закрытой сети. Только proxy публикует внешние порты. n8n editor доступен лишь по loopback через SSH tunnel; PostgreSQL не имеет host port. Не импортировать demo data в live.

До запуска: Docker Engine/Compose, приватная БД, домен, сертификат и папки secret/backup принадлежат владельцу. Эти системные установки здесь не выполнялись. Вне checkout, например в `/etc/flowbridge`, создаётся app settings file с mode 0600: `DATABASE_URL` указывает на `ai_leads_live` с отдельной ролью, `PUBLIC_URL=https://<domain>`, `ALLOWED_ORIGINS=https://<domain>`, сильные разные `INTERNAL_TOKEN`/`N8N_WEBHOOK_TOKEN`, `BUSINESS_CONFIG`, `ALLOW_EXTERNAL_SENDS=false`, `REAL_DRAFT_ENABLED=false`, лимиты и подтверждённый тариф. OpenAI key предоставляется только через согласованный private process secret injection, не записывается в repository или workflow exports. Не печатать environment/config в терминале.

Compose receives only named secrets; n8n не получает OpenAI/SMTP credentials. Infrastructure env file хранится отдельно и содержит только нужные DB/encryption/path settings. Для OpenAI переменная процесса `OPENAI_API_KEY` передаётся supervisor/secret manager; не вводить literal key в командной строке. Во время smoke приложения ключ вводится скрыто через [controlled launcher](CONTROLLED_MODE.md).

Подготовленные команды для согласованного сервера (не выполнены здесь):

```sh
docker compose -f deploy/compose.live.yaml --env-file /etc/flowbridge/infrastructure.env config --quiet
docker compose -f deploy/compose.live.yaml --env-file /etc/flowbridge/infrastructure.env up --build -d postgres n8n app proxy
# Import credentials/workflows and publish the same four workflow IDs; see operations.md.
# Create operator through hidden-password CLI, check activated triggers and HTTPS auth.
docker compose -f deploy/compose.live.yaml --env-file /etc/flowbridge/infrastructure.env up -d worker
```

Пути `FLOWBRIDGE_APP_ENV`, `FLOWBRIDGE_WORKFLOWS` и `FLOWBRIDGE_TLS_DIRECTORY` абсолютные и вне репозитория. Workflow generator получает `BUILD_BACKEND_URL=http://app:8000`; secrets import — временный приватный файл только для n8n internal/webhook tokens, удаляемый после импорта. Editor credentials отдельно. Первый init-db script работает только на новом volume; существующий volume не переинициализируется.

## Health, backup, restore

`/health/live` означает живой процесс; `/health/ready` — доступную БД. Docker health не доказывает доступность OpenAI/mailbox. Контролировать также failed/needs_review jobs, lease, stale n8n/IMAP и delivery_unknown. Не включать SQL/HTTP access body logging. Docker json-file logs ограничены 10 MB × 3; доступ к logs restricted. Локальные `.local` логи не переносятся клиенту и очищаются/архивируются только после остановки проверенных проектных процессов.

Backup: custom-format `pg_dump` каждой application/n8n DB, encryption key n8n и приватные настройки хранятся раздельно в зашифрованном backup с ограниченным доступом. Credentials через `PGPASSFILE`/secret manager, без passwords в argv. Согласовать расписание, retention, RPO и RTO; проверить backup до удаления старых копий. Snapshot n8n без encryption key не обеспечивает восстановление credentials.

Restore: создать **новую** закрытую БД, `pg_restore --no-owner --exit-on-error`, проверить alembic revision, counts и связи, затем smoke с отключёнными отправками. Не восстанавливать поверх работающей БД и не запускать два worker на один mailbox. Демонстрационная проверка:

```powershell
.\.venv\Scripts\python.exe scripts/check-restore.py
```

## Upgrade и rollback

1. Зафиксировать ревизию исходников/config/schema и сделать проверяемый backup; отдельный test deployment.
2. Отключить внешние отправки и остановить свой worker; дождаться или вручную разобрать sending/delivery_unknown.
3. Проверить migration plan и совместимость, применить миграции одним процессом; запустить API и проверить ready/auth.
4. Проверить workflow publication и synthetic smoke, затем worker. Внешние sends включать только после приёмки.
5. Rollback совместимой версии приложения возможен только при совместимой схеме. После изменения схемы безопаснее restore в новую БД и явное переключение; downgrade выполняется отдельно на копии. Не повторять неизвестные SMTP исходы после restore.

Не переписывайте старые immutable versions/approvals, не очищайте production tables тестами. Технические блокеры и проверенные команды — [FINAL_PROJECT_REPORT.md](../FINAL_PROJECT_REPORT.md).
