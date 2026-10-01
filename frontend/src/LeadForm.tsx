import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import {
  api,
  ApiError,
  intakeKey,
  mutation,
  setCsrf,
  validateLead,
} from "./api";
import type { LeadSubmission, PublicConfig, Session } from "./types";
import { ErrorNote, Icon, Loading } from "./ui";

export function LeadForm({ config }: { config: PublicConfig }) {
  const isolated = config.mode === "demo" || config.mode === "controlled";
  const [values, setValues] = useState({
    name: "",
    email: "",
    company: "",
    phone: "",
    message: "",
  });
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [reference, setReference] = useState("");
  const [access, setAccess] = useState<
    "checking" | "allowed" | "required" | "error"
  >(isolated ? "checking" : "allowed");
  const [accessError, setAccessError] = useState("");
  const [accessAttempt, setAccessAttempt] = useState(0);
  const attempt = useRef<{ payload: string; key: string } | null>(null);
  const firstInvalid = useRef<HTMLFormElement>(null);
  useEffect(() => {
    if (!isolated) return;
    let active = true;
    setAccess("checking");
    setAccessError("");
    api<Session>("/auth/me")
      .then((session) => {
        if (!active) return;
        setCsrf(session.csrf_token);
        setAccess("allowed");
      })
      .catch((err: unknown) => {
        if (!active) return;
        setCsrf("");
        if (err instanceof ApiError && err.status === 401) {
          setAccess("required");
        } else {
          setAccessError(
            err instanceof Error ? err.message : "Не удалось проверить доступ.",
          );
          setAccess("error");
        }
      });
    return () => {
      active = false;
    };
  }, [isolated, accessAttempt]);
  async function submit(event: FormEvent) {
    event.preventDefault();
    const nextErrors = validateLead(values);
    setErrors(nextErrors);
    setError("");
    if (Object.keys(nextErrors).length) {
      setTimeout(
        () =>
          firstInvalid.current
            ?.querySelector<HTMLElement>('[aria-invalid="true"]')
            ?.focus(),
        0,
      );
      return;
    }
    const params = new URLSearchParams(window.location.search);
    const utm = Object.fromEntries(
      [
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_content",
        "utm_term",
      ].flatMap((key) =>
        params.has(key) ? [[key, params.get(key)!.slice(0, 200)]] : [],
      ),
    );
    const body: LeadSubmission = {
      name: values.name.trim(),
      email: values.email.trim(),
      company: values.company.trim() || null,
      phone: values.phone.trim() || null,
      message: values.message.trim(),
      utm,
    };
    attempt.current = intakeKey(attempt.current, body);
    setBusy(true);
    try {
      const result = await mutation<{ reference: string }>(
        "/public/leads",
        body,
        "POST",
        attempt.current.key,
      );
      setReference(result.reference);
    } catch (err) {
      if (
        isolated &&
        err instanceof ApiError &&
        err.status === 401
      ) {
        setCsrf("");
        setAccess("required");
      }
      setError(
        err instanceof Error ? err.message : "Не удалось отправить заявку.",
      );
      if (
        err instanceof ApiError &&
        err.fields &&
        Object.keys(err.fields).length
      ) {
        setErrors(err.fields);
        setTimeout(
          () =>
            firstInvalid.current
              ?.querySelector<HTMLElement>('[aria-invalid="true"]')
              ?.focus(),
          0,
        );
      }
    } finally {
      setBusy(false);
    }
  }
  if (isolated && access !== "allowed") {
    if (access === "checking")
      return <Loading label="Проверяем доступ к тестовой форме…" />;
    if (access === "error")
      return (
        <ErrorNote
          text={accessError}
          retry={() => setAccessAttempt((value) => value + 1)}
        />
      );
    return (
      <div>
        <h3>Тестовая заявка — после входа</h3>
        <p className="privacy-note">
          Создавать заявки в demo может только оператор. Публичные сценарии
          доступны без входа.
        </p>
        <a className="button primary full" href="#/app?return=contact">
          Войти для тестовой заявки
          <Icon name="arrow" size={18} />
        </a>
      </div>
    );
  }
  if (reference)
    return (
      <div className="success-state" role="status">
        <span className="success-icon">
          <Icon name="check" size={30} />
        </span>
        <h3>Заявка принята</h3>
        <p>
          {config.mode === "demo"
            ? "Тестовое обращение сохранено в demo-экземпляре. Реальная отправка на почту не выполняется."
            : config.mode === "controlled" ? "Синтетическое обращение сохранено в controlled-экземпляре. Почта не отправляется."
            : "Ваше обращение сохранено. Мы вернёмся к нему и свяжемся по указанной почте."}
        </p>
        <span className="reference">Обращение {reference}</span>
        <button
          className="button secondary"
          onClick={() => {
            setReference("");
            setValues({
              name: "",
              email: "",
              company: "",
              phone: "",
              message: "",
            });
            attempt.current = null;
          }}
        >
          Новое обращение
        </button>
      </div>
    );
  return (
    <form onSubmit={submit} noValidate ref={firstInvalid}>
      <div className="form-grid">
        {[
          {
            name: "name",
            label: "Ваше имя",
            placeholder: "Как к вам обращаться",
            required: true,
            type: "text",
            auto: "name",
          },
          {
            name: "email",
            label: "Рабочая почта",
            placeholder: "you@company.com",
            required: true,
            type: "email",
            auto: "email",
          },
          {
            name: "company",
            label: "Компания",
            placeholder: "Необязательно",
            required: false,
            type: "text",
            auto: "organization",
          },
          {
            name: "phone",
            label: "Телефон",
            placeholder: "Необязательно",
            required: false,
            type: "tel",
            auto: "tel",
          },
        ].map((field) => (
          <label
            className="field"
            key={field.name}
            htmlFor={`lead-${field.name}`}
          >
            <span>
              {field.label}
              {field.required && <b aria-hidden="true"> *</b>}
            </span>
            <input
              id={`lead-${field.name}`}
              name={field.name}
              autoComplete={field.auto}
              type={field.type}
              placeholder={field.placeholder}
              value={values[field.name as keyof typeof values]}
              required={field.required}
              aria-invalid={Boolean(errors[field.name])}
              aria-describedby={
                errors[field.name] ? `error-${field.name}` : undefined
              }
              onChange={(event) =>
                setValues({ ...values, [field.name]: event.target.value })
              }
              disabled={busy}
            />
            {errors[field.name] && (
              <small className="field-error" id={`error-${field.name}`}>
                {errors[field.name]}
              </small>
            )}
          </label>
        ))}
      </div>
      <label className="field" htmlFor="lead-message">
        <span>
          Что хотите автоматизировать? <b aria-hidden="true">*</b>
        </span>
        <textarea
          id="lead-message"
          name="message"
          rows={5}
          maxLength={8000}
          placeholder="Какая задача, что используете сейчас и какой результат хотите получить…"
          value={values.message}
          onChange={(event) =>
            setValues({ ...values, message: event.target.value })
          }
          aria-invalid={Boolean(errors.message)}
          aria-describedby="message-help"
          required
          disabled={busy}
        />
        <small
          id="message-help"
          className={errors.message ? "field-error" : "muted"}
        >
          {errors.message ||
            "Чем больше контекста, тем полезнее первый разговор."}
        </small>
      </label>
      {error && <ErrorNote text={error} />}
      <button type="submit" className="button primary full" disabled={busy}>
        {busy ? (
          <>
            <span className="spinner" />
            Сохраняем заявку…
          </>
        ) : (
          <>
            {isolated
              ? "Создать тестовую заявку"
              : "Отправить заявку"}
            <Icon name="arrow" size={18} />
          </>
        )}
      </button>
      <p className="privacy-note">{config.privacy_notice}</p>
      {isolated && (
        <p className="notice compact">
          Демонстрационный экземпляр. Используйте только вымышленные данные.
        </p>
      )}
    </form>
  );
}
