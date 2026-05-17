/**
 * Single visual walkthrough for README demo video (mocked APIs).
 * Record: bash scripts/record-demo.sh
 */
import path from "node:path";
import { test, expect } from "@playwright/test";

const POSTER = path.resolve(__dirname, "../../docs/demo-poster.png");
import { installApiMocks, resetAppState } from "../helpers/mocks";
import {
  completeSetupWithFork,
  ensureSkillCategory,
  expectActivePanel,
  goToStep,
} from "../helpers/navigation";

const pause = (ms: number) => new Promise((r) => setTimeout(r, ms));

test.describe("README demo recording", () => {
  test.beforeEach(async ({ page }) => {
    await resetAppState(page);
    await installApiMocks(page);
    await page.goto("/");
  });

  test("skill composer walkthrough", async ({ page }) => {
    test.setTimeout(180_000);

    // ── 1 Setup ──
    await completeSetupWithFork(page);
    await pause(900);

    // ── 2 Create ──
    await ensureSkillCategory(page, "jira");
    await page.locator("#f-skill-kind").selectOption("generic");
    await page.locator("#f-name").fill("release-slack-comms");
    await pause(400);
    await page.locator("#f-desc").fill(
      "Draft upbeat Slack announcements for product releases—what shipped, why it matters, and the user impact.",
    );
    await pause(400);
    await page.locator("#f-when").fill(
      "Use when writing release comms, changelog posts, or user-facing Slack updates.",
    );
    await pause(600);
    await page.locator("#gen-btn").click();
    await expectActivePanel(page, 2);
    await expect(page.locator("#val-summary")).toContainText(/passed|Pass/i);
    await pause(800);

    // ── 3 Validate → Review ──
    await page.locator("#to-review-btn").click();
    await expectActivePanel(page, 3);
    await expect(page.locator("#preview-path")).toContainText("SKILL.md");
    await expect(page.locator("#monaco-container")).toBeVisible({ timeout: 15_000 });
    await pause(1400);

    // ── 4 Test (short) ──
    await page.getByRole("button", { name: "Test skill →" }).click();
    await expectActivePanel(page, 4);
    await pause(500);
    await page.locator("#test-prompt").fill("Draft a short release note for our Q2 launch.");
    await pause(400);
    await page.locator("#test-btn").click();
    await expect(page.locator("#test-response-wrap")).toBeVisible({ timeout: 15_000 });
    await pause(900);

    // ── 5 Push ──
    await page.getByRole("button", { name: "Push to repo →" }).click();
    await expectActivePanel(page, 5);
    await page.locator("#pr-author").fill("Skill Composer");
    await pause(500);
    await page.locator("#push-btn").click();
    await expect(page.locator("#success-box")).toContainText("Skill pushed", {
      timeout: 30_000,
    });
    await pause(1500);

    // ── 6 Install (closing frame) ──
    await goToStep(page, 6);
    await expect(page.locator("#install-clone")).toContainText("git clone");
    await pause(800);

    // Poster frame for README <video poster="">
    await page.screenshot({ path: POSTER, fullPage: false });
  });
});
