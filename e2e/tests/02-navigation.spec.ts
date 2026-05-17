import { test, expect } from "@playwright/test";
import { installApiMocks, resetAppState } from "../helpers/mocks";
import { expectActivePanel, goToStep } from "../helpers/navigation";

test.describe("Wizard navigation", () => {
  test.beforeEach(async ({ page }) => {
    await resetAppState(page);
    await installApiMocks(page);
    await page.goto("/");
  });

  test("sidebar shows all seven steps", async ({ page }) => {
    const labels = [
      "Setup",
      "Create / Load",
      "Validate",
      "Review",
      "Test",
      "Push to repo",
      "Install",
    ];
    for (let i = 0; i < labels.length; i++) {
      await expect(page.locator(`#nav-${i}`)).toContainText(labels[i]);
    }
  });

  test("can visit every panel via sidebar", async ({ page }) => {
    for (let i = 0; i <= 6; i++) {
      await goToStep(page, i);
    }
    await expect(page.locator("#panel-6 h1")).toHaveText("Install skills");
  });

  test("create / load tab toggle works", async ({ page }) => {
    await goToStep(page, 1);
    await expect(page.locator("#pane-create")).toBeVisible();
    await page.locator("#tab-load").click();
    await expect(page.locator("#load-pr-field")).toBeVisible();
    await page.locator("#tab-create").click();
    await expect(page.locator("#f-name")).toBeVisible();
  });

  test("push panel mode toggle (new vs update PR)", async ({ page }) => {
    await goToStep(page, 5);
    await expect(page.locator("#new-pr-fields")).toBeVisible();
    await page.locator("#mode-update").click();
    await expect(page.locator("#update-pr-fields")).toBeVisible();
    await page.locator("#mode-new").click();
    await expect(page.locator("#pr-title")).toBeVisible();
  });
});
