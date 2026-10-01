import { test, expect } from "@playwright/test";

test("public navigation returns from demo to the compact workflow and dismisses at phone/tablet widths", async ({
  page,
}, info) => {
  await page.goto("/#/demo");
  await expect(
    page.getByText("Синтетические данные · только просмотр"),
  ).toBeVisible();

  if (info.project.name === "mobile") {
    await page.getByRole("button", { name: "Открыть меню" }).click();
    await page
      .getByRole("navigation", { name: "Мобильная навигация" })
      .getByRole("link", { name: "Как работает", exact: true })
      .click();
  } else {
    await page
      .getByRole("navigation", { name: "Основная навигация" })
      .getByRole("link", { name: "Как работает", exact: true })
      .click();
  }
  await expect(page).toHaveURL(/#workflow$/);
  await expect(page.locator("#workflow")).toBeInViewport();
  await expect(page.locator(".lf-story")).toBeVisible();
  await expect(page.locator(".demo-page")).toHaveCount(0);

  for (const width of [320, 768]) {
    await page.setViewportSize({ width, height: 900 });
    const toggle = page.getByRole("button", { name: "Открыть меню" });
    await toggle.click();
    const menu = page.getByRole("navigation", { name: "Мобильная навигация" });
    await menu.getByRole("link", { name: "Как работает", exact: true }).focus();
    await page.keyboard.press("Escape");
    await expect(menu).toHaveCount(0);
    await expect(toggle).toBeFocused();
    await expect(toggle).toHaveAttribute("aria-expanded", "false");

    await toggle.click();
    await menu.getByRole("link", { name: "Демо", exact: true }).click();
    await expect(menu).toHaveCount(0);
    await expect(page).toHaveURL(/#\/demo$/);
    await expect(page.locator(".demo-page")).toBeVisible();
    await toggle.click();
    await menu.getByRole("link", { name: "Как работает", exact: true }).click();
    await expect(menu).toHaveCount(0);
    await expect(page).toHaveURL(/#workflow$/);
    await expect(page.locator("#workflow")).toBeInViewport();
    const dimensions = await page.evaluate(() => ({
      scroll: document.documentElement.scrollWidth,
      client: document.documentElement.clientWidth,
    }));
    expect(
      dimensions.scroll,
      `horizontal overflow at ${width}px`,
    ).toBeLessThanOrEqual(dimensions.client);
  }
});

test("compact example cycles three illustrative steps and respects pause and reduced motion without demo requests", async ({
  page,
}, info) => {
  await page.emulateMedia({
    reducedMotion: info.project.name === "desktop" ? "no-preference" : "reduce",
  });
  const requests: { path: string; method: string }[] = [];
  page.on("request", (request) => {
    const path = new URL(request.url()).pathname;
    if (path.startsWith("/api/"))
      requests.push({ path, method: request.method() });
  });
  await page.goto("/#workflow");
  const story = page.locator(".lf-story");
  const steps = story.getByRole("group", { name: "Как работает LeadFlow" });
  const panel = story.getByRole("region", { name: "Пример обработки заявки" });
  await story.scrollIntoViewIfNeeded();
  await expect(
    story.getByText("Пример · без отправки", { exact: true }),
  ).toBeVisible();
  await expect(steps.getByRole("button")).toHaveCount(3);
  await expect(panel).toHaveAttribute("data-step", "0");

  if (info.project.name === "desktop") {
    await expect(story).toHaveAttribute("data-playing", "true");
    await expect
      .poll(() =>
        story.evaluate((element) =>
          element
            .getAnimations({ subtree: true })
            .some((animation) => animation.playState === "running"),
        ),
      )
      .toBe(true);
    await expect(
      page.getByRole("button", { name: "Остановить анимацию", exact: true }),
    ).toHaveAttribute("aria-pressed", "false");
    await expect(panel).toHaveAttribute("data-step", "1", { timeout: 7500 });
    await page
      .getByRole("button", { name: "Остановить анимацию", exact: true })
      .click();
  }

  for (const [index, name, heading] of [
    [0, "01 Заявка приходит", "Новая заявка"],
    [1, "02 AI разбирает задачу", "Главное — на виду"],
    [2, "03 Вы проверяете ответ", "Черновик по шаблону"],
  ] as const) {
    const control = steps.getByRole("button", { name: new RegExp(`^${name}`) });
    await control.click();
    await expect(control).toHaveAttribute("aria-pressed", "true");
    await expect(steps.locator('button[aria-pressed="true"]')).toHaveCount(1);
    await expect(panel).toHaveAttribute("data-step", String(index));
    await expect(
      panel.getByRole("heading", { name: heading, exact: true }),
    ).toBeVisible();
  }

  // Exceed the 4.5 s interval: manual selection must remain paused.
  await expect(story).toHaveAttribute("data-playing", "false");
  await page.waitForTimeout(4800);
  await expect(panel).toHaveAttribute("data-step", "2");
  if (info.project.name === "desktop") {
    const play = page.getByRole("button", {
      name: "Включить анимацию",
      exact: true,
    });
    await expect(play).toHaveAttribute("aria-pressed", "true");
    await play.click();
    await story.scrollIntoViewIfNeeded();
    await expect(story).toHaveAttribute("data-playing", "true");
    await expect(panel).toHaveAttribute("data-step", "0", { timeout: 7500 });
    await page.emulateMedia({ reducedMotion: "reduce" });
  }
  expect(
    await page.evaluate(
      () => matchMedia("(prefers-reduced-motion: reduce)").matches,
    ),
  ).toBe(true);
  await expect(
    page.getByRole("button", { name: "Включить анимацию", exact: true }),
  ).toBeDisabled();
  const reducedStep = await panel.getAttribute("data-step");
  await page.waitForTimeout(4800);
  await expect(panel).toHaveAttribute("data-step", reducedStep!);
  const activeAnimations = await page
    .locator(".lf-page")
    .evaluate(
      (element) =>
        element
          .getAnimations({ subtree: true })
          .filter((animation) => animation.playState === "running").length,
    );
  expect(activeAnimations).toBe(0);
  expect(requests.some(({ path }) => path === "/api/v1/public/demo")).toBe(
    false,
  );
  expect(requests.length).toBeGreaterThan(0);
  expect(requests.every(({ method }) => method === "GET")).toBe(true);
});

// Explicit UI-only response fixtures. This does not verify backend availability.
test("UI contract: unavailable full demo offers a safe retry without fabricated analysis", async ({
  page,
  request,
}) => {
  const response = await request.get("/api/v1/public/demo");
  expect(response.ok()).toBe(true);
  const demo = await response.json();
  let unavailable = true;
  await page.route("**/api/v1/public/demo", (route) =>
    route.fulfill(
      unavailable
        ? {
            status: 503,
            json: { message: "INTERNAL_PREVIEW_FAILURE_SHOULD_NOT_RENDER" },
          }
        : { json: demo },
    ),
  );
  await page.goto("/#/demo");
  const preview = page.locator(".demo-page");
  await expect(preview.getByRole("alert")).toContainText(
    "Сервис временно недоступен",
  );
  await expect(
    preview.getByText("INTERNAL_PREVIEW_FAILURE_SHOULD_NOT_RENDER"),
  ).toHaveCount(0);
  await expect(preview.locator(".demo-result")).toHaveCount(0);
  unavailable = false;
  await preview.getByRole("button", { name: "Повторить", exact: true }).click();
  await expect(preview.getByRole("alert")).toHaveCount(0);
  await expect(preview.locator(".original-message")).toHaveText(
    demo.scenarios[0].input,
  );
  await expect(
    preview.getByRole("button", { name: /одобрить|отправить|сохранить/i }),
  ).toHaveCount(0);
});
