import { expect, type Page } from "@playwright/test";

export async function expectActivePanel(page: Page, index: number): Promise<void> {
  await expect(page.locator(`#panel-${index}`)).toHaveClass(/active/);
}

export async function goToStep(page: Page, index: number): Promise<void> {
  await page.locator(`#nav-${index}`).click();
  await expectActivePanel(page, index);
}

export async function completeSetupMinimal(page: Page): Promise<void> {
  // No token: app saves upstream/path and advances without calling GitHub.
  await page.locator("#gh-upstream").fill("anthropics/skills");
  await page.locator("#gh-path").fill("skills/jira/");
  await page.locator("#setup-btn").click();
  await expectActivePanel(page, 1);
}

export async function completeSetupWithFork(page: Page): Promise<void> {
  await page.locator("#gh-token").fill("ghp_e2e_mock_token");
  await page.locator("#gh-upstream").fill("anthropics/skills");
  await page.locator("#gh-path").fill("skills/jira/");
  await page.locator("#setup-btn").click();
  await expect(page.locator("#fork-status-box")).toBeVisible({ timeout: 20_000 });
  await expectActivePanel(page, 1);
}

/** Keep skills/<category>/ path before generate() (generate() calls onSkillCategoryChange first). */
export async function ensureSkillCategory(
  page: Page,
  category: string,
): Promise<void> {
  const prefix = `skills/${category}/`;
  await page.evaluate(
    ({ cat, pathPrefix }) => {
      cfg.path = pathPrefix;
      const sel = document.getElementById("f-skill-category");
      if (sel) {
        let opt = [...sel.options].find((o) => o.value === cat);
        if (!opt) {
          opt = document.createElement("option");
          opt.value = cat;
          opt.textContent = `${cat} — ${pathPrefix}`;
          sel.appendChild(opt);
        }
        sel.value = cat;
        if (typeof onSkillCategoryChange === "function") onSkillCategoryChange();
      }
      if (typeof persistConfig === "function") persistConfig();
    },
    { cat: category, pathPrefix: prefix },
  );
  await expect(page.locator("#f-skill-category")).toHaveValue(category);
}
