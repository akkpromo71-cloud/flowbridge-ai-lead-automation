import { afterEach, describe, expect, it, vi } from "vitest";
import {
  api,
  ApiError,
  errorText,
  intakeKey,
  mutation,
  setCsrf,
  validateLead,
} from "./api";
import type { LeadSubmission } from "./types";

const valid = {
  name: "Анна Тестовая",
  email: "anna@example.test",
  message: "Нужно автоматизировать обработку входящих заявок.",
};
const body: LeadSubmission = { ...valid, company: null, phone: null, utm: {} };
afterEach(() => {
  vi.unstubAllGlobals();
  setCsrf("");
});

describe("public intake validation", () => {
  it("accepts Unicode and optional missing contacts", () =>
    expect(validateLead(valid)).toEqual({}));
  it("reports per-field errors before any network request", () =>
    expect(
      Object.keys(
        validateLead({ name: " ", email: "invalid", message: "коротко" }),
      ).sort(),
    ).toEqual(["email", "message", "name"]));
  it("enforces backend message and company limits", () =>
    expect(
      validateLead({
        ...valid,
        message: "a".repeat(8001),
        company: "a".repeat(161),
      }),
    ).toHaveProperty("message"));
  it("keeps an unknown budget as free text, with no invented amount", () =>
    expect(
      validateLead({
        ...valid,
        message: "Нужна автоматизация, бюджет пока неизвестен.",
      }),
    ).toEqual({}));
});

describe("idempotent intake retry", () => {
  it("keeps the same key after an uncertain network outcome", () => {
    const first = intakeKey(null, body, () => "first");
    expect(intakeKey(first, { ...body }, () => "second")).toBe(first);
  });
  it("gives an edited request by the same sender a new key", () => {
    const first = intakeKey(null, body, () => "first");
    expect(
      intakeKey(
        first,
        { ...body, message: "Другая задача для той же компании." },
        () => "second",
      ).key,
    ).toBe("second");
  });
});

describe("safe API boundary", () => {
  it("sends cookie session, CSRF and stable decision idempotency key together", async () => {
    const fetcher = vi
      .fn()
      .mockResolvedValue(
        new Response(JSON.stringify({ ok: true }), { status: 200 }),
      );
    vi.stubGlobal("fetch", fetcher);
    setCsrf("test-csrf");
    await mutation(
      "/admin/messages/one/decisions",
      { decision: "approve" },
      "POST",
      "decision-key",
    );
    const [url, options] = fetcher.mock.calls[0];
    expect(url).toBe("/api/v1/admin/messages/one/decisions");
    expect(options.credentials).toBe("same-origin");
    expect(options.headers.get("X-CSRF-Token")).toBe("test-csrf");
    expect(options.headers.get("Idempotency-Key")).toBe("decision-key");
  });
  it("never presents a server exception as a successful result", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValue(
          new Response('{"message":"password=secret"}', { status: 500 }),
        ),
    );
    await expect(api("/public/leads")).rejects.toThrow(
      "Сервис временно недоступен",
    );
  });
  it("does not expose backend tracebacks in user errors", () =>
    expect(
      errorText(500, { message: "Traceback api_key=secret" }),
    ).not.toContain("secret"));
  it("preserves version conflict as a distinct actionable error", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response("{}", { status: 409 })),
    );
    try {
      await api("/admin/leads/id");
    } catch (error) {
      expect(error).toBeInstanceOf(ApiError);
      expect((error as ApiError).status).toBe(409);
      expect((error as Error).message).toContain("Обновите карточку");
    }
  });
  it("reports unavailable network instead of using fake success", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("offline")));
    await expect(api("/public/leads")).rejects.toThrow("Нет связи");
  });
});
