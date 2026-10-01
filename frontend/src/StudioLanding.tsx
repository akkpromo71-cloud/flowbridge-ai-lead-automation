import { useEffect, useRef, useState } from "react";
import type { PublicConfig } from "./types";
import { ErrorNote, Icon, Loading, Logo } from "./ui";
import { LeadForm } from "./LeadForm";
import { PublicHeader } from "./PublicHeader";
import "./studio-landing.css";

const stages = [
  {
    title: "Заявка приходит",
    detail: "Из формы — сразу в рабочее пространство.",
    icon: "inbox",
  },
  {
    title: "AI разбирает задачу",
    detail: "Выделяет задачу, сроки и нужные уточнения.",
    icon: "spark",
  },
  {
    title: "Вы проверяете ответ",
    detail: "Отправка начинается только с вашего одобрения.",
    icon: "shield",
  },
];

function useReducedMotion() {
  const [reduced, setReduced] = useState(
    () => window.matchMedia("(prefers-reduced-motion: reduce)").matches,
  );
  useEffect(() => {
    const media = window.matchMedia("(prefers-reduced-motion: reduce)");
    const update = () => setReduced(media.matches);
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);
  return reduced;
}

function ProcessStory({
  moving,
  pause,
}: {
  moving: boolean;
  pause: () => void;
}) {
  const target = useRef<HTMLDivElement>(null);
  const [visible, setVisible] = useState(false);
  const [step, setStep] = useState(0);
  useEffect(() => {
    if (!target.current || !window.IntersectionObserver) {
      setVisible(true);
      return;
    }
    const observer = new IntersectionObserver(
      ([entry]) => setVisible(entry.isIntersecting),
      { threshold: 0.35 },
    );
    observer.observe(target.current);
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    if (!moving || !visible) return;
    const timer = window.setInterval(
      () => setStep((value) => (value + 1) % stages.length),
      4500,
    );
    return () => window.clearInterval(timer);
  }, [moving, visible]);
  return (
    <div className="lf-story" ref={target} data-playing={moving && visible}>
      <div
        className="lf-story-steps"
        role="group"
        aria-label="Как работает LeadFlow"
      >
        {stages.map((stage, index) => (
          <button
            key={stage.title}
            type="button"
            aria-pressed={index === step}
            onClick={() => {
              setStep(index);
              pause();
            }}
          >
            <span className="lf-step-number">0{index + 1}</span>
            <span>
              <strong>{stage.title}</strong>
              <small>{stage.detail}</small>
            </span>
            <span
              className="lf-step-progress"
              key={`${step}-${index}`}
              aria-hidden="true"
            />
          </button>
        ))}
      </div>
      <div className="lf-story-display">
        <div className="lf-story-top">
          <span>
            <i /> LEADFLOW
          </span>
          <span>Пример · без отправки</span>
        </div>
        <div
          className="lf-story-panel"
          data-step={step}
          key={step}
          role="region"
          aria-label="Пример обработки заявки"
        >
          <div className="lf-panel-icon">
            <Icon name={stages[step].icon} size={24} />
          </div>
          <h3>
            {["Новая заявка", "Главное — на виду", "Черновик по шаблону"][step]}
          </h3>
          {step === 0 ? (
            <div className="lf-message-example">
              <span>Входящее обращение</span>
              <p>
                «Хотим автоматизировать заявки с сайта. Сейчас разбираем всё
                вручную.»
              </p>
              <div>
                <Icon name="check" size={14} /> Обращение сохранено
              </div>
            </div>
          ) : step === 1 ? (
            <dl className="lf-brief-example">
              <div>
                <dt>Задача</dt>
                <dd>Автоматизация заявок</dd>
              </div>
              <div>
                <dt>Сейчас</dt>
                <dd>Ручная обработка</dd>
              </div>
              <div>
                <dt>Следующий шаг</dt>
                <dd>Уточнить процесс</dd>
              </div>
            </dl>
          ) : (
            <div className="lf-message-example">
              <span>Шаблонный ответ для проверки</span>
              <p>
                «Расскажите, как сейчас обрабатываете обращения и на что уходит
                больше всего времени?»
              </p>
              <div className="lf-awaiting">
                <Icon name="shield" size={14} /> Ожидает вашего одобрения
              </div>
            </div>
          )}
        </div>
        <div className="lf-story-track" aria-hidden="true">
          {stages.map((stage, index) => (
            <span key={stage.title} className={index <= step ? "active" : ""} />
          ))}
        </div>
      </div>
    </div>
  );
}

export function Landing({
  config,
  configError,
}: {
  config: PublicConfig | null;
  configError: string;
}) {
  const root = useRef<HTMLDivElement>(null);
  const reduced = useReducedMotion();
  const [paused, setPaused] = useState(false);
  const [hidden, setHidden] = useState(document.hidden);
  const [heroVisible, setHeroVisible] = useState(true);
  const moving = !reduced && !paused && !hidden;
  useEffect(() => {
    const update = () => setHidden(document.hidden);
    document.addEventListener("visibilitychange", update);
    return () => document.removeEventListener("visibilitychange", update);
  }, []);
  useEffect(() => {
    if (!window.IntersectionObserver) return;
    const heroObserver = new IntersectionObserver(([entry]) =>
      setHeroVisible(entry.isIntersecting),
    );
    const hero = root.current?.querySelector(".lf-art");
    if (hero) heroObserver.observe(hero);
    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) {
            entry.target.classList.add("lf-entered");
            observer.unobserve(entry.target);
          }
        });
      },
      { threshold: 0.12 },
    );
    root.current
      ?.querySelectorAll("[data-enter]")
      .forEach((node) => observer.observe(node));
    return () => {
      observer.disconnect();
      heroObserver.disconnect();
    };
  }, []);
  const headline = config?.branding?.headline || "Автоматизируем ваши заявки.";
  return (
    <div
      className="lf-page"
      ref={root}
      data-motion={moving ? "running" : "paused"}
    >
      <PublicHeader />
      <main id="main-content">
        <section className="lf-hero lf-container">
          <div className="lf-hero-copy">
            <p className="lf-eyebrow">
              <span>{config?.brand.name || "LeadFlow"}</span>
              <i /> AI-АВТОМАТИЗАЦИЯ
            </p>
            <h1>
              {headline === "Автоматизируем ваши заявки." ? (
                <>
                  Автоматизируем <br />
                  <em>ваши заявки.</em>
                </>
              ) : (
                headline
              )}
            </h1>
            <p className="lf-hero-description">
              {config?.branding?.description ||
                "AI разбирает обращения. Система готовит черновик по шаблону — вы проверяете и одобряете отправку."}
            </p>
            <div className="lf-hero-actions">
              <a className="lf-button" href="#contact">
                {config?.branding?.primary_cta || "Обсудить задачу"}
                <Icon name="arrow" size={17} />
              </a>
              <a className="lf-text-link" href="#workflow">
                Как это работает <span>↓</span>
              </a>
            </div>
          </div>
          <div className="lf-art" data-active={heroVisible}>
            <div className="lf-art-stage" aria-hidden="true">
              <div className="lf-art-halo" />
              <div className="lf-art-object">
                <picture>
                  <source
                    media="(max-width: 640px)"
                    srcSet="/images/leadflow-signal-640.webp"
                  />
                  <img
                    src="/images/leadflow-signal-960.webp"
                    width="960"
                    height="960"
                    alt=""
                    fetchPriority="high"
                    decoding="async"
                  />
                </picture>
              </div>
              <div className="lf-signal-path lf-signal-in">
                <i />
                <span>
                  <Icon name="mail" size={14} /> Заявка
                </span>
              </div>
              <div className="lf-signal-path lf-signal-out">
                <i />
                <span>
                  <Icon name="check" size={14} /> Готово к проверке
                </span>
              </div>
            </div>
            <button
              type="button"
              className="lf-motion-control"
              aria-label={moving ? "Остановить анимацию" : "Включить анимацию"}
              aria-pressed={!moving}
              disabled={reduced}
              onClick={() => setPaused((value) => !value)}
            >
              <span aria-hidden="true">{moving ? "Ⅱ" : "▷"}</span>
              {reduced
                ? "Анимация отключена в настройках"
                : moving
                  ? "Анимация"
                  : "Продолжить анимацию"}
            </button>
          </div>
          <a className="lf-scroll-cue" href="#workflow">
            <span /> Листайте — покажем на примере
          </a>
        </section>

        <section id="workflow" className="lf-workflow lf-container" data-enter>
          <span id="capabilities" className="lf-anchor" />
          <span id="approach" className="lf-anchor" />
          <div className="lf-section-heading">
            <div>
              <p className="lf-eyebrow">ОТ ОБРАЩЕНИЯ К ОТВЕТУ</p>
              <h2>
                Три шага. <br />
                <em>Меньше ручной работы.</em>
              </h2>
            </div>
            <a className="lf-text-link" href="#/demo">
              {config?.branding?.secondary_cta || "Посмотреть демо"}
              <Icon name="arrow" size={16} />
            </a>
          </div>
          <ProcessStory moving={moving} pause={() => setPaused(true)} />
        </section>

        <section className="lf-contact lf-container" id="contact" data-enter>
          <div className="lf-contact-copy">
            <p className="lf-eyebrow">НАЧНЁМ С ВАШЕЙ ЗАДАЧИ</p>
            <h2>
              Что хотите <br />
              <em>автоматизировать?</em>
            </h2>
            <p>
              {config?.mode === "demo"
                ? "Войдите как оператор и создайте вымышленную заявку. Это demo: настоящие письма не отправляются."
                : "Расскажите о своём процессе. Разберём задачу и предложим первый шаг."}
            </p>
          </div>
          <div className="lf-form-card">
            {configError ? (
              <ErrorNote text={configError} />
            ) : !config ? (
              <Loading label="Загружаем форму…" />
            ) : (
              <LeadForm config={config} />
            )}
          </div>
        </section>
      </main>
      <footer className="lf-footer lf-container">
        <Logo />
        <span>{config?.brand.name || "LeadFlow"} · AI для вашей команды</span>
        <a href="#/app">
          Кабинет <Icon name="arrow" size={14} />
        </a>
      </footer>
    </div>
  );
}
