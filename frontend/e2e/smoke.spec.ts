import { expect, test, type Page } from "@playwright/test";

// Every test watches for console errors and uncaught exceptions; any one fails the test.
function watchErrors(page: Page): string[] {
  const errors: string[] = [];
  page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
  page.on("pageerror", (e) => errors.push(String(e)));
  return errors;
}

async function skipTour(page: Page) {
  await page.addInitScript(() => localStorage.setItem("atlas_tour_completed", "true"));
}

async function runReplay(page: Page, replayId = "sample_synthetic") {
  await page.goto("/?speed=instant");
  await page.waitForSelector('select[aria-label="Replay company"] option', { state: "attached" });
  await page.locator('select[aria-label="Replay company"]').selectOption(replayId);
  await page.getByRole("button", { name: /Run research/ }).click();
  await expect(page.getByRole("link", { name: "Export PDF" })).toBeVisible({ timeout: 20_000 });
}

/** The workings view (stage tracker and scrubber) is off by default. */
async function showWorkings(page: Page) {
  await page.getByRole("button", { name: "How it works" }).click();
  await expect(page.getByText(/Event (\d+) of \1/)).toBeVisible();
}

test("health and replay list", async ({ request }) => {
  const health = await (await request.get("/api/v1/health")).json();
  expect(health.status).toBe("ok");
  expect(health.sqlite_wal).toBe(true);
  const { replays } = await (await request.get("/api/v1/replays")).json();
  expect(replays.length).toBeGreaterThan(0);
});

test("the guided tour opens from the header and can be finished", async ({ page }) => {
  const errors = watchErrors(page);
  await page.goto("/");
  await page.waitForTimeout(800);
  await expect(page.locator("#tour-title")).toHaveCount(0);
  await page.getByRole("button", { name: "Guided tour" }).click();
  await expect(page.locator("#tour-title")).toBeVisible({ timeout: 10_000 });
  for (let i = 0; i < 7; i++) await page.getByRole("button", { name: "Next" }).click();
  await page.getByRole("button", { name: "Finish" }).click();
  await expect(page.locator("#tour-title")).toHaveCount(0);
  await page.reload();
  await page.waitForTimeout(800);
  await expect(page.locator("#tour-title")).toHaveCount(0);
  expect(errors).toEqual([]);
});

test("a replay streams to completion and every tab renders", async ({ page }) => {
  const errors = watchErrors(page);
  await skipTour(page);
  await runReplay(page);
  await expect(page.locator("#verdict-h")).not.toBeEmpty();
  await expect(page.getByText(/^Synthetic sample:/)).toBeVisible();
  expect(await page.locator(".kp").count()).toBeGreaterThan(3);
  for (const tab of ["Financials", "News", "Sentiment", "Market", "Insights", "AI at work", "Method", "Summary"]) {
    await page.getByRole("tab", { name: tab }).click();
    await expect(page.getByRole("tabpanel")).not.toBeEmpty();
  }
  await page.getByRole("tab", { name: "Financials" }).click();
  await expect(page.locator('[data-tour-id="calculation-ledger"]')).toContainText("DSO");
  expect(errors).toEqual([]);
});

test("a seed replay carries the unconfirmed banner", async ({ page }) => {
  const errors = watchErrors(page);
  await skipTour(page);
  await runReplay(page, "block_nyse_seed");
  await expect(page.getByText(/^Unconfirmed seed figures:/)).toBeVisible();
  await expect(page.locator("#verdict-h")).not.toBeEmpty();
  expect(errors).toEqual([]);
});

test("explain mode: show the math and open the evidence drawer", async ({ page }) => {
  const errors = watchErrors(page);
  await skipTour(page);
  await runReplay(page);
  await page.locator(".kp button[data-metric-id]").first().hover();
  await expect(page.getByRole("tooltip").first()).toContainText("Show the math");
  await page.locator("[data-token]").first().click();
  const drawer = page.getByRole("dialog");
  await expect(drawer).toBeVisible();
  await expect(drawer).toContainText(/T1|T2|T3|T4/);
  await page.keyboard.press("Escape");
  await expect(drawer).toHaveCount(0);
  expect(errors).toEqual([]);
});

test("the timeline scrubber rewinds the report", async ({ page }) => {
  await skipTour(page);
  await runReplay(page);
  await showWorkings(page);
  await page.locator('input[type="range"]').fill("3");
  await expect(page.getByText(/Event 4 of/)).toBeVisible();
  await expect(page.locator("#verdict-h")).toHaveCount(0);
});

test("the first page is just a picker, and the AI view tells the GenAI story", async ({ page }) => {
  const errors = watchErrors(page);
  await skipTour(page);
  await page.goto("/");
  await expect(page.getByRole("tablist")).toHaveCount(0);
  await expect(page.getByRole("button", { name: /Run research/ })).toBeVisible();
  await runReplay(page);
  await page.getByRole("tab", { name: "AI at work" }).click();
  await expect(page.getByRole("tabpanel")).toContainText("Proactive intelligence");
  await expect(page.getByRole("tabpanel")).toContainText("red flags raised");
  expect(errors).toEqual([]);
});

test("the clean report hides the workings and exports markdown and pdf", async ({ page }) => {
  await skipTour(page);
  await runReplay(page);
  await expect(page.locator('input[type="range"]')).toHaveCount(0);
  for (const [label, ext] of [["Export Markdown", ".md"], ["Export PDF", ".pdf"]] as const) {
    const [dl] = await Promise.all([page.waitForEvent("download"), page.getByRole("link", { name: label }).click()]);
    expect(dl.suggestedFilename()).toMatch(new RegExp(`^atlas_.*\\${ext}$`));
  }
});

test("live mode is unavailable without an API key, and the API says why", async ({ page, request }) => {
  await skipTour(page);
  await page.goto("/");
  const live = page.getByRole("button", { name: "Live", exact: true });
  await expect(live).toBeDisabled();
  await expect(live).toHaveAttribute("title", /not configured/);
  const r = await request.post("/api/v1/runs", { data: { company_query: "Block, Inc.", mode: "live" } });
  expect(r.status()).toBe(503);
  expect((await r.json()).error_code).toBe("LLM_NOT_CONFIGURED");
});

test("phone width has no horizontal scroll", async ({ page }) => {
  const errors = watchErrors(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await skipTour(page);
  await runReplay(page);
  for (const tab of ["Summary", "Financials", "Insights", "Market"]) {
    await page.getByRole("tab", { name: tab }).click();
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  }
  expect(errors).toEqual([]);
});
