import { Component, Suspense, lazy, useEffect, useState } from "react";
import type { ReactNode } from "react";
import { api, setCsrf } from "./api";
import type { PublicConfig, Session } from "./types";
import { Landing } from "./StudioLanding";
import { ErrorNote, Loading } from "./ui";

// CSS is requested only with a workspace route; the landing has its own styles.
const workspaceStyles = () =>
  Promise.all([import("./styles.css"), import("./workspace-polish.css")]);
const Dashboard = lazy(async () => {
  const [module] = await Promise.all([
    import("./Dashboard"),
    workspaceStyles(),
  ]);
  return { default: module.Dashboard };
});
const Demo = lazy(async () => {
  const [module] = await Promise.all([import("./Public"), workspaceStyles()]);
  return { default: module.Demo };
});
const Login = lazy(async () => {
  const [module] = await Promise.all([import("./Public"), workspaceStyles()]);
  return { default: module.Login };
});

class RouteBoundary extends Component<
  { children: ReactNode },
  { failed: boolean }
> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  render() {
    return this.state.failed ? (
      <main id="main-content" className="route-state">
        <ErrorNote
          text="Не удалось загрузить страницу. Проверьте соединение и попробуйте снова."
          retry={() => window.location.reload()}
        />
      </main>
    ) : (
      this.props.children
    );
  }
}

const currentRoute = () =>
  window.location.hash.startsWith("#/app")
    ? "app"
    : window.location.hash.startsWith("#/demo")
      ? "demo"
      : "home";
const returnToContact = () => {
  if (window.location.hash === "#/app?return=contact")
    window.location.hash = "contact";
};
export default function App() {
  const [route, setRoute] = useState(currentRoute);
  const [config, setConfig] = useState<PublicConfig | null>(null);
  const [configError, setConfigError] = useState("");
  const [session, setSession] = useState<Session | null>(null);
  const [checking, setChecking] = useState(() => currentRoute() === "app");
  useEffect(() => {
    const onHash = () => {
      setRoute(currentRoute());
      if (window.location.hash.startsWith("#/")) window.scrollTo(0, 0);
    };
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);
  useEffect(() => {
    if (route !== "home") return;
    const id = window.location.hash.slice(1);
    if (!id || id.startsWith("/")) return;
    const frame = requestAnimationFrame(() =>
      document.getElementById(id)?.scrollIntoView(),
    );
    return () => cancelAnimationFrame(frame);
  }, [route]);
  useEffect(() => {
    api<PublicConfig>("/public/config")
      .then(setConfig)
      .catch((err) => setConfigError(err.message));
  }, []);
  useEffect(() => {
    const color = config?.branding?.primary_color;
    if (color && /^#[0-9a-f]{6}$/i.test(color)) {
      document.documentElement.style.setProperty("--indigo", color);
      document.documentElement.style.setProperty(
        "--indigo-hover",
        `color-mix(in srgb, ${color} 85%, black)`,
      );
    }
  }, [config]);
  useEffect(() => {
    if (route !== "app") return;
    let active = true;
    setChecking(true);
    api<Session>("/auth/me")
      .then((result) => {
        if (active) {
          setSession(result);
          setCsrf(result.csrf_token);
          returnToContact();
        }
      })
      .catch(() => {
        if (active) {
          setSession(null);
          setCsrf("");
        }
      })
      .finally(() => {
        if (active) setChecking(false);
      });
    return () => {
      active = false;
    };
  }, [route]);
  useEffect(() => {
    const brand = config?.brand.name || "LeadFlow";
    document.title =
      route === "app"
        ? `Кабинет — ${brand}`
        : route === "demo"
          ? `Демонстрация — ${brand}`
          : `${brand} — ${config?.branding?.headline || "Автоматизируем ваши заявки."}`;
    document
      .querySelector<HTMLMetaElement>('meta[property="og:title"]')
      ?.setAttribute("content", document.title);
    const description = config?.branding?.description;
    if (description) {
      document
        .querySelectorAll<HTMLMetaElement>(
          'meta[name="description"], meta[property="og:description"]',
        )
        .forEach((meta) => meta.setAttribute("content", description));
    }
  }, [route, config]);
  return (
    <>
      <a
        className="skip-link"
        href="#main-content"
        onClick={(event) => {
          event.preventDefault();
          const main = document.getElementById("main-content");
          main?.setAttribute("tabindex", "-1");
          main?.focus();
          main?.scrollIntoView();
        }}
      >
        Перейти к содержимому
      </a>
      <RouteBoundary key={route}>
        <Suspense fallback={<Loading label="Загружаем страницу…" />}>
          {route === "demo" ? (
            <Demo />
          ) : route === "app" ? (
            checking ? (
              <div className="auth-loading">
                <Loading label="Проверяем защищённую сессию…" />
              </div>
            ) : session ? (
              <Dashboard
                session={session}
                config={config}
                onLogout={() => {
                  setSession(null);
                  setCsrf("");
                }}
              />
            ) : (
              <Login
                mode={config?.mode}
                onLogin={(result) => {
                  setSession(result);
                  setCsrf(result.csrf_token);
                  returnToContact();
                }}
              />
            )
          ) : (
            <Landing config={config} configError={configError} />
          )}
        </Suspense>
      </RouteBoundary>
    </>
  );
}
