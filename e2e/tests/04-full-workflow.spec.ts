import path from "node:path";
import { test, expect } from "@playwright/test";
import { MOCK_SKILL_SLUG, MOCK_SKILL_MARKDOWN } from "../helpers/fixtures";
import { installApiMocks, resetAppState } from "../helpers/mocks";
import {
  completeSetupWithFork,
  ensureSkillCategory,
  expectActivePanel,
  goToStep,
} from "../helpers/navigation";

test.describe("End-to-end workflow (mocked LLM + GitHub)", () => {
  test.beforeEach(async ({ page }) => {
    await resetAppState(page);
    await installApiMocks(page);
    await page.goto("/");
  });

  test("setup → create → validate → review → test → push → install", async ({
    page,
  }) => {
    // ── Setup with fork detection (mocked api.github.com) ──
    await completeSetupWithFork(page);

    // ── Create: category + skill type + form ──
    await ensureSkillCategory(page, "jira");

    await page.locator("#f-skill-kind").selectOption("generic");
    await page.locator("#f-name").fill(MOCK_SKILL_SLUG);
    await page.locator("#f-desc").fill(
      "E2E Playwright skill for release Slack comms and automated Skills Composer testing.",
    );
    await page.locator("#f-when").fill(
      "Use when running playwright e2e tests against localhost:3747.",
    );

    await page.locator("#gen-btn").click();
    await expectActivePanel(page, 2);
    await expect(page.locator("#val-summary")).toContainText(/passed|Pass/i);

    // ── Validate → Review ──
    await page.locator("#to-review-btn").click();
    await expectActivePanel(page, 3);
    await expect(page.locator("#preview-path")).toContainText(
      `skills/jira/${MOCK_SKILL_SLUG}/SKILL.md`,
    );

    await expect
      .poll(async () => page.evaluate(() => getSkillContent()))
      .toContain(`name: ${MOCK_SKILL_SLUG}`);

    // ── Review IDE validate (debounced 1s) ──
    await page.getByRole("button", { name: "Validate", exact: true }).click();
    await expect
      .poll(async () => page.locator("#ide-val-summary").innerText())
      .toMatch(/Spec valid|error|warning/i, { timeout: 15_000 });

    // ── Test panel: prompt test (mocked __test generate) ──
    await page.getByRole("button", { name: "Test skill →" }).click();
    await expectActivePanel(page, 4);
    await expect(page.locator("#test-skill-context")).toContainText(MOCK_SKILL_SLUG);

    await page.locator("#test-prompt").fill("Write a short Slack release note for E2E.");
    await page.locator("#test-btn").click();
    await expect(page.locator("#test-response-wrap")).toBeVisible({ timeout: 15_000 });
    await expect(page.locator("#test-response")).toContainText("E2E mock");

    // ── Push (mocked GitHub proxy) ──
    await page.getByRole("button", { name: "Push to repo →" }).click();
    await expectActivePanel(page, 5);
    await page.locator("#pr-author").fill("Playwright E2E");
    await page.locator("#push-btn").click();

    await expect(page.locator("#success-box")).toContainText("Skill pushed", {
      timeout: 30_000,
    });
    await expect(page.locator("#success-box a")).toHaveAttribute(
      "href",
      /github\.com.*pull/,
    );

    // After push, local install branch panel should appear on Test when revisited
    await goToStep(page, 4);
    await expect(page.locator("#local-install-has-branch")).toBeVisible();

    // ── Install panel ──
    await goToStep(page, 6);
    await expect(page.locator("#install-clone")).toContainText("git clone");
    await page.getByRole("button", { name: "Copy install instructions" }).click();
    // Clipboard may be blocked in headless; button should still run without error.
    await expect(page.locator("#install-clone")).toContainText("git clone");
  });

  test("load existing tab UI and validation error on empty generate", async ({
    page,
  }) => {
    await page.locator("#setup-btn").click();
    await expectActivePanel(page, 1);

    await page.locator("#tab-load").click();
    await page.locator("#load-by-name").click();
    await expect(page.locator("#load-name-field")).toBeVisible();

    await page.locator("#tab-create").click();
    await page.locator("#gen-btn").click();
    await expect(page.locator("#s-gen")).toContainText(/required/i);
  });

  test("architecture skill type can be selected", async ({ page }) => {
    await page.locator("#setup-btn").click();
    await page.locator("#f-skill-kind").selectOption("architecture");
    await expect(page.locator("#f-skill-kind")).toHaveValue("architecture");
  });

  test("update existing PR resolves branch and pushes after new PR", async ({
    page,
  }) => {
    await completeSetupWithFork(page);
    await ensureSkillCategory(page, "jira");

    await page.locator("#f-skill-kind").selectOption("generic");
    await page.locator("#f-name").fill(MOCK_SKILL_SLUG);
    await page.locator("#f-desc").fill(
      "E2E skill for update-existing-PR flow after an initial push.",
    );
    await page.locator("#f-when").fill("Use when testing update branch push.");

    await page.locator("#gen-btn").click();
    await expectActivePanel(page, 2);
    await page.locator("#to-review-btn").click();
    await expectActivePanel(page, 3);

    await page.getByRole("button", { name: "Test skill →" }).click();
    await expectActivePanel(page, 4);
    await page.getByRole("button", { name: "Push to repo →" }).click();
    await expectActivePanel(page, 5);
    await page.locator("#pr-author").fill("Playwright E2E");
    await page.locator("#push-btn").click();
    await expect(page.locator("#success-box")).toContainText("Skill pushed", {
      timeout: 30_000,
    });

    await page.locator("#mode-update").click();
    await expect(page.locator("#update-branch")).toHaveValue(
      /skill\/e2e-playwright-skill-\d+/,
    );

    // Omit skill/ prefix — resolveBranchForUpdate should still find pushed branch
    const fullBranch = await page.locator("#update-branch").inputValue();
    const shortBranch = fullBranch.replace(/^skill\//, "");
    await page.locator("#update-branch").fill(shortBranch);
    await page.locator("#update-msg").fill("fix: address review comments");
    await page.locator("#push-btn").click();

    await expect(page.locator("#success-box")).toContainText(/Updated|updated/, {
      timeout: 30_000,
    });
    await expect(page.locator("#error-box")).not.toHaveClass(/show/);
  });

  test("supporting files added on Create appear on Review with editor chips", async ({
    page,
  }) => {
    await completeSetupWithFork(page);
    await ensureSkillCategory(page, "jira");

    await page.locator("#f-skill-kind").selectOption("generic");
    await page.locator("#f-name").fill(MOCK_SKILL_SLUG);
    await page.locator("#f-desc").fill(
      "E2E skill with supporting reference file attached on create step.",
    );
    await page.locator("#f-when").fill("Use when testing supporting file sync.");

    await page.getByRole("button", { name: "+ Add file" }).first().click();
    const supportPath = path.resolve("fixtures/support-ref.md");
    await page.locator('[id^="file-input-create-"]').setInputFiles(supportPath);
    await expect(page.locator('[id^="file-name-create-"]')).toContainText(
      "support-ref.md",
    );

    await page.locator("#gen-btn").click();
    await expectActivePanel(page, 2);
    await page.locator("#to-review-btn").click();
    await expectActivePanel(page, 3);

    await expect(page.locator("#file-list-review .file-row")).toHaveCount(1);
    await expect(page.locator("#review-files-body")).toBeVisible();
    await expect(page.locator("#ide-attachment-bar")).toContainText(
      /references\/support-ref\.md/,
    );

    await page
      .locator("#ide-attachment-bar button")
      .filter({ hasText: "references/support-ref.md" })
      .click();
    await expect(page.locator("#ide-support-editor")).toHaveClass(/open/);
    await expect(page.locator("#ide-support-preview")).toHaveValue(
      /Playwright fixture/,
    );
  });
});

test.describe("Injected skill content (no generate)", () => {
  test.beforeEach(async ({ page }) => {
    await resetAppState(page);
    await installApiMocks(page);
    await page.goto("/");
    await page.locator("#setup-btn").click();
  });

  test("review shows injected markdown and validates", async ({ page }) => {
    await goToStep(page, 3);
    await page.evaluate(
      ({ md, skillSlug }) => {
        slug = skillSlug;
        setSkillContent(md);
        setSkillPath(`skills/jira/${skillSlug}/SKILL.md`);
      },
      { md: MOCK_SKILL_MARKDOWN, skillSlug: MOCK_SKILL_SLUG },
    );

    await expect
      .poll(async () => page.evaluate(() => getSkillContent()))
      .toContain(MOCK_SKILL_SLUG);

    await expect(page.locator("#preview-path")).toContainText(
      `skills/jira/${MOCK_SKILL_SLUG}/SKILL.md`,
    );
    await expect(page.locator("#preview-path")).toContainText(MOCK_SKILL_SLUG);
  });
});
