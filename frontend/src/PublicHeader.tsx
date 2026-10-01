import { useEffect, useState } from "react";
import { Logo } from "./ui";
import "./public-header.css";

export function PublicHeader() {
  const [open, setOpen] = useState(false);
  useEffect(() => {
    const close = () => setOpen(false);
    window.addEventListener("hashchange", close);
    return () => window.removeEventListener("hashchange", close);
  }, []);
  return (
    <header className="lf-header">
      <div className="lf-header-inner">
        <Logo />
        <nav className="lf-desktop-nav" aria-label="Основная навигация">
          <a href="#workflow">Как работает</a>
          <a href="#/demo">Демо</a>
        </nav>
        <div className="lf-header-actions">
          <a href="#/app" className="lf-login-link">
            Кабинет
          </a>
          <a href="#contact" className="lf-header-cta">
            Начать
          </a>
          <button
            type="button"
            className="lf-menu-toggle"
            aria-label={open ? "Закрыть меню" : "Открыть меню"}
            aria-expanded={open}
            aria-controls="lf-mobile-nav"
            onClick={() => setOpen(!open)}
            onKeyDown={(event) => {
              if (event.key === "Escape") setOpen(false);
            }}
          >
            <span />
            <span />
          </button>
        </div>
      </div>
      {open && (
        <nav
          id="lf-mobile-nav"
          className="lf-mobile-nav"
          aria-label="Мобильная навигация"
          onKeyDown={(event) => {
            if (event.key === "Escape") {
              setOpen(false);
              document
                .querySelector<HTMLButtonElement>(".lf-menu-toggle")
                ?.focus();
            }
          }}
        >
          <a href="#workflow" onClick={() => setOpen(false)}>
            Как работает
          </a>
          <a href="#/demo" onClick={() => setOpen(false)}>
            Демо
          </a>
          <a href="#/app" onClick={() => setOpen(false)}>
            Кабинет
          </a>
          <a href="#contact" onClick={() => setOpen(false)}>
            Начать с заявки
          </a>
        </nav>
      )}
    </header>
  );
}
