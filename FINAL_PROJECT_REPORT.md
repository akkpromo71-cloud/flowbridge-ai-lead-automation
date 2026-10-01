# Flowbridge v1 — final portfolio state

**v1 FROZEN.** Single-company portfolio-проект. Бизнес-поведение и архитектура сохраняются; публичной поставки, реальных клиентов и live коммуникаций не заявляется.

| Компонент | Матрица |
|---|---|
| Real analysis | VERIFIED controlled: один реальный OpenAI request, HTTP 200, gpt-4.1-mini-2025-04-14, schema/semantic pass, 95/HOT |
| Real draft | VERIFIED controlled: один отдельный request, schema/content pass, $0.000626 по usage, draft, human_approval_required=true, sent=false |
| Scoring | VERIFIED, deterministic server-side, Decimal; без изменения правил |
| Human approval | VERIFIED: immutable version/recipient, edit invalidation, repeat/concurrent approval gates |
| SMTP | IMPLEMENTED; live UNVERIFIED/BLOCKED |
| Telegram | IMPLEMENTED; live UNVERIFIED/BLOCKED |
| IMAP | IMPLEMENTED; live UNVERIFIED/BLOCKED |
| Deployment | Local VERIFIED; production target environment pending, Docker runtime UNVERIFIED |

Real provider evidence — приватные owner reports; в публичный Git они не входят. Draft исторически имел sender/client subject defect; исправление lead-draft-2 сделано бесплатно, повторного paid запроса не было. Исторический output сохранён. Новый prompt не имеет повторной live verification; review человеком обязателен, маленькая выборка не доказывает качество всех текстов.

## Portfolio acceptance

Обязательные правки: общие node_modules/runtime ignore rules; переносимая synthetic test fixture вместо private historical report; форматирование без изменения AST; согласованные публичные docs без личных абсолютных путей. Safety/recovery/approval/communication logic не упрощались. Текущие результаты — [verification.md](docs/verification.md), аудит и Git boundary — [PORTFOLIO_AUDIT.md](docs/PORTFOLIO_AUDIT.md).

Прежние подробные README/final/verification/controlled документы сохранены владельцу в игнорируемом `.local/portfolio-history`. Остальные исторические acceptance/evaluation/design reports и screenshots остаются local-only; credentials и runtime state не читаются и не публикуются.

## Remaining client-pilot work

1. Согласованная реальная проверка SMTP/Telegram/IMAP с test recipients и отдельным разрешением.
2. Client business config, privacy/retention, quality и cost acceptance.
3. Целевая HTTPS/private network/access roles среда.
4. Backup/restore/rollback в этой среде.
5. Фиксация поставляемой ревизии.

Это приёмка имеющегося v1, без новых features/RAG/SaaS/CRM. GitHub publication, git push/commit/tag, deploy и live provider actions не выполняются в текущем этапе.
