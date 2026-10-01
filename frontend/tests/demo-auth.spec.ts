import { test, expect } from "@playwright/test";
import { readFileSync } from "node:fs";

const credentialPath = process.env.DEMO_OPERATOR_FILE;
const credential = credentialPath
  ? JSON.parse(readFileSync(credentialPath, "utf8").replace(/^\uFEFF/, ""))
  : null;

test.beforeEach(async ({ request }) => {
  const response = await request.get("/api/v1/public/config");
  expect(response.ok()).toBe(true);
  expect((await response.json()).mode).toBe("demo");
});

test("anonymous demo shows public scenarios but gates the intake form", async ({
  page,
  request,
}) => {
  const demo = await request.get("/api/v1/public/demo");
  expect(demo.ok()).toBe(true);
  expect((await demo.json()).readonly).toBe(true);
  expect((await request.get("/api/v1/admin/leads")).status()).toBe(401);
  await page.goto("/#contact");
  await expect(
    page.getByRole("link", { name: "Войти для тестовой заявки", exact: true }),
  ).toHaveAttribute("href", "#/app?return=contact");
  await expect(page.getByLabel("Ваше имя")).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "Создать тестовую заявку", exact: true }),
  ).toHaveCount(0);
});

test("demo intake rejects anonymous HTTP and resumes after operator login with CSRF", async ({
  page,
  request,
}) => {
  test.skip(!credential, "Private DEMO_OPERATOR_FILE is required.");
  const suffix = `${Date.now()}-${test.info().project.name}`;
  const name = `Demo auth ${suffix}`;
  const payload = {
    name,
    email: `demo-auth-${suffix}@example.com`,
    message: "Синтетическая проверка авторизации demo-заявки.",
    utm: {},
  };
  const rejected = await request.post("/api/v1/public/leads", {
    headers: {
      Origin: "http://127.0.0.1:5173",
      "Idempotency-Key": crypto.randomUUID(),
    },
    data: payload,
  });
  expect(rejected.status()).toBe(401);

  await page.goto("/#contact");
  await page
    .getByRole("link", { name: "Войти для тестовой заявки", exact: true })
    .click();
  await page
    .getByLabel("Электронная почта", { exact: true })
    .fill(credential.email);
  await page.getByLabel("Пароль", { exact: true }).fill(credential.password);
  await page
    .getByRole("button", { name: "Войти в кабинет", exact: true })
    .click();
  await expect(page).toHaveURL(/#contact$/);
  await expect(page.getByLabel("Ваше имя")).toBeVisible();
  const before = await page.request.get(
    `/api/v1/admin/leads?q=${encodeURIComponent(name)}`,
  );
  expect(before.ok()).toBe(true);
  expect((await before.json()).total).toBe(0);

  const withoutCsrf = await page.request.post("/api/v1/public/leads", {
    headers: {
      Origin: "http://127.0.0.1:5173",
      "Idempotency-Key": crypto.randomUUID(),
    },
    data: payload,
  });
  expect(withoutCsrf.status()).toBe(403);
  const afterRejected = await page.request.get(
    `/api/v1/admin/leads?q=${encodeURIComponent(name)}`,
  );
  expect((await afterRejected.json()).total).toBe(0);
  await page.getByLabel("Ваше имя").fill(name);
  await page.getByLabel("Рабочая почта").fill(payload.email);
  await page.getByLabel("Что хотите автоматизировать?").fill(payload.message);
  const responsePromise = page.waitForResponse(
    (response) =>
      response.url().endsWith("/public/leads") &&
      response.request().method() === "POST",
  );
  await page
    .getByRole("button", { name: "Создать тестовую заявку", exact: true })
    .click();
  const accepted = await responsePromise;
  expect(accepted.status()).toBe(202);
  expect(accepted.request().headers()["x-csrf-token"]).toBeTruthy();
  expect(accepted.request().headers()["idempotency-key"]).toBeTruthy();
  await expect(
    page.getByRole("heading", { name: "Заявка принята" }),
  ).toBeVisible();
  const after = await page.request.get(
    `/api/v1/admin/leads?q=${encodeURIComponent(name)}`,
  );
  expect((await after.json()).total).toBe(1);
});
