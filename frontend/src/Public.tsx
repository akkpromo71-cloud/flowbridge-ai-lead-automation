import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { api, mutation } from "./api";
import type { PublicDemo, Session } from "./types";
import { Badge, ErrorNote, Icon, Loading, Logo } from "./ui";
import { AnalysisView } from "./analysis-view";
import { PublicHeader } from "./PublicHeader";

export function Login({
  onLogin,
  mode,
}: {
  onLogin: (session: Session) => void;
  mode?: string;
}) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      onLogin(await mutation<Session>("/auth/login", { email, password }));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось войти.");
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <PublicHeader />
      <main className="login-layout" id="main-content">
        <div className="login-intro">
          <span className="eyebrow">РАБОЧЕЕ ПРОСТРАНСТВО</span>
          <h1>
            Контекст.
            <br />
            Приоритет.
            <br />
            <em>Следующий шаг.</em>
          </h1>
          <p>
            Каждая заявка на своём месте.
            <br />
            Каждое решение — под вашим контролем.
          </p>
          <div className="login-decoration">
            <Icon name="shield" size={42} />
            <span>Защищённый кабинет оператора</span>
          </div>
        </div>
        <div className="login-card">
          <span className="icon-block indigo">
            <Icon name="lock" size={25} />
          </span>
          <h2>С возвращением</h2>
          <p className="muted">
            Войдите в кабинет{" "}
            {mode === "demo" ? "демонстрационного экземпляра" : "компании"}.
          </p>
          <form onSubmit={submit}>
            <label className="field" htmlFor="login-email">
              <span>Электронная почта</span>
              <input
                id="login-email"
                type="email"
                autoComplete="username"
                required
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                disabled={busy}
                placeholder="operator@company.com"
              />
            </label>
            <label className="field" htmlFor="login-password">
              <span>Пароль</span>
              <input
                id="login-password"
                type="password"
                autoComplete="current-password"
                required
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                disabled={busy}
                placeholder="Введите пароль"
              />
            </label>
            {error && <ErrorNote text={error} />}
            <button className="button primary full" disabled={busy}>
              {busy ? "Входим…" : "Войти в кабинет"}
              <Icon name="arrow" size={18} />
            </button>
          </form>
          <p className="login-note">
            Доступ создаёт администратор экземпляра. В публичной демонстрации
            можно посмотреть синтетические сценарии без входа.
          </p>
          <a className="text-link" href="#/demo">
            Открыть публичную демонстрацию
            <Icon name="arrow" size={16} />
          </a>
        </div>
      </main>
    </>
  );
}

export function Demo() {
  const [data, setData] = useState<PublicDemo | null>(null);
  const [error, setError] = useState("");
  const [selected, setSelected] = useState(0);
  const load = () => {
    setError("");
    api<PublicDemo>("/public/demo")
      .then(setData)
      .catch((err) => setError(err.message));
  };
  useEffect(load, []);
  const scenario = data?.scenarios[selected];
  return (
    <>
      <PublicHeader />
      <main className="demo-page wrap" id="main-content">
        <div className="demo-page-header">
          <div>
            <span className="eyebrow">ПОСМОТРИТЕ ИЗНУТРИ</span>
            <h1>
              Одна заявка.
              <br />
              <em>Весь контекст.</em>
            </h1>
            <p>
              Выберите сценарий и посмотрите, как запрос превращается в понятный
              следующий шаг.
            </p>
          </div>
          <div className="demo-mode">
            <Icon name="shield" size={22} />
            <div>
              <strong>Публичная демонстрация</strong>
              <span>Синтетические данные · только просмотр</span>
            </div>
          </div>
        </div>
        <div className="notice">
          <Icon name="book" />
          <p>
            Здесь нет реальных клиентов и отправки сообщений. Редактирование и
            одобрение доступны только оператору в отдельном защищённом
            demo-кабинете.
          </p>
        </div>
        {error ? (
          <ErrorNote text={error} retry={load} />
        ) : !data ? (
          <Loading />
        ) : (
          <div className="demo-layout">
            <aside className="scenario-list" aria-label="Сценарии демонстрации">
              <span className="tiny-label">ВЫБЕРИТЕ СЦЕНАРИЙ</span>
              {data.scenarios.map((item, index) => (
                <button
                  key={item.id}
                  className={`scenario-button ${index === selected ? "selected" : ""}`}
                  onClick={() => setSelected(index)}
                  aria-pressed={index === selected}
                >
                  <span className="scenario-number">
                    {String(index + 1).padStart(2, "0")}
                  </span>
                  <span>{item.title}</span>
                  <Icon name="chevron" size={17} />
                </button>
              ))}
              <a className="demo-login-link" href="#/app">
                <Icon name="lock" size={17} />
                Войти в кабинет оператора
              </a>
            </aside>
            {scenario && (
              <div className="demo-result">
                <article className="panel source-panel">
                  <div className="panel-heading">
                    <div>
                      <span className="eyebrow">ВХОДЯЩЕЕ ОБРАЩЕНИЕ</span>
                      <h2>{scenario.title}</h2>
                    </div>
                    <Badge value={scenario.analysis.temperature} />
                  </div>
                  <p className="original-message">{scenario.input}</p>
                  <p className="muted small">
                    <Icon name="user" size={14} />
                    Вымышленный запрос для демонстрации
                  </p>
                </article>
                <AnalysisView analysis={scenario.analysis} />
              </div>
            )}
          </div>
        )}
      </main>
      <Footer />
    </>
  );
}

function Footer() {
  return (
    <footer className="site-footer wrap">
      <Logo />
      <p>
        Осмысленная автоматизация.
        <br />
        Решения остаются за вами.
      </p>
      <a href="#/app">
        Кабинет оператора
        <Icon name="arrow" size={16} />
      </a>
    </footer>
  );
}
