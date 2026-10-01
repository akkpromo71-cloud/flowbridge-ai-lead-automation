# Portfolio demo — 3 минуты

Используйте только уже подготовленные synthetic demo-данные и сохранённые результаты. В этой приёмке приложение/сервисы не перезапускаются, новые заявки/approval не создаются. Для записи показывайте существующие состояния либо безопасные снимки; real controlled smoke никогда не повторяется. Отличайте fake UI от исторически подтверждённого real результата.

| Время | WHAT TO SHOW | WHAT TO SAY | WHAT NOT TO CLAIM |
|---|---|---|---|
| 0:00–0:20 | Landing, форма | «Вручную переносить и квалифицировать заявки долго. Flowbridge сохраняет обращение и помогает менеджеру подготовить ответ». | Реальный клиент, доказанная экономия/ROI |
| 0:20–0:40 | Mermaid из README | «PostgreSQL хранит состояние. FastAPI владеет правилами. Worker доставляет durable jobs, n8n оркестрирует». | n8n — source of truth; AI управляет системой |
| 0:40–1:10 | Уже сохранённая synthetic заявка, состояние обработки | «Приём и Job атомарны; idempotency и recovery защищают обработку». | Новая запись подтверждает live OpenAI; повторный controlled run |
| 1:10–1:40 | Подготовленная карточка анализа/evidence и безопасная сводка controlled 95/HOT | «AI извлекает факты. Backend валидирует и детерминированно считает score. Один real controlled анализ дал 95/HOT». | Fake карточка — результат нового API call; качество на всех текстах |
| 1:40–2:10 | Draft UI и обезличенная сводка real verification | «AI готовит текст. Один real draft прошёл schema/content проверки и остался черновиком; стоимость того теста $0.000626. Sender-brand defect исправлен локально». | Новый prompt перепроверен OpenAI; fake UI текст — real output; гарантия любых фактов |
| 2:10–2:30 | Версия и approval control без клика отправки | «Человек одобряет точную версию и получателя. Правка снимает разрешение». | Реальное письмо отправлено; fully autonomous sending |
| 2:30–2:50 | Dashboard, analytics, integration badges | «Это события synthetic demo. Адаптеры SMTP/IMAP/Telegram реализованы, live verification ожидает клиентского пилота». | Delivery/прочтение, production revenue, real live channels VERIFIED |
| 2:50–3:00 | Stack и таблица проверки | «Python/FastAPI/PostgreSQL/n8n/OpenAI, React/TypeScript. v1 frozen, бесплатные тесты зелёные; пилот — отдельно». | Публичный production deploy или коммерческий опыт проекта |

Ключевая фраза: **AI prepares. Backend validates/scores. Human approves.**

Не показывайте private service files, terminal history, API keys, `.env`, provider headers, реальные контакты и raw local reports. Выберите synthetic контакт на example.com. Не включайте autoplay approval/send и не выдавайте UI controls за доказательство live коммуникации. Историческое письмо с ошибочным subject сохраняется как evidence; текущий neutral subject из regression fixture обозначайте как локальный пример, не новый real ответ.
