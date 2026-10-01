# План portfolio screenshots

Набор из семи кадров. В текущей приёмке новые данные и screenshots не создаются; таблица — план записи существующих synthetic состояний. Исторические `docs/screenshots` и Playwright artifacts local-only, до отдельного визуального отбора не публикуются. Проверка metadata не заменяет осмотр пикселей.

| Кадр | Что видно | Что скрыть | Данные | Caption |
|---|---|---|---|---|
| 1. Landing | Название, value proposition, CTA | Браузерный профиль, вкладки/история, личные URL | Публичный интерфейс без клиента | «Flowbridge: от заявки к проверяемому ответу» |
| 2. Architecture | Mermaid из README | Локальные personal paths, private URLs | Только схема | «PostgreSQL — состояние; backend — правила; n8n — orchestration» |
| 3. Dashboard | Synthetic список, статусы/фильтры | Реальные контакты, account menu, cookies | Example Company, demo@example.com | «Защищённый кабинет оператора, synthetic demo» |
| 4. Analysis + HOT/95 | Facts/evidence, score breakdown, source label | UUID/API IDs при ненужности, private report path | Уже существующий synthetic пример с score 95; если нет — безопасная таблица исторического результата | «Deterministic 95/HOT; real controlled result отдельно подтверждён» |
| 5. Draft / approval | Subject/body, версия, approval required | Получатели вне example.com, никакого send click | Existing synthetic draft; нейтральный subject только из local regression как явно обозначенный пример | «AI предлагает. Человек одобряет точную версию» |
| 6. Analytics/integrations | Synthetic события, REAL/SIMULATED badges | Credential settings, tokens, mailbox IDs | Demo aggregates | «Аналитика событий; live communication пока UNVERIFIED» |
| 7. Optional code | Worker claims/fencing или approval gate | Terminal/history, `.env`, personal paths | Исходный код из public candidate | «Durable processing и version-bound approval» |

Не называйте fake карточку реальным OpenAI output. Не исправляйте исторический real subject на изображении, выдавая его за исходный ответ. Если подходящего состояния нет, не запускайте controlled smoke ради кадра: используйте обезличенную сводку и обозначьте её как историческую.

Перед отдельной публикацией кадра: визуально проверить все контакты/панели/таблицы, убрать EXIF/XMP/text metadata и терминальные traces; использовать PNG/WebP без чувствительных полей. Метаданные существующих PNG/WebP проверены при аудите, чувствительные metadata chunks не обнаружены. Public frontend изображения — абстрактная иллюстрация, без клиентских данных. Видео/снимки пока не опубликованы.
