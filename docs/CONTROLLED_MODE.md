# Controlled mode — завершённые проверки v1

Real analysis и отдельный real draft VERIFIED controlled, каждый одним разрешённым запросом модели `gpt-4.1-mini-2025-04-14`. **Повторять controlled analysis/draft smoke запрещено.** Ключи, reservations, runtime DB и owner reports local-only. Основной demo/live не переключается.

| Проверка | Результат |
|---|---|
| Analysis | UI→backend→PostgreSQL→worker→n8n→OpenAI→validation→scoring; HTTP 200, schema/semantic pass, 95/HOT |
| Draft | Validated synthetic source→existing OpenAIDraftAdapter→OpenAI→validation→MessageVersion; HTTP 200, schema/content passed |
| Draft usage | 845 input + 180 output tokens, latency 5766 ms, $0.000626 по usage |
| Draft state | draft, human_approval_required=true, approved_version_id=null, sent=false |
| Communication | SMTP/Telegram/IMAP не вызывались; live UNVERIFIED/BLOCKED |

Analysis source был бесплатно восстановлен из verified owner report после обнаружения пустой controlled DB. Новые UUID/timestamps не выдаются за original rows; provenance restored_from_verified_controlled_report и historical reference показывают происхождение. Original jobs/provider audit не фабриковались. Постоянная отметка historical analysis запрещает повтор; draft имеет отдельную уже использованную reservation. Restore/prepare-source после draft не повторять, reservations не сбрасывать.

Controlled launcher — проверочный инструмент, не второй analyser/generator. Для draft нет UI/worker/n8n/почтовых действий. Максимум один Responses POST, SDK/HTTP retries=0, timeout ≤40 s, output ≤800 tokens, исторический cap $0.02 и reserve $0.01728. Ambiguous outcome расходует попытку без автоматического повтора. Наличие API key никогда не означает разрешение вызова.

Исторический subject ошибочно использовал sender Flowbridge как клиента. Бесплатный fix в существующем адаптере: sender_business_name, neutral subject при неизвестной компании и customer name только из явно подтверждённых business_context/evidence. Общий NFC/casefold subject gate запрещает sender name, в том числе при совпадении названий клиента и отправителя. Body и старый output не переписывались. Текущий prompt lead-draft-2 имеет только FREE regression verification; исторический real call хранит прежнюю prompt version.

| Профиль | Application DB | n8n DB | UI/API | n8n/broker | Граница |
|---|---|---|---|---|---|
| Demo | ai_leads_demo | ai_leads_n8n | 5173/8000 | 5681/5682 | Synthetic, fake providers |
| Controlled | ai_leads_controlled | ai_leads_n8n_controlled | 8001 | 5683/5684 | Исторически analysis=1, draft=1; no communication |
| Live | Отдельная клиентская DB | Отдельная n8n DB | HTTPS | Private | Только после разрешённого пилота |

Private service/operator files находятся в `%LOCALAPPDATA%/AILeadAutomationPro`, вне repository/OneDrive. Demo/test отвергают live credentials, real draft по умолчанию выключен. Routine logs не содержат тексты писем. Приватные подробные reports сохранены владельцу и исключены из Git; публичные fixtures не являются live evidence.

Текущие бесплатные результаты — [verification.md](verification.md), публичный аудит — [PORTFOLIO_AUDIT.md](PORTFOLIO_AUDIT.md), client-pilot checklist — [PILOT_CHECKLIST.md](PILOT_CHECKLIST.md). Платные команды здесь не предлагаются; новых разрешений на provider actions нет.
