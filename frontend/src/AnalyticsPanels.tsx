import type { Analytics, PublicConfig } from "./types";
import { stageNames } from "./ui";

export function AnalyticsPanels({
  analytics,
  services,
}: {
  analytics: Analytics;
  services: PublicConfig["services"];
}) {
  const serviceNames = Object.fromEntries(
    services.map((service) => [service.id, service.name]),
  );
  const sourceNames: Record<string, string> = {
    website: "Публичная форма",
    demo: "Демонстрация",
    manual: "Вручную",
    unassessed: "Не указан",
  };
  const panels = [
    {
      title: "Коммерческие стадии",
      data: analytics.stages,
      labels: stageNames,
    },
    {
      title: "Источники обращений",
      data: analytics.sources,
      labels: sourceNames,
    },
    {
      title: "Запрошенные услуги",
      data: analytics.services,
      labels: { ...serviceNames, unassessed: "Не определена" },
    },
  ];
  return (
    <section
      className="analytics-breakdown"
      aria-label="Структура входящих обращений"
    >
      {panels.map((panel) => (
        <article className="panel" key={panel.title}>
          <h3>{panel.title}</h3>
          <p className="small muted">Когорта за последние 30 дней</p>
          {Object.keys(panel.data).length ? (
            <dl>
              {Object.entries(panel.data)
                .sort((a, b) => b[1] - a[1])
                .map(([key, count]) => (
                  <div key={key}>
                    <dt>{panel.labels[key] || key}</dt>
                    <dd>{count}</dd>
                    <span className="breakdown-track" aria-hidden="true">
                      <i
                        style={{
                          width: `${analytics.total ? (count / analytics.total) * 100 : 0}%`,
                        }}
                      />
                    </span>
                  </div>
                ))}
            </dl>
          ) : (
            <p className="breakdown-empty">
              В выбранной когорте пока нет заявок.
            </p>
          )}
        </article>
      ))}
    </section>
  );
}
