const labels: Record<string, string> = {
  fit: "Подходит",
  not_fit: "Не подходит",
  unknown: "Недостаточно данных",
  ru: "Русский",
  en: "Английский",
  proposal: "Запрос предложения",
  project_discussion: "Обсуждение проекта",
  comparison: "Сравнение вариантов",
  information: "Общая информация",
  within_30_days: "В течение 30 дней",
  within_90_days: "В течение 90 дней",
  later: "Позже",
  one_time: "Разово",
  monthly: "В месяц",
  services: "Услуги",
  ad_spend: "Рекламный бюджет",
  combined: "Услуги и реклама вместе",
  clarify: "Уточнить детали",
  discuss_project: "Обсудить проект",
  review_fit: "Проверить соответствие услуге",
  manual_review: "Передать на ручную проверку",
  minimum: "От",
  maximum: "До",
  currency: "Валюта",
  period: "Период",
  purpose: "Назначение",
  service_id: "Услуга",
  value: "Сведения",
  budget: "Бюджет на услуги",
  service: "Подходящая услуга",
  urgency: "Сроки",
  intent: "Цель обращения",
};

export function formatFact(value: unknown): string {
  if (value === null || value === undefined) return "Не указано";
  if (typeof value === "string") return labels[value] || value;
  if (typeof value === "boolean") return value ? "Да" : "Нет";
  if (Array.isArray(value))
    return value.length ? value.map(formatFact).join(" · ") : "Не указано";
  if (typeof value === "object") {
    if ("value" in value) return formatFact(value.value);
    return Object.entries(value)
      .filter(([key]) => key !== "evidence")
      .map(([key, val]) => `${labels[key] || key}: ${formatFact(val)}`)
      .join(" · ");
  }
  return String(value);
}

export function formatExplanation(reason: string): string {
  for (const prefix of ["Намерение: ", "Срок: "]) {
    if (reason.startsWith(prefix))
      return prefix + formatFact(reason.slice(prefix.length));
  }
  return reason;
}

export function extractEvidence(
  facts: Record<string, unknown>,
): Record<string, unknown> {
  const result: Record<string, unknown> = {};
  for (const [field, source] of Object.entries({
    service_fit: "service_evidence",
    intent: "intent_evidence",
    urgency: "urgency_evidence",
  })) {
    if (Array.isArray(facts[source]) && facts[source].length)
      result[field] = facts[source];
  }
  for (const field of ["business_context", "desired_outcome"]) {
    const fact = facts[field];
    if (
      fact &&
      typeof fact === "object" &&
      "evidence" in fact &&
      Array.isArray(fact.evidence) &&
      fact.evidence.length
    )
      result[field] = fact.evidence;
  }
  if (Array.isArray(facts.budgets)) {
    const quotes = facts.budgets.flatMap((budget) =>
      budget &&
      typeof budget === "object" &&
      "evidence" in budget &&
      Array.isArray(budget.evidence)
        ? budget.evidence
        : [],
    );
    if (quotes.length) result.budgets = quotes;
  }
  return result;
}

export function reviewReason(code: string): string {
  return (
    (
      {
        service_fit_unknown:
          "Недостаточно информации, чтобы определить подходящую услугу.",
        service_not_configured: "Услуга отсутствует в текущем бизнес-конфиге.",
        source_contradiction:
          "В исходном обращении есть противоречивые сведения.",
        multiple_comparable_budgets:
          "Несколько сопоставимых бюджетов: уточните, какой использовать.",
      } as Record<string, string>
    )[code] || code
  );
}
