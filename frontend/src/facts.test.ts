import { describe, expect, it } from "vitest";
import { extractEvidence, formatFact } from "./facts";

describe("fact presentation preserves uncertainty and evidence", () => {
  it("does not turn missing money into zero", () =>
    expect(formatFact({ minimum: null, maximum: null, currency: null })).toBe(
      "От: Не указано · До: Не указано · Валюта: Не указано",
    ));
  it("distinguishes unsupported from unknown fit", () => {
    expect(formatFact("not_fit")).toBe("Не подходит");
    expect(formatFact("unknown")).toBe("Недостаточно данных");
  });
  it("shows supported facts without leaking internal field labels", () =>
    expect(
      formatFact({ value: "B2B-агентство", evidence: ["Компания B2B."] }),
    ).toBe("B2B-агентство"));
  it("takes evidence from every actual schema location without inventing a quote", () => {
    expect(
      extractEvidence({
        service_evidence: ["Нужна интеграция."],
        urgency_evidence: [],
        business_context: { value: "Агентство", evidence: ["Мы агентство."] },
        budgets: [{ minimum: "300000", evidence: ["300000 KZT"] }],
      }),
    ).toEqual({
      service_fit: ["Нужна интеграция."],
      business_context: ["Мы агентство."],
      budgets: ["300000 KZT"],
    });
  });
  it("keeps instructions and HTML as text for React escaping", () =>
    expect(formatFact("<script>doBadThings()</script>")).toBe(
      "<script>doBadThings()</script>",
    ));
});
