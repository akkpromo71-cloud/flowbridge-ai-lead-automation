# Учебный маршрут: одна заявка от формы до follow-up

Код: `<project-root>`. Начните с новой синтетической заявки в demo после входа оператором. Не используйте реальные контакты. Тесты очищают только явно настроенную `ai_leads_test`; два набора одновременно на ней не запускают.

## 21 остановка

| № | Файл / функция или компонент | Вход → выход / следующий этап | Типичная ошибка |
|---|---|---|---|
| 1 | `frontend/src/LeadForm.tsx`, submit; `api.ts` | Поля и idempotency key → POST / 202 | Новый ключ при повторе того же обращения |
| 2 | `backend/app/main.py`, `/api/v1/public/leads` | HTTP, cookie, Origin → accept_lead | Demo-посетитель не авторизован |
| 3 | `schemas.py:LeadInput` | JSON → типизированные поля | Неверный email, лишнее поле, размер |
| 4 | `intake.py:accept_lead` | Поля + конфиг → commit Lead и Job | Частичное сохранение; rollback должен отменить оба |
| 5 | `models.py:Job`, `jobs.py:dispatch_claim` | Due-job → generation/lease/dispatching | Два исполнителя захватывают одну работу |
| 6 | `worker.py:run_once` | Захваченная работа → HTTP вне транзакции | n8n недоступен, Job остаётся в БД |
| 7 | `worker.py`, `acknowledge_dispatch` | job_id/generation + auth → webhook ACK | Поздний ACK откатывает callback |
| 8 | `n8n/workflows/lead-processing.json` | Job → analyze/draft/finish | Trigger не опубликован |
| 9 | `processing.py:process_step`, `jobs.py:claim_step` | Internal token + generation → step owner | Старое поколение или running-step |
| 10 | `adapters/ai.py:OpenAIAdapter.analyze` | Текст + snapshot → Responses API | Timeout/refusal; fake fallback запрещён |
| 11 | `domain/analysis.py:AnalysisFacts` | Structured JSON → строгий объект | Неверный enum, null вместо списка |
| 12 | `domain/analysis.py:validate_facts` | Факты + исходник → issues/valid | Evidence — пересказ |
| 13 | `domain/scoring.py:score` | Validated facts + пороги → breakdown | Combined бюджет принят за services |
| 14 | `processing.py:_analyze`, `complete_step` | Result + token → Analysis/Lead/Audit | Lease сменился до commit |
| 15 | `admin.py:detail`, `frontend/src/Dashboard.tsx` | Сохранённый анализ → карточка | Unknown показан как COLD |
| 16 | `processing.py:_draft`, `adapters/draft.py` | Facts/config/CTA → proposed subject/body | Выдуманная цена: отказ без отправки |
| 17 | `admin.py:edit_message`, `MessageVersion` | Text + revision → новая immutable версия | Concurrent edit conflict |
| 18 | `admin.py:decision` | Версия + оператор → approval/send-job | Одобрение старой версии |
| 19 | `communication.py:prepare_send/smtp_send/record_accepted` | Повторная проверка → provider_accepted | Обрыв после DATA: delivery_unknown |
| 20 | `communication.py:sync_mailbox/parse_inbound/ingest_inbound` | UID/References/sender → reply или problem | Oversize/UIDVALIDITY/неоднозначность |
| 21 | `communication.py:check_followups/blocked_reason` | Acceptance + delay + fresh inbox → один draft | Reply/opt-out/closed/stale, отдельное approval |

Demo использует FakeAIAdapter, FakeDraftAdapter и fake communication. Controlled real analysis VERIFIED на одной synthetic-заявке: 95/HOT; draft этого запуска был fake. Отдельный real draft VERIFIED controlled: одна генерация, $0.000626 по usage, draft/unapproved/unsent. Subject-branding fix (lead-draft-2) проверен только бесплатно. Live SMTP/Telegram/IMAP UNVERIFIED/BLOCKED. Новые controlled smoke запускать нельзя; матрица — в FINAL_PROJECT_REPORT.md.

## 30 вопросов для интервью

1. Почему PG — источник истины, а n8n history — нет?
2. Чем различаются 202, completed и provider_accepted?
3. Как исключается Lead без Job?
4. Почему email не ключ идемпотентности?
5. Что при том же ключе и другом payload?
6. Что решает SKIP LOCKED?
7. Зачем generation/token при наличии lease?
8. Почему lease не даёт exactly-once у провайдера?
9. Почему сеть вне транзакции?
10. Как обработать callback раньше ACK?
11. Кто владеет scoring и почему?
12. Schema и semantic validation — в чём различие?
13. Почему evidence не пересказ?
14. Какие Unicode-различия допустимы у цитаты?
15. Чем not_fit отличается от unknown?
16. Почему summary не влияет на score?
17. Что с рекламным/combined бюджетом?
18. Зачем config snapshot?
19. Что означает unknown usage?
20. SDK retries и Job retries — в чём различие?
21. Как архитектура ограничивает prompt injection?
22. Почему draft — отдельный call?
23. К чему привязано approval?
24. Что при edit после approve?
25. Почему delivery_unknown нельзя автоматически повторить?
26. Почему subject недостаточен для correlation?
27. Зачем UIDVALIDITY и атомарный cursor?
28. Почему stale inbox блокирует follow-up?
29. Как защищены кабинет, mutations и internal API?
30. Что fake/MockTransport доказывают и чего не доказывают?

## 10 debugging exercises

Используйте tests, MockTransport и отдельную test-БД. Без OpenAI и изменения Windows clock.

