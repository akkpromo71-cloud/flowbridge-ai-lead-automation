import { test, expect } from "@playwright/test";
import { readFileSync } from "node:fs";

test("real demo UI: intake → n8n → reviewed new version → fake provider acceptance", async ({
  page,
  request,
}, info) => {
  const path = process.env.DEMO_OPERATOR_FILE;
  if (!path)
    throw new Error(
      "Set DEMO_OPERATOR_FILE to the private synthetic operator JSON.",
    );
  const credential = JSON.parse(
    readFileSync(path, "utf8").replace(/^\uFEFF/, ""),
  );
  const baseUrl = new URL(String(info.project.use.baseURL));
  expect(["127.0.0.1", "localhost"]).toContain(baseUrl.hostname);
  const config = await (await request.get("/api/v1/public/config")).json();
  expect(config.mode).toBe("demo");
  const demo = await (await request.get("/api/v1/public/demo")).json();
  const hot = demo.scenarios.find(
    (scenario: { analysis: { temperature: string } }) =>
      scenario.analysis.temperature === "HOT",
  );
  expect(hot).toBeTruthy();
  const suffix = Date.now();
  const name = `UI workflow ${suffix}`;
  const existingId =
    info.project.name === "desktop" ? process.env.DEMO_LEAD_ID : undefined;
  await page.goto("/#/app");
  await page
    .getByLabel("Электронная почта", { exact: true })
    .fill(credential.email);
  await page.getByLabel("Пароль", { exact: true }).fill(credential.password);
  await page
    .getByRole("button", { name: "Войти в кабинет", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "Всё начинается с заявки" }),
  ).toBeVisible();
  const integrations = await (
    await page.request.get("/api/v1/admin/integrations")
  ).json();
  for (const name of ["openai", "smtp"])
    expect(
      integrations.find((item: { name: string }) => item.name === name)?.state,
    ).toBe("simulated");
  if (!existingId) {
    await page.goto("/#contact");
    await page.getByLabel("Ваше имя").fill(name);
    await page
      .getByLabel("Рабочая почта")
      .fill(`ui-workflow-${suffix}@example.com`);
    await page
      .getByLabel("Компания", { exact: true })
      .fill("Синтетический браузерный smoke");
    await page.getByLabel("Что хотите автоматизировать?").fill(hot.input);
    await page
      .getByRole("button", { name: "Создать тестовую заявку", exact: true })
      .click();
    await expect(
      page.getByRole("heading", { name: "Заявка принята" }),
    ).toBeVisible();
  }
  await page.goto("/#/app");
  await expect(
    page.getByRole("heading", { name: "Всё начинается с заявки" }),
  ).toBeVisible();
  let leadId = existingId;
  if (!leadId) {
    const result = await (
      await page.request.get(
        `/api/v1/admin/leads?q=${encodeURIComponent(name)}`,
      )
    ).json();
    expect(result.items).toHaveLength(1);
    leadId = result.items[0].id;
  }
  console.log(`Synthetic ${info.project.name} lead: ${leadId}`);
  await expect
    .poll(
      async () => {
        const lead = await (
          await page.request.get(`/api/v1/admin/leads/${leadId}`)
        ).json();
        return lead.messages.some(
          (message: { state: string }) => message.state === "pending_approval",
        );
      },
      { timeout: 60000, intervals: [1000, 2000, 4000] },
    )
    .toBe(true);
  await page.goto(`/#/app/leads/${leadId}`);
  await expect(
    page.getByRole("heading", { name: "Понятный приоритет" }),
  ).toBeVisible();
  await page.getByRole("button", { name: /^Переписка/ }).click();
  await expect(page.getByText(/Основа: черновик по шаблону/)).toBeVisible();
  await expect(page.getByText("Ждёт одобрения", { exact: true })).toBeVisible();
  await page
    .getByRole("button", { name: "Редактировать", exact: true })
    .click();
  await page
    .getByRole("textbox", { name: "Тема", exact: true })
    .fill("Уточним задачу — синтетическая проверка");
  await page
    .getByRole("textbox", { name: "Текст письма", exact: true })
    .fill(
      "Спасибо за обращение. Уточним текущий процесс и желаемый результат. Это синтетический тест, реальная отправка не выполняется.",
    );
  await page.getByRole("button", { name: "Сохранить новую версию" }).click();
  await expect(page.getByText(/Версия 2/)).toBeVisible();
  await page.getByRole("button", { name: "Проверить и одобрить" }).click();
  await expect(
    page.getByRole("heading", { name: "Одобрить отправку версии 2?" }),
  ).toBeVisible();
  await page.screenshot({
    path: `test-results-processing/processing-before-approval-${info.project.name}.png`,
    fullPage: true,
  });
  await page
    .getByRole("button", { name: "Одобрить и поставить в очередь" })
    .click();
  await expect
    .poll(
      async () => {
        const lead = await (
          await page.request.get(`/api/v1/admin/leads/${leadId}`)
        ).json();
        return lead.messages.some(
          (message: { state: string; current_version: { revision: number } }) =>
            message.state === "provider_accepted" &&
            message.current_version.revision === 2,
        );
      },
      { timeout: 60000, intervals: [1000, 2000, 4000] },
    )
    .toBe(true);
  await page
    .getByRole("button", { name: "Обновить карточку", exact: true })
    .click();
  await expect(
    page.getByText("Принято провайдером", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Проверить и одобрить" }),
  ).toHaveCount(0);
  await page.screenshot({
    path: `test-results-processing/processing-accepted-${info.project.name}.png`,
    fullPage: true,
  });
});
