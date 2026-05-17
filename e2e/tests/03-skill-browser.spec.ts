import { test, expect } from "@playwright/test";
import { installApiMocks, resetAppState } from "../helpers/mocks";

test.describe("Skills browser overlay", () => {
  test.beforeEach(async ({ page }) => {
    await resetAppState(page);
    await installApiMocks(page);
    await page.goto("/");
  });

  test("opens catalog from sidebar and loads SKILL_INDEX", async ({ page }) => {
    await page.locator("#browse-btn").click();
    await expect(page.locator("#skill-browser-overlay")).toBeVisible();

    await expect(page.locator("#browser-total")).not.toHaveText("", {
      timeout: 20_000,
    });
    const total = await page.locator("#browser-total").textContent();
    expect(Number.parseInt(total ?? "0", 10)).toBeGreaterThan(0);

    await expect(page.locator(".skill-card").first()).toBeVisible();
  });

  test("search and category filter reduce results", async ({ page }) => {
    await page.locator("#browse-btn").click();
    await expect(page.locator(".skill-card").first()).toBeVisible({
      timeout: 20_000,
    });

    const before = await page.locator(".skill-card").count();
    await page.locator("#browser-search").fill("analyze-project");
    await expect(page.locator(".skill-card")).toHaveCount(1, { timeout: 10_000 });

    await page.locator("#browser-search").fill("");
    await page.locator("#browser-cat").selectOption("jira");
    const jiraCount = await page.locator(".skill-card").count();
    expect(jiraCount).toBeGreaterThan(0);
    expect(jiraCount).toBeLessThan(before);
  });

  test("closes overlay with Escape", async ({ page }) => {
    await page.locator("#browse-btn").click();
    await expect(page.locator("#skill-browser-overlay")).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(page.locator("#skill-browser-overlay")).toBeHidden();
  });
});
