import type { LeadSubmission } from "./types";

let csrfToken = "";
export const setCsrf = (token: string) => {
  csrfToken = token;
};
export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    public code?: string,
    public fields?: Record<string, string>,
  ) {
    super(message);
  }
}

export function errorText(status: number, payload: unknown): string {
  if (status === 401) return "Сессия завершена. Войдите в кабинет ещё раз.";
  if (status === 403)
    return "Недостаточно прав для этого действия. Обновите страницу и войдите снова.";
  if (status === 409)
    return "Данные уже изменились. Обновите карточку перед повторным действием.";
  if (status === 429)
    return "Слишком много запросов. Подождите немного и повторите попытку.";
  if (status >= 500)
    return "Сервис временно недоступен. Ваш запрос можно повторить позже.";
  if (status === 422)
    return "Проверьте заполненные поля. Не все данные подходят для отправки.";
  if (
    payload &&
    typeof payload === "object" &&
    "message" in payload &&
    typeof payload.message === "string"
  )
    return payload.message;
  return "Не удалось выполнить действие. Попробуйте ещё раз.";
}

export async function api<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const headers = new Headers(options.headers);
  if (options.body) headers.set("Content-Type", "application/json");
  if (csrfToken && options.method && options.method !== "GET")
    headers.set("X-CSRF-Token", csrfToken);
  let response: Response;
  try {
    response = await fetch(`/api/v1${path}`, {
      ...options,
      headers,
      credentials: "same-origin",
    });
  } catch {
    throw new ApiError(
      0,
      "Нет связи с сервисом. Проверьте подключение и повторите попытку.",
    );
  }
  const payload: unknown =
    response.status === 204
      ? undefined
      : await response.json().catch(() => undefined);
  if (!response.ok) {
    const fields: Record<string, string> = {};
    if (
      response.status === 422 &&
      payload &&
      typeof payload === "object" &&
      "detail" in payload &&
      Array.isArray(payload.detail)
    ) {
      const labels: Record<string, string> = {
        name: "Проверьте имя: от 2 до 120 символов.",
        email: "Введите корректный адрес почты с допустимым доменом.",
        phone: "Проверьте телефон: не больше 40 символов.",
        company: "Проверьте компанию: не больше 160 символов.",
        message: "Опишите задачу: от 10 до 8 000 символов.",
      };
      for (const issue of payload.detail) {
        if (
          issue &&
          typeof issue === "object" &&
          "loc" in issue &&
          Array.isArray(issue.loc)
        ) {
          const field = String(issue.loc.at(-1));
          if (labels[field]) fields[field] = labels[field];
        }
      }
    }
    throw new ApiError(
      response.status,
      path === "/auth/login" && response.status === 401
        ? "Почта или пароль не подошли. Проверьте данные для входа."
        : errorText(response.status, payload),
      undefined,
      fields,
    );
  }
  return payload as T;
}

export const mutation = <T>(
  path: string,
  body: unknown,
  method = "POST",
  key?: string,
) =>
  api<T>(path, {
    method,
    body: JSON.stringify(body),
    headers: key ? { "Idempotency-Key": key } : undefined,
  });

export function validateLead(values: {
  name: string;
  email: string;
  message: string;
  phone?: string;
  company?: string;
}): Record<string, string> {
  const errors: Record<string, string> = {};
  if (values.name.trim().length < 2 || values.name.trim().length > 120)
    errors.name = "Введите имя: от 2 до 120 символов.";
  if (
    !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(values.email.trim()) ||
    values.email.length > 254
  )
    errors.email = "Введите корректный адрес электронной почты.";
  if (values.message.trim().length < 10 || values.message.length > 8000)
    errors.message = "Опишите задачу: от 10 до 8 000 символов.";
  if ((values.phone?.length ?? 0) > 40) errors.phone = "Не больше 40 символов.";
  if ((values.company?.length ?? 0) > 160)
    errors.company = "Не больше 160 символов.";
  return errors;
}

export function intakeKey(
  previous: { payload: string; key: string } | null,
  body: LeadSubmission,
  newKey: () => string = () => crypto.randomUUID(),
) {
  const payload = JSON.stringify(body);
  return previous?.payload === payload ? previous : { payload, key: newKey() };
}
