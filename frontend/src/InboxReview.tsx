import { useCallback, useEffect, useState } from "react";
import { api, mutation } from "./api";
import type { LeadPage } from "./types";
import { dateTime, Empty, ErrorNote, Icon, Loading } from "./ui";

interface UnmatchedMessage {
  id: string;
  subject: string;
  sender: string;
  body: string;
  created_at: string;
  category?: string;
  problem?: string;
}

export function InboxReview({ timezone }: { timezone?: string }) {
  const [messages, setMessages] = useState<UnmatchedMessage[] | null>(null);
  const [error, setError] = useState("");
  const load = useCallback(async () => {
    setError("");
    try {
      setMessages(await api<UnmatchedMessage[]>("/admin/inbox/unmatched"));
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Не удалось загрузить входящие.",
      );
    }
  }, []);
  useEffect(() => {
    void load();
  }, [load]);
  return (
    <section className="panel inbox-review">
      <div className="panel-heading">
        <div>
          <span className="eyebrow">РУЧНАЯ ПРОВЕРКА ПЕРЕПИСКИ</span>
          <h3>Входящие без связи с заявкой</h3>
        </div>
        <button
          className="icon-button"
          onClick={() => void load()}
          aria-label="Обновить несопоставленные письма"
        >
          <Icon name="refresh" size={17} />
        </button>
      </div>
      <p className="muted small">
        Система не определяет переписку только по теме. Проверьте отправителя и
        содержание перед сопоставлением.
      </p>
      {error ? (
        <ErrorNote text={error} retry={() => void load()} />
      ) : !messages ? (
        <Loading />
      ) : messages.length ? (
        messages.map((message) => (
          <UnmatchedCard
            key={message.id}
            message={message}
            timezone={timezone}
            onMatch={load}
          />
        ))
      ) : (
        <Empty title="Все входящие разобраны">
          Несопоставленных писем в сохранённых данных нет. Состояние IMAP
          указано выше.
        </Empty>
      )}
    </section>
  );
}

function UnmatchedCard({
  message,
  timezone,
  onMatch,
}: {
  message: UnmatchedMessage;
  timezone?: string;
  onMatch: () => Promise<void>;
}) {
  const [expanded, setExpanded] = useState(false);
  const [leads, setLeads] = useState<LeadPage | null>(null);
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState("");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    if (!expanded) return;
    let active = true;
    const timer = setTimeout(() => {
      api<LeadPage>(
        `/admin/leads?page=1&page_size=50&q=${encodeURIComponent(query)}`,
      )
        .then((result) => {
          if (active) setLeads(result);
        })
        .catch((err) => {
          if (active) setError(err.message);
        });
    }, 250);
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [expanded, query]);
  async function match() {
    setBusy(true);
    setError("");
    try {
      await mutation(`/admin/inbox/${message.id}/match`, {
        lead_id: selected,
        reason,
      });
      await onMatch();
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Не удалось сопоставить письмо.",
      );
    } finally {
      setBusy(false);
    }
  }
  return (
    <article className="unmatched-card">
      <button
        className="unmatched-toggle"
        onClick={() => setExpanded(!expanded)}
        aria-expanded={expanded}
      >
        <div>
          <strong>{message.subject || "Без темы"}</strong>
          <span>
            {message.sender} · {dateTime(message.created_at, timezone)}
          </span>
        </div>
        <Icon name="chevron" size={17} />
      </button>
      {expanded && (
        <div className="unmatched-content">
          <p className="notice compact">Тип: {({ auto_reply: "автоматический ответ", bounce: "возврат письма", ambiguous: "неоднозначная связь", unmatched: "связь не установлена" } as Record<string, string>)[message.category || "unmatched"] || "нужна проверка"}{message.problem && ` · ${message.problem}`}</p>
          <p className="original-message">{message.body}</p>
          {error && <ErrorNote text={error} />}
          <form
            onSubmit={(event) => {
              event.preventDefault();
              void match();
            }}
          >
            <label className="field">
              <span>Найти заявку</span>
              <input
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="Имя, компания или email"
                disabled={busy}
              />
            </label>
            <label className="field">
              <span>Связать с заявкой</span>
              <select
                required
                value={selected}
                onChange={(event) => setSelected(event.target.value)}
                disabled={busy}
              >
                <option value="">Выберите подтверждённую переписку</option>
                {leads?.items.map((lead) => (
                  <option key={lead.id} value={lead.id}>
                    {lead.name} · {lead.email} ·{" "}
                    {lead.reference || lead.id.slice(0, 8)}
                  </option>
                ))}
              </select>
            </label>
            <label className="field">
              <span>Основание для сопоставления</span>
              <textarea
                required
                minLength={3}
                maxLength={500}
                rows={2}
                value={reason}
                onChange={(event) => setReason(event.target.value)}
                placeholder="Как проверено, что письмо относится к этой заявке"
                disabled={busy}
              />
            </label>
            <button className="button primary" disabled={busy || !selected}>
              {busy ? "Сохраняем…" : "Подтвердить связь с заявкой"}
            </button>
          </form>
        </div>
      )}
    </article>
  );
}

export function FollowupReview({
  leadId,
  onChange,
}: {
  leadId: string;
  onChange: () => Promise<unknown>;
}) {
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  return (
    <form
      className="followup-review"
      onSubmit={async (event) => {
        event.preventDefault();
        setBusy(true);
        setError("");
        try {
          await mutation(`/admin/leads/${leadId}/followup-review`, { reason });
          await onChange();
        } catch (err) {
          setError(
            err instanceof Error ? err.message : "Проверка пока не завершена.",
          );
        } finally {
          setBusy(false);
        }
      }}
    >
      <div className="notice warning compact">
        Перед продолжением проверьте почту и состояние входящего канала.
      </div>
      <label className="field">
        <span>Что проверено</span>
        <textarea
          required
          minLength={3}
          maxLength={500}
          rows={3}
          value={reason}
          onChange={(event) => setReason(event.target.value)}
          disabled={busy}
        />
      </label>
      {error && <ErrorNote text={error} />}
      <button className="button secondary full" disabled={busy}>
        {busy ? "Проверяем условия…" : "Повторно проверить follow-up"}
      </button>
      <p className="small muted">
        Проверка не разрешает отправку. Новому письму потребуется одобрение.
      </p>
    </form>
  );
}
