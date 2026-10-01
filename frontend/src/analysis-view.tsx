import type { Analysis, Criterion } from "./types";
import {
  extractEvidence,
  formatExplanation,
  formatFact,
  reviewReason,
} from "./facts";
import { Badge, Empty, Icon } from "./ui";

const factLabels: Record<string, string> = {
  service_fit: "Соответствие услуге",
  service_id: "Услуга",
  intent: "Намерение",
  urgency: "Сроки",
  budgets: "Бюджет",
  business_context: "Контекст бизнеса",
  desired_outcome: "Желаемый результат",
  language: "Язык",
  suggested_next_action: "Следующее действие",
};
const criterionLabels: Record<string, string> = {
  service_fit: "Соответствие услуге",
  intent: "Намерение",
  urgency: "Сроки",
  budget: "Релевантный бюджет",
  business_context: "Контекст бизнеса",
  desired_outcome: "Желаемый результат",
  not_fit_cap: "Ограничение: услуга не подходит",
};

export function AnalysisView({
  analysis,
}: {
  analysis: Analysis | null | undefined;
}) {
  if (!analysis)
    return (
      <article className="panel">
        <Empty title="Анализ ещё не готов">
          Заявка сохранена. После обработки здесь появятся факты и объяснение
          приоритета.
        </Empty>
      </article>
    );
  const facts = analysis.facts || {};
  const summary =
    analysis.summary ||
    (typeof facts.summary === "string" ? facts.summary : "");
  const contributions: Criterion[] = Array.isArray(analysis.contributions)
    ? analysis.contributions
    : Object.entries(analysis.contributions || {}).map(([key, value]) =>
        typeof value === "number"
          ? { key, points: value, reason: "" }
          : { ...value, key },
      );
  const missing =
    analysis.missing_information ||
    (Array.isArray(facts.missing_information)
      ? (facts.missing_information as string[])
      : []);
  const evidence =
    analysis.evidence ||
    (typeof facts.evidence === "object" && facts.evidence !== null
      ? (facts.evidence as Record<string, unknown>)
      : extractEvidence(facts));
  return (
    <>
      <article className="panel analysis-panel">
        <div className="panel-heading">
          <div className="inline-heading">
            <span className="icon-block indigo">
              <Icon name="spark" />
            </span>
            <div>
              <span className="eyebrow">AI ИЗВЛЕКАЕТ ФАКТЫ</span>
              <h3>Понятный приоритет</h3>
            </div>
          </div>
          <Badge value={analysis.temperature} />
        </div>
        {summary && <p className="analysis-summary">{summary}</p>}
        <div className="score-layout">
          <div className="score-display">
            <div
              className="score-ring"
              style={
                { "--score": `${analysis.score ?? 0}%` } as React.CSSProperties
              }
            >
              <div>
                <strong>{analysis.score ?? "—"}</strong>
                <span>из 100</span>
              </div>
            </div>
            <span>Приоритет обработки</span>
            <small>Не вероятность покупки</small>
          </div>
          <div className="score-criteria">
            {contributions.length ? (
              contributions.map((criterion, index) => (
                <div className="criterion" key={criterion.key || index}>
                  <div>
                    <span>
                      {criterion.name ||
                        criterionLabels[criterion.key || ""] ||
                        criterion.key ||
                        `Критерий ${index + 1}`}
                    </span>
                    <b>
                      {criterion.points > 0 ? "+" : ""}
                      {criterion.points}
                    </b>
                  </div>
                  {criterion.reason && (
                    <small>{formatExplanation(criterion.reason)}</small>
                  )}
                  <div className="criterion-track">
                    <span
                      style={{
                        width: `${Math.min(100, Math.max(0, (criterion.points / (criterion.maximum || 30)) * 100))}%`,
                      }}
                    />
                  </div>
                </div>
              ))
            ) : (
              <p className="muted">
                {analysis.score === null
                  ? "Недостаточно подтверждённых данных для оценки."
                  : "Вклад критериев появится после обработки."}
              </p>
            )}
          </div>
        </div>
        {analysis.review_reasons?.length ? (
          <div className="notice warning">
            <Icon name="alert" />
            <div>
              <strong>Нужна проверка менеджера</strong>
              <ul>
                {analysis.review_reasons.map((reason) => (
                  <li key={reason}>{reviewReason(reason)}</li>
                ))}
              </ul>
            </div>
          </div>
        ) : null}
        <div className="analysis-meta">
          <span>Правила {analysis.config_version || "—"}</span>
          <span>Модель {analysis.model || "—"}</span>
          {analysis.prompt_version && (
            <span>Промпт {analysis.prompt_version}</span>
          )}
        </div>
      </article>
      <article className="panel facts-panel">
        <div className="panel-heading">
          <div>
            <span className="eyebrow">ПРОВЕРЯЕМЫЙ КОНТЕКСТ</span>
            <h3>Факты из обращения</h3>
          </div>
          <Icon name="book" />
        </div>
        <dl className="facts-grid">
          {Object.entries(facts)
            .filter(([key]) => key in factLabels)
            .map(([key, value]) => (
              <div key={key}>
                <dt>{factLabels[key]}</dt>
                <dd>{formatFact(value)}</dd>
                {Boolean(evidence[key]) && (
                  <blockquote>«{formatFact(evidence[key])}»</blockquote>
                )}
              </div>
            ))}
        </dl>
        {missing.length > 0 && (
          <div className="missing-info">
            <span className="tiny-label">СТОИТ УТОЧНИТЬ</span>
            <ul>
              {missing.map((item) => (
                <li key={item}>{formatFact(item)}</li>
              ))}
            </ul>
          </div>
        )}
        {Object.keys(evidence).length > 0 && (
          <details className="evidence-details">
            <summary>Цитаты из исходного обращения</summary>
            {Object.entries(evidence).map(([key, value]) => (
              <div key={key}>
                <span className="tiny-label">{factLabels[key] || key}</span>
                <blockquote>{formatFact(value)}</blockquote>
              </div>
            ))}
          </details>
        )}
      </article>
    </>
  );
}
