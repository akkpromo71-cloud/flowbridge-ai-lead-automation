import { test, expect } from "@playwright/test";
import { readFileSync } from "node:fs";

test("controlled synthetic intake traverses application/n8n and shows real model", async ({
  page,
}) => {
  const path = process.env.CONTROLLED_OPERATOR_FILE;
  const name = process.env.CONTROLLED_SMOKE_NAME;
  if (!path || !name)
    throw new Error(
      "Run only through app.controlled_smoke hidden-key launcher",
    );
  const credential = JSON.parse(readFileSync(path, "utf8"));
  const fixture = JSON.parse(
    readFileSync("../evaluation/controlled-pipeline-smoke.json", "utf8"),
  );
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
  const config = await (await page.request.get("/api/v1/public/config")).json();
  expect(config.mode).toBe("controlled");
  await page.goto("/#contact");
  await page.getByLabel("Ваше имя").fill(name);
  await page
    .getByLabel("Рабочая почта")
    .fill(`${name.toLowerCase()}@example.com`);
  await page
    .getByLabel("Компания", { exact: true })
    .fill("Синтетическая мастерская печати");
  await page.getByLabel("Что хотите автоматизировать?").fill(fixture.source);
  await page
    .getByRole("button", { name: "Создать тестовую заявку", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "Заявка принята" }),
  ).toBeVisible();
  const list = await (
    await page.request.get(`/api/v1/admin/leads?q=${encodeURIComponent(name)}`)
  ).json();
  expect(list.items).toHaveLength(1);
  const id = list.items[0].id;
  await expect
    .poll(
      async () => {
        const lead = await (
          await page.request.get(`/api/v1/admin/leads/${id}`)
        ).json();
        if (
          !lead.analysis &&
          ["failed", "needs_review"].includes(lead.processing_status)
        )
          throw new Error("Analysis stopped; do not repeat API request");
        return lead.analysis?.model;
      },
      { timeout: 65000, intervals: [500, 1000, 2000] },
    )
    .toBe("gpt-4.1-mini-2025-04-14");
  await expect
    .poll(
      async () => {
        const lead = await (
          await page.request.get(`/api/v1/admin/leads/${id}`)
        ).json();
        return lead.messages?.some(
          (message: { state: string }) => message.state === "pending_approval",
        );
      },
      { timeout: 15000, intervals: [500, 1000] },
    )
    .toBe(true);
  await expect
    .poll(
      async () => {
        const lead = await (
          await page.request.get(`/api/v1/admin/leads/${id}`)
        ).json();
        return lead.processing_status;
      },
      { timeout: 15000, intervals: [500, 1000] },
    )
    .toBe("completed");
  await page.goto(`/#/app/leads/${id}`);
  await expect(page.getByText("Модель gpt-4.1-mini-2025-04-14")).toBeVisible();
  await page.screenshot({
    path: "../.local/controlled-smoke-ui.png",
    fullPage: true,
  });
  const integrations = await (
    await page.request.get("/api/v1/admin/integrations")
  ).json();
  for (const provider of ["smtp", "imap", "telegram"])
    expect(
      integrations.find((x: { name: string }) => x.name === provider)?.state,
    ).toBe("simulated");
});