1. Intake: тот же key, другой текст; объясните 409.
2. Worker: n8n отвечает 503; найдите сохранённый Job.
3. Два concurrent callback: найдите единственного владельца.
4. Callback старой generation: проверьте fencing.
5. Schema-valid facts с evidence-пересказом: отделите два уровня проверки.
6. AI timeout после резерва: usage неизвестен, стоимость не ноль.
7. Approve v1, edit v2, старый send-job: v2 не повреждена.
8. SMTP disconnect после возможного acceptance: найдите delivery_unknown.
9. Oversize IMAP UID затем normal UID: cursor, problem, follow-up block.
10. Due follow-up при stale inbox: найдите отказ с управляемым тестовым временем.

## 5 небольших coding tasks

Копия конфига и новые учебные tests; production scoring и frozen cases не меняйте.

1. Тест других порогов HOT/WARM в config copy.
2. Тест диапазона через budget threshold и advertising-only budget.
3. Synthetic unknown fit: score/temperature отсутствуют.
4. EN draft с unknown budget/deadline: вопросы без числовых обещаний.
5. Edit approved версии с новым recipient: нужно новое approval.

## Команды

```powershell
$env:TEST_DATABASE_URL = 'postgresql+psycopg://ai_leads_test@127.0.0.1:15432/ai_leads_test'
.\.venv\Scripts\python.exe -m pytest backend/tests -q
.\.venv\Scripts\python.exe -m ruff check backend
```

Подробнее: [test-database-safety.md](test-database-safety.md). Ключи не передавайте через команду или чат.

## Appendix: ответы для самопроверки

Сначала найдите соответствующий код и объясните самостоятельно.

### Вопросы

1. PG хранит durable jobs, версии, approval и audit. n8n запускает защищённые операции.
2. 202 — commit приёма, completed — шаг выполнен, provider_accepted — SMTP acceptance без доказательства доставки.
3. Одна транзакция Lead + Job, rollback отменяет оба.
4. Один человек может обращаться повторно; key относится к запросу.
5. Canonical hash отличается: 409, без второго Lead.
6. Worker пропускает locked jobs; захват одной строки сериализован.
7. Lease — время; generation/token запрещают commit старого владельца.
8. Внешний эффект мог случиться до обрыва; БД не отменит его.
9. Сеть не держит блокировки/соединения неопределённо долго.
10. ACK меняет только dispatching, не running/succeeded.
11. FastAPI/domain: одна детерминированная реализация правил.
12. Schema — форма/типы/enums; semantic — подтверждение фактов исходником и правилами.
13. Пересказ может изменить смысл; принимается реальная цитата.
14. NFC/casefold; порядок слов, пунктуация и содержание сохраняются. Оригинал цитаты хранится.
15. not_fit подтверждён, unknown требует проверки, а не COLD.
16. Summary редакционное; scoring использует validated structured fields.
17. Нейтральный вклад, без деления/конвертации общего бюджета в стоимость услуг.
18. Историческая оценка объяснима после изменения YAML.
19. Полный резерв сохраняется, расход неизвестен, а не ноль.
20. SDK повтор внутри HTTP-call; Job — новое поколение. Controlled ограничивает внешний request одним.
21. Текст недоверенный; модель без tools/секретов/прав approval/send, правила на сервере.
22. Отдельный контракт subject/body, бюджет и ответственность; analysis не даёт право отправки.
23. Точные recipient/subject/body/version и решение оператора.
24. Новая revision, очищенное approval; старое задание не отправит новую версию.
25. Возможен дубликат; нужна сверка с провайдером.
26. Нужны известный Message-ID через References/In-Reply-To и проверка отправителя.
27. UID имеет смысл в UIDVALIDITY; cursor не продвигают без сохранения результата/проблемы.
28. Ответ мог прийти, но ещё не известен; нужна свежая синхронизация.
29. Argon2/session expiry/cookie/Origin/CSRF/rate limits/internal token; live HTTPS/Secure-cookie.
30. Доказывают локальный контракт и правила, но не LLM качество, TLS/права/deliverability реального ящика.

### Debugging

1. `test_intake.py`: hash до нового сохранения.
2. `test_worker.py`/jobs: bounded retry/backoff, Job не исчезает.
3. `test_jobs.py`: row lock + unique step; второй получает conflict.
4. `_owned_step`: generation/token/lease до commit.
5. AI semantic tests: JSON проходит, validate_facts даёт issues, score не подтверждён.
6. `test_processing.py`, `test_metrics.py`: reserved_at/unknown_usage_calls, без fake fallback.
7. `test_security_review.py`: exact version gate, failure старого job не меняет новую версию.
8. `test_communications.py`/jobs: ambiguous DATA/expired lease → unknown, без resend.
9. IMAP tests: bounded problem сохраняется, normal UID обработан, mailbox block остаётся.
10. `check_followups(now=...)`/inbox_fresh; передают время аргументом, не меняют систему.

### Coding tasks: критерии

1. Config copy меняет границу, функция score не переписывается.
2. Пересечение порога и advertising дают neutral budget 7, суммы не складываются.
3. needs_review, score/temperature отсутствуют.
4. Английские вопросы, fake provenance, нет recipient/send/approval и numeric claims.
5. Поля v1 сохранены, v2 recipient новый, approved_version_id пуст, нужно отдельное решение.

Для показа: [DEMO_SCRIPT.md](DEMO_SCRIPT.md). Для клиента: [PILOT_CHECKLIST.md](PILOT_CHECKLIST.md). Результаты проверок: [verification.md](verification.md).
