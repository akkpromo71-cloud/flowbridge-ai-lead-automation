import { test, expect } from "@playwright/test";
import type { Page } from "@playwright/test";
import { readFileSync } from "node:fs";

// These browser tests use the real local demo API. No network mocks or live services.
const credentialsPath = process.env.DEMO_OPERATOR_FILE;
const credential = credentialsPath
  ? (JSON.parse(
      readFileSync(credentialsPath, "utf8").replace(/^\uFEFF/, ""),
    ) as { email: string; password: string })
  : null;

async function login(page: Page) {
  test.skip(
    !credential,
    "DEMO_OPERATOR_FILE must point to a local synthetic operator credential file.",
  );
  await page.goto("/#/app");
  await page
    .getByLabel("Электронная почта", { exact: true })
    .fill(credential!.email);
  await page.getByLabel("Пароль", { exact: true }).fill(credential!.password);
  await page
    .getByRole("button", { name: "Войти в кабинет", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "Всё начинается с заявки" }),
  ).toBeVisible();
}

test.beforeEach(async ({ request }) => {
  const result = await request.get("/api/v1/public/config");
  expect(result.ok()).toBeTruthy();
  expect((await result.json()).mode).toBe("demo");
});

test("landing fits viewport and preserves keyboard/reduced-motion access", async ({
  page,
}, info) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/");
  const config = await (await page.request.get("/api/v1/public/config")).json();
  await expect(
    page.getByRole("heading", { name: config.branding.headline, exact: true }),
  ).toBeVisible();
  await page.keyboard.press("Tab");
  await expect(page.getByText("Перейти к содержимому")).toBeFocused();
  const dimensions = await page.evaluate(() => ({
    scroll: document.documentElement.scrollWidth,
    client: document.documentElement.clientWidth,
  }));
  expect(dimensions.scroll).toBeLessThanOrEqual(dimensions.client);
  if (info.project.name === "mobile")
    expect(
      await page.evaluate(
        () => matchMedia("(prefers-reduced-motion: reduce)").matches,
      ),
    ).toBe(true);
  expect(errors).toEqual([]);
  await page.keyboard.press("Tab");
  await page.screenshot({
    path: `test-results/landing-${info.project.name}.png`,
    fullPage: true,
  });
});

test("invalid operator demo form focuses error and never calls intake", async ({
  page,
}) => {
  let requests = 0;
  page.on("request", (request) => {
    if (request.method() === "POST" && request.url().endsWith("/public/leads"))
      requests++;
  });
  await login(page);
  await page.goto("/#contact");
  await page
    .getByRole("button", { name: "Создать тестовую заявку", exact: true })
    .click();
  await expect(
    page.getByText("Введите имя: от 2 до 120 символов."),
  ).toBeVisible();
  await expect(page.getByLabel("Ваше имя")).toBeFocused();
  expect(requests).toBe(0);
});

test("public synthetic demo has no mutation or shared admin access", async ({
  page,
}, info) => {
  const methods: string[] = [];
  page.on("request", (request) => {
    if (request.url().includes("/api/")) methods.push(request.method());
  });
  await page.goto("/#/demo");
  await expect(
    page.getByText("Синтетические данные · только просмотр"),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Понятный приоритет" }),
  ).toBeVisible();
  await page.getByRole("button", { name: /Недостаточно информации/ }).click();
  await expect(page.getByText("Нужна проверка менеджера")).toBeVisible();
  await expect(
    page.getByRole("button", { name: /одобрить|сохранить|отправить/i }),
  ).toHaveCount(0);
  expect(methods.every((method) => method === "GET")).toBe(true);
  await page.screenshot({
    path: `test-results/demo-${info.project.name}.png`,
    fullPage: true,
  });
});

test("operator demo intake gives safe 202 confirmation from real PostgreSQL", async ({
  page,
}) => {
  await login(page);
  await page.goto("/#contact");
  await page.getByLabel("Ваше имя").fill("Браузерный тест");
  await page.getByLabel("Рабочая почта").fill("browser-smoke@example.com");
  await page
    .getByLabel("Компания", { exact: true })
    .fill("Синтетическая компания QA");
  await page
    .getByLabel("Что хотите автоматизировать?")
    .fill(
      "Синтетический браузерный тест. Нужна автоматизация входящих заявок, бюджет пока не определён.",
    );
  const responsePromise = page.waitForResponse(
    (response) =>
      response.url().endsWith("/public/leads") &&
      response.request().method() === "POST",
  );
  await page
    .getByRole("button", { name: "Создать тестовую заявку", exact: true })
    .click();
  const response = await responsePromise;
  expect(response.status()).toBe(202);
  expect(response.request().headers()["x-csrf-token"]).toBeTruthy();
  const payload = await response.json();
  expect(Object.keys(payload).sort()).toEqual(["reference", "status"]);
  await expect(
    page.getByRole("heading", { name: "Заявка принята" }),
  ).toBeVisible();
  await expect(
    page.getByText(/Реальная отправка на почту не выполняется/),
  ).toBeVisible();
});

test("operator login, real data, detail and logout keep access protected", async ({
  page,
}, info) => {
  await login(page);
  await expect(
    page.getByText("За последние 30 дней", { exact: true }),
  ).toBeVisible();
  await expect(page.locator(".lead-table tbody tr").first()).toBeVisible();
  await page.screenshot({
    path: `test-results/dashboard-${info.project.name}.png`,
    fullPage: true,
  });
  await page
    .getByRole("button", { name: "Браузерный тест", exact: true })
    .first()
    .click();
  await expect(
    page.getByRole("heading", { name: "С чего начался разговор" }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Изменить стадию", exact: true })
    .click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();
  await dialog.getByRole("button", { name: "Сохранить решение" }).focus();
  await page.keyboard.press("Tab");
  await expect(
    dialog.getByRole("button", { name: "Закрыть", exact: true }),
  ).toBeFocused();
  await dialog
    .getByRole("combobox", { name: "Стадия", exact: true })
    .selectOption("contacted");
  await dialog
    .getByLabel("Причина изменения")
    .fill("Синтетическая проверка интерфейса: контакт отмечен вручную.");
  await dialog.getByRole("button", { name: "Сохранить решение" }).click();
  await expect(page.getByText("Изменения сохранены в истории.")).toBeVisible();
  await expect(
    page.locator(".contact-details").getByText("Связались", { exact: true }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Остановить сообщения", exact: true })
    .click();
  await expect(
    page.getByRole("checkbox", { name: /Клиент попросил больше не писать/ }),
  ).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  const current = page.url();
  expect(current).toContain("#/app/leads/");
  await page.reload();
  await expect(
    page.getByRole("heading", { name: "С чего начался разговор" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "История", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "История решений" }),
  ).toBeVisible();
  await page.screenshot({
    path: `test-results/card-${info.project.name}.png`,
    fullPage: true,
  });
  await page.getByRole("button", { name: "Выйти из кабинета" }).click();
  await expect(
    page.getByRole("heading", { name: "С возвращением" }),
  ).toBeVisible();
  const response = await page.request.get("/api/v1/admin/leads");
  expect(response.status()).toBe(401);
});

test("integration health distinguishes simulation from live verification", async ({
  page,
}) => {
  await login(page);
  await page.getByRole("button", { name: "Интеграции", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Интеграции", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("Синтетическая имитация; реальных вызовов нет").first(),
  ).toBeVisible();
});
