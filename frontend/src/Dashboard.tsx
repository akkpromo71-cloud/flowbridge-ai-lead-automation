import { useCallback, useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import { api, mutation } from "./api";
import type {
  Analytics,
  Integration,
  LeadDetail,
  LeadPage,
  Message,
  PublicConfig,
  Session,
  Stage,
} from "./types";
import { InboxReview, FollowupReview } from "./InboxReview";
import { AnalyticsPanels } from "./AnalyticsPanels";
import { AnalysisView } from "./analysis-view";
import {
  Badge,
  dateTime,
  Empty,
  ErrorNote,
  Icon,
  initials,
  Loading,
  Logo,
  messageNames,
  processNames,
  stageNames,
} from "./ui";

type View = "overview" | "leads" | "integrations";
export function Dashboard({
  session,
  config,
  onLogout,
}: {
  session: Session;
  config: PublicConfig | null;
  onLogout: () => void;
}) {
  const [view, setView] = useState<View>("overview");
  const [leadId, setLeadId] = useState<string | null>(
    () =>
      window.location.hash.match(/^#\/app\/leads\/([a-zA-Z0-9-]+)$/)?.[1] ||
      null,
  );
  const [logoutError, setLogoutError] = useState("");
  useEffect(() => {
    const changed = () =>
      setLeadId(
        window.location.hash.match(/^#\/app\/leads\/([a-zA-Z0-9-]+)$/)?.[1] ||
          null,
      );
    window.addEventListener("hashchange", changed);
    return () => window.removeEventListener("hashchange", changed);
  }, []);
  const title = {
    overview: "Обзор",
    leads: "Заявки",
    integrations: "Интеграции",
  }[view];
  async function logout() {
    try {
      await mutation("/auth/logout", {});
      onLogout();
    } catch (err) {
      setLogoutError(err instanceof Error ? err.message : "Не удалось выйти.");
    }
  }
  function navigate(next: View) {
    setView(next);
    setLeadId(null);
    window.history.replaceState(null, "", "#/app");
  }
  return (
    <div className="workspace">
      <aside className="sidebar">
        <Logo inverse />
        <div className="workspace-label">
          <span className="workspace-avatar">
            {initials(config?.brand.name || "Lead Flow")}
          </span>
          <div>
            <strong>{config?.brand.name || "Рабочее пространство"}</strong>
            <span>
              {config?.mode === "demo"
                ? "Демонстрационный экземпляр"
                : "Кабинет компании"}
            </span>
          </div>
        </div>
        <span className="nav-caption">РАБОТА С ЗАЯВКАМИ</span>
        <nav aria-label="Разделы кабинета">
          {(
            [
              { id: "overview", icon: "grid", label: "Обзор" },
              { id: "leads", icon: "inbox", label: "Заявки" },
              { id: "integrations", icon: "plug", label: "Интеграции" },
            ] as const
          ).map((item) => (
            <button
              key={item.id}
              className={view === item.id ? "active" : ""}
              onClick={() => navigate(item.id)}
              aria-current={view === item.id ? "page" : undefined}
            >
              <Icon name={item.icon} />
              {item.label}
              {view === item.id && <span className="nav-active-dot" />}
            </button>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <a href="#/demo">
            <Icon name="book" size={18} />
            Публичная демонстрация
            <Icon name="arrow" size={15} />
          </a>
          <div className="sidebar-note">
            <Icon name="shield" size={17} />
            <p>
              Отправка сообщений
              <br />
              только после одобрения
            </p>
          </div>
          <div className="operator">
            <span className="operator-avatar">
              {initials(
                session.operator.display_name || session.operator.email,
              )}
            </span>
            <div>
              <strong>{session.operator.display_name || "Оператор"}</strong>
              <span>{session.operator.email}</span>
            </div>
            <button
              onClick={logout}
              aria-label="Выйти из кабинета"
              title="Выйти"
            >
              <Icon name="logout" size={18} />
            </button>
          </div>
        </div>
      </aside>
      <div className="workspace-body">
        <header className="workspace-header">
          <div className="breadcrumbs">
            <span>Рабочее пространство</span>
            <Icon name="chevron" size={14} />
            <strong>{leadId ? "Карточка заявки" : title}</strong>
          </div>
          <div className="workspace-status">
            <span
              className={config?.mode === "demo" ? "demo-dot" : "live-dot"}
            />
            {config?.mode === "demo"
              ? "DEMO · синтетические данные"
              : config?.mode === "controlled" ? "CONTROLLED · REAL AI / SIMULATED почта"
              : "Защищённый доступ"}
          </div>
        </header>
        <main className="dashboard-main" id="main-content">
          {logoutError && <ErrorNote text={logoutError} />}
          {config?.mode === "demo" && (
            <div className="notice demo-banner">
              <Icon name="shield" size={18} />
              <span>
                Демонстрация: письма и уведомления не отправляются, платные
                AI-запросы не выполняются.
              </span>
            </div>
          )}
          {config?.mode === "controlled" && (
            <div className="notice demo-banner"><Icon name="shield" size={18} /><span>CONTROLLED: анализ REAL; draft {config.providers?.draft || "SIMULATED"}; почта и уведомления SIMULATED. Только синтетические данные.</span></div>
          )}
          {leadId ? (
            <LeadCard
              id={leadId}
              config={config}
              onBack={() => {
                setLeadId(null);
                window.history.replaceState(null, "", "#/app");
              }}
            />
          ) : view === "integrations" ? (
            <Integrations timezone={config?.timezone} />
          ) : (
            <LeadList
              view={view}
              timezone={config?.timezone}
              services={config?.services || []}
              onOpen={(id) => {
                setLeadId(id);
                window.history.replaceState(null, "", `#/app/leads/${id}`);
              }}
            />
          )}
        </main>
        <footer className="workspace-footer">
          <span>LeadFlow · AI Lead Automation</span>
          <span>Часовой пояс: {config?.timezone || "Asia/Qyzylorda"}</span>
        </footer>
      </div>
    </div>
  );
}

function LeadList({
  view,
  timezone,
  services,
  onOpen,
}: {
  view: View;
  timezone?: string;
  services: PublicConfig["services"];
  onOpen: (id: string) => void;
}) {
  const [data, setData] = useState<LeadPage | null>(null);
  const [analytics, setAnalytics] = useState<Analytics | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(true);
  const [query, setQuery] = useState("");
  const [search, setSearch] = useState("");
  const [temperature, setTemperature] = useState("");
  const [stage, setStage] = useState("");
  const [sort, setSort] = useState("newest");
  const [page, setPage] = useState(1);
  const latestRequest = useRef(0);
  const load = useCallback(async () => {
    const requestNumber = ++latestRequest.current;
    setBusy(true);
    setError("");
    const params = new URLSearchParams({ page: String(page), page_size: "10" });
    if (search) params.set("q", search);
    if (temperature) params.set("temperature", temperature);
    if (stage) params.set("stage", stage);
    params.set("sort", sort);
    try {
      const [leads, stats] = await Promise.all([
        api<LeadPage>(`/admin/leads?${params}`),
        api<Analytics>("/admin/analytics"),
      ]);
      if (requestNumber === latestRequest.current) {
        setData(leads);
        setAnalytics(stats);
      }
    } catch (err) {
      if (requestNumber === latestRequest.current)
        setError(
          err instanceof Error ? err.message : "Не удалось загрузить заявки.",
        );
    } finally {
      if (requestNumber === latestRequest.current) setBusy(false);
    }
  }, [page, search, temperature, stage, sort]);
  useEffect(() => {
    void load();
  }, [load]);
  useEffect(() => {
    const timer = setTimeout(() => {
      setSearch(query);
      setPage(1);
    }, 300);
    return () => clearTimeout(timer);
  }, [query]);
  return (
    <>
      <div className="page-heading">
        <div>
          <span className="eyebrow">
            {view === "overview"
              ? "РАБОЧИЙ ДЕНЬ, В КОНТЕКСТЕ"
              : "ОТ ОБРАЩЕНИЯ К СЛЕДУЮЩЕМУ ШАГУ"}
          </span>
          <h1>{view === "overview" ? "Всё начинается с заявки" : "Заявки"}</h1>
          <p>
            {view === "overview"
              ? "Приоритеты, ответы и решения вашей команды."
              : "Каждое обращение сохранено. Каждое действие в истории."}
          </p>
        </div>
        <button
          className="button secondary"
          onClick={() => void load()}
          disabled={busy}
        >
          <Icon name="refresh" size={16} />
          Обновить
        </button>
      </div>
      {analytics && view === "overview" && (
        <>
          <div className="metric-grid">
            <Metric
              label="Всего заявок"
              value={String(analytics.total)}
              icon="inbox"
              note="За последние 30 дней"
            />
            <Metric
              label="Высокий приоритет"
              value={String(analytics.temperatures.HOT || 0)}
              icon="spark"
              note="По правилам текущих анализов"
              accent
            />
            <Metric
              label="Ждут одобрения"
              value={String(analytics.pending_approval)}
              icon="shield"
              note="Черновики на проверке"
            />
            <Metric
              label="Первый ответ"
              value={
                analytics.median_first_response_seconds == null
                  ? "—"
                  : `${Math.round(analytics.median_first_response_seconds / 60)} мин`
              }
              icon="clock"
              note="Медиана до принятия провайдером"
            />
          </div>
          <div className="overview-secondary">
            <div className="distribution panel">
              <div className="section-inline">
                <h3>Приоритеты</h3>
                <span className="muted small">Заявки из базы данных</span>
              </div>
              <div
                className="distribution-bar"
                aria-label="Распределение приоритетов"
              >
                {["HOT", "WARM", "COLD", "unassessed"].map((key) => (
                  <span
                    key={key}
                    className={`segment-${key.toLowerCase()}`}
                    style={{ flex: analytics.temperatures[key] || 0 }}
                  />
                ))}
              </div>
              <div className="distribution-legend">
                {[
                  ["HOT", "HOT"],
                  ["WARM", "WARM"],
                  ["COLD", "COLD"],
                  ["unassessed", "Не оценено"],
                ].map(([key, label]) => (
                  <span key={key}>
                    <i className={`segment-${key.toLowerCase()}`} />
                    {label}
                    <b>{analytics.temperatures[key] || 0}</b>
                  </span>
                ))}
              </div>
            </div>
            <div className="attention-panel panel">
              <span className="icon-block">
                <Icon name={analytics.processing_errors ? "alert" : "check"} />
              </span>
              <div>
                <h3>
                  {analytics.processing_errors
                    ? `${analytics.processing_errors} ошибок обработки`
                    : "Состояние обработки"}
                </h3>
                <p>
                  {analytics.processing_errors
                    ? "Откройте карточки и проверьте причину перед повтором."
                    : "Ошибок обработки в текущих данных нет."}
                </p>
                <button
                  className="text-link"
                  onClick={() => {
                    setTemperature("");
                    setStage("");
                    setQuery("");
                  }}
                >
                  Перейти к заявкам
                  <Icon name="arrow" size={15} />
                </button>
              </div>
            </div>
          </div>
        </>
      )}
      {error && <ErrorNote text={error} retry={() => void load()} />}
      <section className="panel leads-panel">
        <div className="leads-panel-top">
          <div>
            <h2>
              {view === "overview" ? "Входящие заявки" : "Все обращения"}
              <span className="count-pill">{data?.total ?? "—"}</span>
            </h2>
            <p>
              {sort === "score"
                ? "Сначала заявки с высокой оценкой"
                : sort === "oldest"
                  ? "Старые обращения сверху"
                  : "Новые обращения сверху"}
            </p>
          </div>
          <div className="table-tools">
            <label className="sr-only" htmlFor="sort-filter">
              Сортировка
            </label>
            <select
              id="sort-filter"
              value={sort}
              onChange={(event) => {
                setSort(event.target.value);
                setPage(1);
              }}
            >
              <option value="newest">Сначала новые</option>
              <option value="oldest">Сначала старые</option>
              <option value="score">По оценке</option>
            </select>
            <label className="search-field">
              <Icon name="search" size={17} />
              <input
                aria-label="Поиск по заявкам"
                placeholder="Имя, почта, компания…"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
              />
            </label>
            <label className="sr-only" htmlFor="temperature-filter">
              Приоритет
            </label>
            <select
              id="temperature-filter"
              value={temperature}
              onChange={(event) => {
                setTemperature(event.target.value);
                setPage(1);
              }}
            >
              <option value="">Все приоритеты</option>
              <option value="HOT">HOT</option>
              <option value="WARM">WARM</option>
              <option value="COLD">COLD</option>
            </select>
            <label className="sr-only" htmlFor="stage-filter">
              Стадия
            </label>
            <select
              id="stage-filter"
              value={stage}
              onChange={(event) => {
                setStage(event.target.value);
                setPage(1);
              }}
            >
              <option value="">Все стадии</option>
              {Object.entries(stageNames).map(([key, name]) => (
                <option key={key} value={key}>
                  {name}
                </option>
              ))}
            </select>
          </div>
        </div>
        {busy ? (
          <Loading label="Загружаем заявки…" />
        ) : data?.items.length ? (
          <>
            <div className="table-scroll">
              <table className="lead-table">
                <thead>
                  <tr>
                    <th>Клиент / компания</th>
                    <th>Запрос</th>
                    <th>Приоритет</th>
                    <th>Стадия</th>
                    <th>Получено</th>
                    <th>
                      <span className="sr-only">Открыть</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {data.items.map((lead) => (
                    <tr key={lead.id}>
                      <td>
                        <div className="lead-person">
                          <span className="person-avatar">
                            {initials(lead.name)}
                          </span>
                          <div>
                            <button
                              className="lead-name"
                              onClick={() => onOpen(lead.id)}
                            >
                              {lead.name}
                            </button>
                            <span>{lead.company || lead.email}</span>
                          </div>
                        </div>
                      </td>
                      <td>
                        <span className="lead-summary">
                          {lead.summary ||
                            processNames[lead.processing_status] ||
                            lead.processing_status}
                        </span>
                        <span
                          className={`processing-label processing-${lead.processing_status}`}
                        >
                          {processNames[lead.processing_status] ||
                            lead.processing_status}
                        </span>
                      </td>
                      <td>
                        <div className="temperature-cell">
                          <Badge value={lead.temperature} />
                          <span className="score-number">
                            {lead.score == null ? "—" : lead.score}
                          </span>
                        </div>
                        {lead.priority_override && (
                          <small className="override-label">
                            Ручной: {lead.priority_override}
                          </small>
                        )}
                      </td>
                      <td>
                        <span className={`stage stage-${lead.sales_stage}`}>
                          {stageNames[lead.sales_stage]}
                        </span>
                      </td>
                      <td className="date-cell">
                        {dateTime(lead.created_at, timezone)}
                      </td>
                      <td>
                        <button
                          className="icon-button"
                          onClick={() => onOpen(lead.id)}
                          aria-label={`Открыть заявку ${lead.name}`}
                        >
                          <Icon name="chevron" size={18} />
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="pagination">
              <span>
                {(page - 1) * 10 + 1}–{Math.min(page * 10, data.total)} из{" "}
                {data.total}
              </span>
              <div>
                <button
                  className="icon-button"
                  aria-label="Предыдущая страница"
                  disabled={page === 1}
                  onClick={() => setPage(page - 1)}
                >
                  <Icon name="back" size={17} />
                </button>
                <span>Страница {page}</span>
                <button
                  className="icon-button"
                  aria-label="Следующая страница"
                  disabled={page * 10 >= data.total}
                  onClick={() => setPage(page + 1)}
                >
                  <Icon name="chevron" size={17} />
                </button>
              </div>
            </div>
          </>
        ) : (
          <Empty
            title={
              search || stage || temperature
                ? "По этим условиям ничего не найдено"
                : "Здесь появятся первые заявки"
            }
          >
            {search || stage || temperature
              ? "Попробуйте изменить поиск или сбросить фильтры."
              : "Отправьте обращение через публичную форму. Приём работает независимо от анализа."}
          </Empty>
        )}
      </section>
      {view === "overview" && analytics && (
        <AnalyticsPanels analytics={analytics} services={services} />
      )}
      {view === "overview" && analytics && (
        <div className="analytics-footnote">
          <Icon name="chart" size={17} />
          <p>
            Все показатели рассчитаны по сохранённым данным этого экземпляра.
            Успешные заявки: {analytics.won_conversion?.numerator ?? 0} из{" "}
            {analytics.won_conversion?.denominator ?? analytics.total}. Когорта
            — заявки за последние 30 дней; стадия на момент запроса.
          </p>
        </div>
      )}
      {view === "overview" && analytics?.technical && (
        <details className="technical-usage">
          <summary>Использование AI и оценка затрат</summary>
          <div className="usage-grid">
            <span>
              Вызовы AI<strong>{analytics.technical.ai_calls}</strong>
            </span>
            <span>
              Входные токены<strong>{analytics.technical.input_tokens}</strong>
            </span>
            <span>
              Выходные токены
              <strong>{analytics.technical.output_tokens}</strong>
            </span>
            <span>
              Usage неизвестен
              <strong>{analytics.technical.unknown_usage_calls} выз.</strong>
            </span>
          </div>
          <p>
            {analytics.technical.estimated_cost
              ? `Оценка: ${analytics.technical.estimated_cost.amount} ${analytics.technical.estimated_cost.currency}. Тариф: ${analytics.technical.estimated_cost.source}, на ${analytics.technical.estimated_cost.as_of}.`
              : "Оценка стоимости недоступна: тариф не настроен или нет подтверждённого usage."}
          </p>
        </details>
      )}
    </>
  );
}

function Metric({
  label,
  value,
  icon,
  note,
  accent = false,
}: {
  label: string;
  value: string;
  icon: string;
  note: string;
  accent?: boolean;
}) {
  return (
    <article className={`metric-card ${accent ? "metric-accent" : ""}`}>
      <div>
        <span>{label}</span>
        <Icon name={icon} size={18} />
      </div>
      <strong>{value}</strong>
      <p>{note}</p>
    </article>
  );
}

function LeadCard({
  id,
  config,
  onBack,
}: {
  id: string;
  config: PublicConfig | null;
  onBack: () => void;
}) {
  const [lead, setLead] = useState<LeadDetail | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [tab, setTab] = useState<"analysis" | "messages" | "history">(
    "analysis",
  );
  const [action, setAction] = useState<"priority" | "stage" | "stop" | null>(
    null,
  );
  const [reason, setReason] = useState("");
  const [choice, setChoice] = useState("");
  const [notice, setNotice] = useState("");
  const [optOut, setOptOut] = useState(false);
  const modalRef = useRef<HTMLElement>(null);
  const previousFocus = useRef<HTMLElement | null>(null);
  useEffect(() => {
    if (!action) return;
    const previous = previousFocus.current;
    const dialog = modalRef.current;
    if (!dialog) return;
    const trap = (event: KeyboardEvent) => {
      if (event.key !== "Tab") return;
      const elements = Array.from(
        dialog.querySelectorAll<HTMLElement>(
          "button:not(:disabled),input:not(:disabled),select:not(:disabled),textarea:not(:disabled),a[href]",
        ),
      );
      const first = elements[0];
      const last = elements.at(-1);
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last?.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first?.focus();
      }
    };
    dialog.addEventListener("keydown", trap);
    return () => {
      dialog.removeEventListener("keydown", trap);
      previous?.focus();
    };
  }, [action]);
  const load = useCallback(
    () =>
      api<LeadDetail>(`/admin/leads/${id}`)
        .then(setLead)
        .catch((err) => setError(err.message)),
    [id],
  );
  useEffect(() => {
    void load();
  }, [load]);
  async function perform(path: string, body: unknown, method = "POST") {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await mutation(path, body, method);
      await load();
      setAction(null);
      setReason("");
      setNotice("Изменения сохранены в истории.");
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Не удалось выполнить действие.",
      );
    } finally {
      setBusy(false);
    }
  }
  if (!lead)
    return error ? (
      <ErrorNote text={error} retry={() => void load()} />
    ) : (
      <Loading />
    );
  function openAction(next: "priority" | "stage" | "stop") {
    previousFocus.current = document.activeElement as HTMLElement | null;
    setAction(next);
    setOptOut(false);
    setReason("");
    setChoice(
      next === "stage" ? lead!.sales_stage : lead!.priority_override || "HOT",
    );
  }
  return (
    <>
      <button className="back-link" onClick={onBack}>
        <Icon name="back" size={17} />К списку заявок
      </button>
      <div className="page-heading lead-heading">
        <div className="lead-title">
          <span className="large-avatar">{initials(lead.name)}</span>
          <div>
            <span className="eyebrow">
              ЗАЯВКА {lead.reference || lead.id.slice(0, 8)}
            </span>
            <h1>{lead.name}</h1>
            <p>
              {lead.company || "Компания не указана"}
              <span>·</span>
              {dateTime(lead.created_at, config?.timezone)}
            </p>
          </div>
        </div>
        <div className="lead-heading-actions">
          <Badge value={lead.temperature} />
          <button
            className="button secondary"
            onClick={() => void load()}
            disabled={busy}
            aria-label="Обновить карточку"
          >
            <Icon name="refresh" size={17} />
          </button>
        </div>
      </div>
      {error && <ErrorNote text={error} />}
      {notice && (
        <div className="notice success" role="status">
          <Icon name="check" size={18} />
          {notice}
        </div>
      )}
      <div className="card-layout">
        <aside className="lead-sidebar">
          <article className="panel contact-details">
            <span className="tiny-label">КОНТАКТЫ</span>
            <a href={`mailto:${lead.email}`}>
              <Icon name="mail" size={17} />
              {lead.email}
            </a>
            {lead.phone && (
              <span>
                <Icon name="user" size={17} />
                {lead.phone}
              </span>
            )}
            <hr />
            <div className="detail-row">
              <span>Стадия</span>
              <span className={`stage stage-${lead.sales_stage}`}>
                {stageNames[lead.sales_stage]}
              </span>
            </div>
            <button className="text-button" onClick={() => openAction("stage")}>
              Изменить стадию
            </button>
            <div className="detail-row">
              <span>Обработка</span>
              <strong>
                {processNames[lead.processing_status] || lead.processing_status}
              </strong>
            </div>
            <div className="detail-row">
              <span>Источник</span>
              <strong>
                {(
                  {
                    website: "Сайт",
                    demo: "Демонстрация",
                    manual: "Вручную",
                  } as Record<string, string>
                )[lead.source] ||
                  lead.source ||
                  "—"}
              </strong>
            </div>
            <hr />
            <div className="detail-row">
              <span>Приоритет</span>
              <strong>{lead.priority_override || "По анализу"}</strong>
            </div>
            <button
              className="text-button"
              onClick={() => openAction("priority")}
            >
              Назначить вручную
            </button>
            <button
              className="button secondary full"
              onClick={() => void perform(`/admin/leads/${id}/reanalyze`, {})}
              disabled={busy}
            >
              <Icon name="spark" size={17} />
              Повторить анализ
            </button>
          </article>
          <article className="panel communication-settings">
            <span className="tiny-label">КОММУНИКАЦИЯ</span>
            <p>
              {lead.communication_stopped || lead.opted_out
                ? "Дальнейшие сообщения остановлены."
                : "Отправка возможна после одобрения актуальной версии."}
            </p>
            {lead.followup_status && (
              <p className="small muted">
                Повторное письмо:{" "}
                {(
                  {
                    not_scheduled: "не запланировано",
                    scheduled: "запланировано",
                    pending_approval: "ждёт одобрения",
                    queued: "в очереди",
                    completed: "завершено",
                    needs_review: "нужна проверка",
                    cancelled: "отменено",
                  } as Record<string, string>
                )[lead.followup_status] || lead.followup_status}
                {lead.followup_due_at &&
                  ` · ${dateTime(lead.followup_due_at, config?.timezone)}`}
              </p>
            )}
            {lead.followup_status === "needs_review" && (
              <FollowupReview leadId={id} onChange={load} />
            )}
            <button
              className="text-button danger"
              onClick={() => openAction("stop")}
              disabled={busy || lead.communication_stopped || lead.opted_out}
            >
              <Icon name="stop" size={16} />
              Остановить сообщения
            </button>
          </article>
        </aside>
        <div className="lead-main">
          <article className="panel source-panel">
            <div className="panel-heading">
              <div>
                <span className="eyebrow">ИСХОДНЫЙ ЗАПРОС</span>
                <h3>С чего начался разговор</h3>
              </div>
              <Icon name="inbox" />
            </div>
            <p className="original-message">{lead.original_message}</p>
          </article>
          <nav className="tabs" aria-label="Данные заявки">
            {(
              [
                { id: "analysis", label: "Анализ и приоритет", icon: "spark" },
                { id: "messages", label: "Переписка", icon: "mail" },
                { id: "history", label: "История", icon: "clock" },
              ] as const
            ).map((item) => (
              <button
                key={item.id}
                className={tab === item.id ? "active" : ""}
                aria-pressed={tab === item.id}
                onClick={() => setTab(item.id)}
              >
                <Icon name={item.icon} size={17} />
                {item.label}
                {item.id === "messages" && lead.messages.length > 0 && (
                  <span>{lead.messages.length}</span>
                )}
              </button>
            ))}
          </nav>
          {tab === "analysis" && <AnalysisView analysis={lead.analysis} />}
          {tab === "messages" && (
            <div className="message-list">
              {lead.messages.length ? (
                lead.messages.map((message) => (
                  <MessageCard
                    key={`${message.id}-${message.current_version?.id}-${message.state}`}
                    message={message}
                    timezone={config?.timezone}
                    onChange={load}
                    communicationStopped={
                      lead.communication_stopped ||
                      lead.opted_out ||
                      ["won", "lost"].includes(lead.sales_stage)
                    }
                  />
                ))
              ) : (
                <article className="panel">
                  <Empty title="Переписка ещё не началась">
                    После анализа здесь появится черновик по шаблону для
                    проверки менеджером.
                  </Empty>
                </article>
              )}
            </div>
          )}
          {tab === "history" && (
            <article className="panel history-panel">
              <h3>История решений</h3>
              {lead.audit_events.length ? (
                <ol className="timeline">
                  {lead.audit_events.map((event, index) => (
                    <li key={event.id || index}>
                      <span className="timeline-dot" />
                      <div>
                        <strong>{auditLabel(event.kind)}</strong>
                        <p>
                          {event.actor || "Система"} ·{" "}
                          {dateTime(event.created_at, config?.timezone)}
                        </p>
                        {event.detail &&
                          Object.entries(event.detail).map(([key, value]) => (
                            <span className="audit-detail" key={key}>
                              {auditDetailLabel(key)}:{" "}
                              {typeof value === "object"
                                ? JSON.stringify(value)
                                : String(value)}
                            </span>
                          ))}
                      </div>
                    </li>
                  ))}
                </ol>
              ) : (
                <Empty title="Нет событий для отображения" />
              )}
            </article>
          )}
        </div>
      </div>
      {action && (
        <div
          className="modal-backdrop"
          onClick={(event) => {
            if (event.target === event.currentTarget && !busy) setAction(null);
          }}
        >
          <section
            className="modal"
            ref={modalRef}
            role="dialog"
            aria-modal="true"
            aria-labelledby="action-title"
            onKeyDown={(event) => {
              if (event.key === "Escape" && !busy) setAction(null);
            }}
          >
            <button
              className="modal-close icon-button"
              aria-label="Закрыть"
              onClick={() => setAction(null)}
              disabled={busy}
            >
              <Icon name="close" />
            </button>
            <span className="icon-block indigo">
              <Icon name={action === "stop" ? "stop" : "user"} />
            </span>
            <h2 id="action-title">
              {action === "priority"
                ? "Ручной приоритет"
                : action === "stage"
                  ? "Коммерческая стадия"
                  : "Остановить сообщения"}
            </h2>
            <p className="muted">
              {action === "stop"
                ? "Запланированные сообщения будут отменены. Причина сохранится в истории заявки."
                : "Изменение сохранится с вашей причиной. Результат AI-анализа останется в истории."}
            </p>
            <form
              onSubmit={(event) => {
                event.preventDefault();
                void perform(
                  action === "stop"
                    ? `/admin/leads/${id}/communication-stop`
                    : `/admin/leads/${id}`,
                  action === "stop"
                    ? { reason, opt_out: optOut }
                    : {
                        expected_version: lead.version,
                        reason,
                        ...(action === "priority"
                          ? {
                              priority_override:
                                choice === "auto" ? null : choice,
                            }
                          : { sales_stage: choice as Stage }),
                      },
                  action === "stop" ? "POST" : "PATCH",
                );
              }}
            >
              {action !== "stop" && (
                <label className="field">
                  <span>{action === "priority" ? "Приоритет" : "Стадия"}</span>
                  <select
                    autoFocus
                    value={choice}
                    onChange={(event) => setChoice(event.target.value)}
                  >
                    {action === "priority" ? (
                      <>
                        <option value="HOT">HOT — высокий</option>
                        <option value="WARM">WARM — средний</option>
                        <option value="COLD">COLD — низкий</option>
                        <option value="auto">По результату анализа</option>
                      </>
                    ) : (
                      Object.entries(stageNames).map(([key, label]) => (
                        <option key={key} value={key}>
                          {label}
                        </option>
                      ))
                    )}
                  </select>
                </label>
              )}
              {action === "stop" && (
                <label className="checkbox-field">
                  <input
                    type="checkbox"
                    checked={optOut}
                    onChange={(event) => setOptOut(event.target.checked)}
                  />
                  <span>
                    Клиент попросил больше не писать (opt-out для этого адреса)
                  </span>
                </label>
              )}
              <label className="field">
                <span>Причина изменения</span>
                <textarea
                  autoFocus={action === "stop"}
                  required
                  minLength={3}
                  maxLength={500}
                  rows={3}
                  value={reason}
                  onChange={(event) => setReason(event.target.value)}
                  placeholder="Что стало основанием для решения?"
                />
              </label>
              <div className="modal-actions">
                <button
                  className="button secondary"
                  type="button"
                  onClick={() => setAction(null)}
                  disabled={busy}
                >
                  Отмена
                </button>
                <button
                  className={`button ${action === "stop" ? "danger-button" : "primary"}`}
                  disabled={busy}
                >
                  {busy ? "Сохраняем…" : "Сохранить решение"}
                </button>
              </div>
            </form>
          </section>
        </div>
      )}
    </>
  );
}

function MessageCard({
  message,
  timezone,
  onChange,
  communicationStopped,
}: {
  message: Message;
  timezone?: string;
  onChange: () => Promise<unknown>;
  communicationStopped?: boolean;
}) {
  const current = message.current_version;
  const [editing, setEditing] = useState(false);
  const [subject, setSubject] = useState(
    current?.subject || message.subject || "",
  );
  const [body, setBody] = useState(current?.body || message.body || "");
  const [recipient, setRecipient] = useState(
    current?.recipient || message.recipient || "",
  );
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [confirm, setConfirm] = useState<"approve" | "reject" | null>(null);
  const [reason, setReason] = useState("");
  const [decisionKey, setDecisionKey] = useState(() => crypto.randomUUID());
  const incoming = message.direction === "inbound";
  const canEdit =
    !incoming &&
    [
      "draft",
      "pending_approval",
      "approved",
      "queued",
      "rejected",
      "failed",
    ].includes(message.state);
  const canDecide =
    !incoming &&
    ["draft", "pending_approval"].includes(message.state) &&
    !editing &&
    current &&
    !communicationStopped;
  async function request(path: string, payload: unknown, key?: string) {
    setBusy(true);
    setError("");
    try {
      await mutation(path, payload, "POST", key);
      setEditing(false);
      setConfirm(null);
      await onChange();
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Не удалось сохранить сообщение.",
      );
    } finally {
      setBusy(false);
    }
  }
  function save(event: FormEvent) {
    event.preventDefault();
    void request(`/admin/messages/${message.id}/versions`, {
      subject,
      body,
      recipient,
      expected_version: current?.revision || message.version || 1,
    });
  }
  return (
    <article className="panel message-card">
      <div className="panel-heading">
        <div className="inline-heading">
          <span className={`icon-block ${incoming ? "green" : "indigo"}`}>
            <Icon name="mail" />
          </span>
          <div>
            <h3>
              {incoming
                ? "Ответ клиента"
                : message.kind === "followup"
                  ? "Повторное обращение"
                  : "Ответ на заявку"}
            </h3>
            <span className="muted small">
              {dateTime(message.created_at, timezone)}
              {current && ` · Версия ${current.revision}`}
              {!incoming && (current?.generation?.provider === "openai"
                ? ` · REAL · AI draft: ${current.generation.model}`
                : current?.generation?.provider === "operator"
                  ? " · Версия оператора"
                  : " · Основа: черновик по шаблону · SIMULATED")}
            </span>
          </div>
        </div>
        <span className={`message-state state-${message.state}`}>
          {messageNames[message.state] || message.state}
        </span>
      </div>
      {message.state === "failed" && (
        <div className="notice warning">
          <Icon name="alert" />
          <p>
            Предыдущая попытка завершилась ошибкой. Для повторной отправки
            создайте новую версию через «Редактировать», проверьте её и одобрите
            заново. Повтор прежнего одобрения не отправит письмо.
          </p>
        </div>
      )}
      {message.state === "delivery_unknown" && (
        <div className="notice warning">
          <Icon name="alert" />
          <p>
            Провайдер мог принять письмо. Автоматическая повторная отправка
            запрещена. Проверьте исходящую почту и историю отправки.
          </p>
        </div>
      )}
      {error && <ErrorNote text={error} />}
      <form onSubmit={save}>
        <div className="message-envelope">
          <label className="field">
            <span>{incoming ? "От кого" : "Кому"}</span>
            {editing ? (
              <input
                type="email"
                required
                value={recipient}
                readOnly
                title="Получатель совпадает с email заявки"
              />
            ) : (
              <div className="readonly-value">
                {incoming ? message.sender || recipient : recipient}
              </div>
            )}
          </label>
          <label className="field">
            <span>Тема</span>
            {editing ? (
              <input
                required
                maxLength={250}
                value={subject}
                onChange={(event) => setSubject(event.target.value)}
              />
            ) : (
              <div className="readonly-value subject-line">
                {subject || "Без темы"}
              </div>
            )}
          </label>
        </div>
        {editing ? (
          <label className="field">
            <span>Текст письма</span>
            <textarea
              required
              rows={12}
              maxLength={12000}
              value={body}
              onChange={(event) => setBody(event.target.value)}
            />
          </label>
        ) : (
          <p className="message-body">{body}</p>
        )}
        {editing && (
          <>
            <div className="notice compact">
              Сохранение новой версии отменит прежнее одобрение.
            </div>
            <div className="message-actions">
              <button
                type="button"
                className="button secondary"
                onClick={() => {
                  setEditing(false);
                  setSubject(current?.subject || "");
                  setBody(current?.body || "");
                  setRecipient(current?.recipient || "");
                }}
              >
                Отмена
              </button>
              <button className="button primary" disabled={busy}>
                {busy ? "Сохраняем…" : "Сохранить новую версию"}
              </button>
            </div>
          </>
        )}
        {!editing && (
          <div className="message-actions">
            {canEdit && (
              <button
                type="button"
                className="button secondary"
                onClick={() => setEditing(true)}
                disabled={busy}
              >
                Редактировать
              </button>
            )}
            {canDecide && (
              <>
                <button
                  type="button"
                  className="button secondary"
                  onClick={() => {
                    setConfirm("reject");
                    setDecisionKey(crypto.randomUUID());
                    setReason("");
                  }}
                  disabled={busy}
                >
                  Отклонить
                </button>
                <button
                  type="button"
                  className="button primary"
                  onClick={() => {
                    setConfirm("approve");
                    setDecisionKey(crypto.randomUUID());
                    setReason("");
                  }}
                  disabled={busy}
                >
                  <Icon name="check" size={17} />
                  Проверить и одобрить
                </button>
              </>
            )}
            {message.provider_accepted_at && (
              <span className="muted small">
                Принято провайдером{" "}
                {dateTime(message.provider_accepted_at, timezone)}. Доставка не
                подтверждена.
              </span>
            )}
          </div>
        )}
      </form>
      {confirm && (
        <div className="approval-confirm">
          <h4>
            {confirm === "approve"
              ? `Одобрить отправку версии ${current?.revision}?`
              : "Отклонить этот черновик?"}
          </h4>
          <p>
            {confirm === "approve"
              ? `Получатель: ${recipient}. Подтверждение разрешит отправку именно показанных темы и текста.`
              : "Укажите, что нужно изменить. Решение останется в истории."}
          </p>
          <label className="field">
            <span>
              Комментарий
              {confirm === "reject" ? " (обязательно)" : " (необязательно)"}
            </span>
            <textarea
              rows={2}
              value={reason}
              onChange={(event) => setReason(event.target.value)}
            />
          </label>
          <div className="message-actions">
            <button
              className="button secondary"
              onClick={() => setConfirm(null)}
              disabled={busy}
            >
              Отмена
            </button>
            <button
              className="button primary"
              disabled={
                busy || (confirm === "reject" && reason.trim().length < 3)
              }
              onClick={() =>
                void request(
                  `/admin/messages/${message.id}/decisions`,
                  {
                    version_id: current?.id,
                    decision: confirm,
                    reason: reason || "",
                  },
                  decisionKey,
                )
              }
            >
              {busy
                ? "Сохраняем решение…"
                : confirm === "approve"
                  ? "Одобрить и поставить в очередь"
                  : "Отклонить версию"}
            </button>
          </div>
        </div>
      )}
    </article>
  );
}

function Integrations({ timezone }: { timezone?: string }) {
  const [data, setData] = useState<Integration[] | null>(null);
  const [error, setError] = useState("");
  const load = () => {
    setError("");
    api<Integration[]>("/admin/integrations")
      .then(setData)
      .catch((err) => setError(err.message));
  };
  useEffect(load, []);
  const names: Record<string, string> = {
    postgres: "PostgreSQL",
    database: "PostgreSQL",
    n8n: "n8n · оркестрация",
    openai: "OpenAI · анализ",
    ai: "AI · анализ",
    telegram: "Telegram · уведомления",
    smtp: "SMTP · исходящая почта",
    imap: "IMAP · входящая почта",
    worker: "Исполнитель заданий",
  };
  return (
    <>
      <div className="page-heading">
        <div>
          <span className="eyebrow">СОСТОЯНИЕ СИСТЕМЫ</span>
          <h1>Интеграции</h1>
          <p>
            Настройка подключения и фактическая проверка — разные состояния.
          </p>
        </div>
        <button className="button secondary" onClick={load}>
          <Icon name="refresh" size={17} />
          Обновить
        </button>
      </div>
      {error ? (
        <ErrorNote text={error} retry={load} />
      ) : !data ? (
        <Loading />
      ) : (
        <div className="integration-grid">
          {data.map((item) => (
            <article className="panel integration-card" key={item.name}>
              <div className="panel-heading">
                <span className="icon-block">
                  <Icon
                    name={
                      item.name === "smtp" || item.name === "imap"
                        ? "mail"
                        : item.name === "ai" || item.name === "openai"
                          ? "spark"
                          : "plug"
                    }
                  />
                </span>
                <span
                  className={`integration-state integration-${item.state.toLowerCase()}`}
                >
                  {(
                    {
                      simulated: "Симуляция",
                      unverified: "Не проверено",
                      not_configured: "Не настроено",
                      stale: "Данные устарели",
                      healthy: "Работает",
                      ok: "Доступно",
                      error: "Ошибка",
                      verified: "Проверено",
                    } as Record<string, string>
                  )[item.state] || item.state}
                </span>
              </div>
              <h3>{names[item.name] || item.name}</h3>
              <p>{item.detail || "Дополнительных сведений нет."}</p>
              <div className="integration-last">
                Последняя успешная проверка
                <strong>
                  {dateTime(item.last_success_at || undefined, timezone)}
                </strong>
              </div>
            </article>
          ))}
        </div>
      )}
      <InboxReview timezone={timezone} />
      <div className="notice">
        <Icon name="shield" />
        <p>
          Ключи и пароли не показываются в кабинете. Live-интеграция считается
          проверенной только после разрешённого теста с настоящим сервисом.
          Fake-адаптеры относятся только к demo.
        </p>
      </div>
    </>
  );
}

function auditLabel(kind: string) {
  const names: Record<string, string> = {
    lead_created: "Заявка принята",
    lead_received: "Заявка принята",
    analysis_completed: "Анализ завершён",
    analysis_failed: "Ошибка анализа",
    message_drafted: "Черновик по шаблону подготовлен",
    message_approved: "Версия сообщения одобрена",
    message_rejected: "Версия отклонена",
    message_edited: "Создана новая версия",
    provider_accepted: "Письмо принято провайдером",
    stage_changed: "Коммерческая стадия изменена",
    priority_changed: "Приоритет изменён",
    communication_stopped: "Коммуникация остановлена",
    reply_received: "Ответ клиента получен",
    followup_cancelled: "Повторное обращение отменено",
    reanalyze_requested: "Запрошен повторный анализ",
  };
  const aliases: Record<string, string> = {
    "lead.created": "lead_created",
    "analysis.completed": "analysis_completed",
    "analysis.failed": "analysis_failed",
    "analysis.requested": "reanalyze_requested",
    "message.drafted": "message_drafted",
    "message.edited": "message_edited",
    "message.approve": "message_approved",
    "message.reject": "message_rejected",
    "communication.stopped": "communication_stopped",
    "followup.cancelled": "followup_cancelled",
    "reply.received": "reply_received",
    "email.accepted": "provider_accepted",
  };
  if (kind === "lead.updated") return "Данные заявки изменены";
  return (
    names[aliases[kind] || kind] ||
    kind.replaceAll("_", " ").replaceAll(".", " · ")
  );
}
function auditDetailLabel(key: string) {
  return (
    (
      {
        reason: "Причина",
        from: "Было",
        to: "Стало",
        version_id: "Версия",
        sales_stage: "Стадия",
        priority_override: "Ручной приоритет",
      } as Record<string, string>
    )[key] || key
  );
}
