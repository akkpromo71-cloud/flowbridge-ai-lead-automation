import type { ReactNode } from "react";
import type { Temperature } from "./types";

export function Icon({
  name,
  size = 20,
  ...props
}: {
  name: string;
  size?: number;
  className?: string;
}) {
  const shapes: Record<string, ReactNode> = {
    arrow: (
      <>
        <path d="M5 12h14M13 6l6 6-6 6" />
      </>
    ),
    chevron: <path d="m9 5 7 7-7 7" />,
    back: <path d="m14 6-6 6 6 6" />,
    check: <path d="m5 12 4 4L19 6" />,
    grid: (
      <>
        <rect x="3" y="3" width="7" height="7" rx="1.5" />
        <rect x="14" y="3" width="7" height="7" rx="1.5" />
        <rect x="3" y="14" width="7" height="7" rx="1.5" />
        <rect x="14" y="14" width="7" height="7" rx="1.5" />
      </>
    ),
    inbox: (
      <>
        <path d="M4 4h16l2 11v5H2v-5L4 4Z" />
        <path d="M2 14h6l2 3h4l2-3h6" />
      </>
    ),
    spark: (
      <>
        <path d="m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5L12 3Z" />
      </>
    ),
    shield: (
      <>
        <path d="M12 3 4 6v6c0 5 8 9 8 9s8-4 8-9V6l-8-3Z" />
        <path d="m8 12 3 3 5-6" />
      </>
    ),
    mail: (
      <>
        <rect x="3" y="5" width="18" height="14" rx="3" />
        <path d="m3 6 9 7 9-7" />
      </>
    ),
    clock: (
      <>
        <circle cx="12" cy="12" r="9" />
        <path d="M12 7v5l3 2" />
      </>
    ),
    chart: (
      <>
        <path d="M4 3v17h17M8 16v-4M13 16V8M18 16V5" />
      </>
    ),
    plug: (
      <>
        <path d="M8 3v5m8-5v5M6 8h12v4a6 6 0 0 1-12 0V8ZM12 18v4" />
      </>
    ),
    search: (
      <>
        <circle cx="10" cy="10" r="6" />
        <path d="m15 15 5 5" />
      </>
    ),
    logout: (
      <>
        <path d="M9 3H4v18h5M10 12h11m-4-4 4 4-4 4" />
      </>
    ),
    user: (
      <>
        <circle cx="12" cy="8" r="4" />
        <path d="M4 21v-2a8 8 0 0 1 16 0v2" />
      </>
    ),
    lock: (
      <>
        <rect x="5" y="10" width="14" height="11" rx="2" />
        <path d="M8 10V7a4 4 0 0 1 8 0v3M12 14v3" />
      </>
    ),
    refresh: (
      <>
        <path d="M20 7v5h-5M4 17v-5h5" />
        <path d="M6 7a7 7 0 0 1 12-2l2 3M4 16l2 3a7 7 0 0 0 12-2" />
      </>
    ),
    alert: (
      <>
        <path d="m12 3 10 18H2L12 3Z" />
        <path d="M12 9v5m0 3v.1" />
      </>
    ),
    stop: (
      <>
        <circle cx="12" cy="12" r="9" />
        <path d="m6 6 12 12" />
      </>
    ),
    book: (
      <>
        <path d="M12 5v16M3 4c4-1 7 0 9 2 2-2 5-3 9-2v15c-4-1-7 0-9 2-2-2-5-3-9-2V4Z" />
      </>
    ),
    close: <path d="m6 6 12 12M6 18 18 6" />,
  };
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.65"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      {...props}
    >
      {shapes[name] || shapes.spark}
    </svg>
  );
}
export function Logo({ inverse = false }: { inverse?: boolean }) {
  return (
    <a
      href="#/"
      className={`logo ${inverse ? "inverse" : ""}`}
      aria-label="LeadFlow — на главную"
    >
      <span className="logo-mark">
        <span />
        <span />
        <span />
      </span>
      <span>
        lead<span className="logo-thin">flow</span>
        <small>AI LEAD AUTOMATION</small>
      </span>
    </a>
  );
}
export function Badge({ value }: { value: Temperature | string | null }) {
  return (
    <span className={`badge badge-${(value || "none").toLowerCase()}`}>
      <span className="badge-dot" />
      {value || "Не оценено"}
    </span>
  );
}
export function Loading({ label = "Загружаем данные…" }: { label?: string }) {
  return (
    <div className="loading" role="status">
      <span className="spinner" />
      {label}
    </div>
  );
}
export function ErrorNote({
  text,
  retry,
}: {
  text: string;
  retry?: () => void;
}) {
  return (
    <div className="notice error" role="alert">
      <Icon name="alert" />
      <div>
        {text}
        {retry && (
          <button className="text-button" onClick={retry}>
            Повторить
          </button>
        )}
      </div>
    </div>
  );
}
export function Empty({
  title,
  children,
}: {
  title: string;
  children?: ReactNode;
}) {
  return (
    <div className="empty">
      <span className="empty-icon">
        <Icon name="inbox" size={30} />
      </span>
      <h3>{title}</h3>
      {children && <p>{children}</p>}
    </div>
  );
}
export const stageNames: Record<string, string> = {
  new: "Новая",
  contacted: "Связались",
  replied: "Получен ответ",
  meeting_booked: "Встреча",
  won: "Успешно",
  lost: "Закрыта",
};
export const processNames: Record<string, string> = {
  pending: "Ожидает обработки",
  processing: "Анализируем",
  completed: "Анализ готов",
  needs_review: "Нужна проверка",
  failed: "Ошибка обработки",
};
export const messageNames: Record<string, string> = {
  draft: "Черновик",
  pending_approval: "Ждёт одобрения",
  approved: "Одобрено",
  queued: "В очереди",
  sending: "Отправляется",
  provider_accepted: "Принято провайдером",
  rejected: "Отклонено",
  cancelled: "Отменено",
  failed: "Ошибка",
  delivery_unknown: "Результат отправки неизвестен",
  received: "Получено",
};
export function dateTime(
  value: string | undefined,
  timezone = "Asia/Qyzylorda",
) {
  if (!value) return "—";
  const parsed = new Date(value);
  return Number.isNaN(parsed.valueOf())
    ? "—"
    : new Intl.DateTimeFormat("ru-RU", {
        day: "numeric",
        month: "short",
        hour: "2-digit",
        minute: "2-digit",
        timeZone: timezone,
      }).format(parsed);
}
export function initials(name: string) {
  return name
    .trim()
    .split(/\s+/)
    .slice(0, 2)
    .map((part) => part[0])
    .join("")
    .toUpperCase();
}
