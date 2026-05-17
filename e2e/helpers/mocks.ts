import fs from "node:fs";
import path from "node:path";
import type { Page, Route } from "@playwright/test";
import {
  MOCK_GENERATE_RESPONSE,
  MOCK_SKILL_MARKDOWN,
  MOCK_TEST_RESPONSE,
} from "./fixtures";

const SKILL_INDEX_PATH = path.resolve(
  process.cwd(),
  "../data/SKILL_INDEX.json",
);

function loadSkillIndexPayload(): Record<string, unknown> {
  const raw = fs.readFileSync(SKILL_INDEX_PATH, "utf8");
  const data = JSON.parse(raw) as { skills?: unknown[] } | unknown[];
  if (Array.isArray(data)) return { version: "1.0", skills: data };
  return data as Record<string, unknown>;
}

/** Mock Anthropic generate + GitHub (direct API + /api/github proxy) for E2E. */
export async function installApiMocks(page: Page): Promise<void> {
  await page.route("**/api/skill-index", async (route: Route) => {
    if (route.request().method() !== "GET") {
      await route.continue();
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(loadSkillIndexPayload()),
    });
  });

  await page.route("**/api/config", async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        anthropicKeyConfigured: true,
        model: "claude-sonnet-4-20250514",
        maxTokens: 8192,
      }),
    });
  });

  await page.route("https://api.github.com/**", async (route: Route) => {
    const req = route.request();
    const method = req.method();
    const url = req.url();
    let body: Record<string, unknown> | undefined;
    try {
      body = req.postDataJSON() as Record<string, unknown>;
    } catch {
      body = undefined;
    }
    const payload = await githubMockResponse(method, url, body);
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(payload),
    });
  });

  await page.route("**/api/generate", async (route: Route) => {
    const req = route.request();
    if (req.method() !== "POST") {
      await route.continue();
      return;
    }
    let body: Record<string, unknown> = {};
    try {
      body = req.postDataJSON() as Record<string, unknown>;
    } catch {
      body = {};
    }
    if (body.__test) {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(MOCK_TEST_RESPONSE),
      });
      return;
    }
    if (body.__chat) {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          response:
            "E2E mock: validation issues are listed in the Validate step; SKILL.md looks structurally fine.",
          fileEdits: [],
        }),
      });
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(MOCK_GENERATE_RESPONSE),
    });
  });

  await page.route("**/api/github", async (route: Route) => {
    const req = route.request();
    if (req.method() !== "POST") {
      await route.continue();
      return;
    }
    const proxy = req.postDataJSON() as {
      method?: string;
      url?: string;
      body?: Record<string, unknown>;
    };
    const method = (proxy.method ?? "GET").toUpperCase();
    const url = proxy.url ?? "";
    const payload = await githubMockResponse(method, url, proxy.body);
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(payload),
    });
  });
}

async function githubMockResponse(
  method: string,
  url: string,
  body?: Record<string, unknown>,
): Promise<unknown> {
  const login = "e2e-tester";
  const forkRepo = "e2e-tester/skills";
  const upstream = "anthropics/skills";

  if (url.includes("/user") && method === "GET") {
    return { login };
  }
  if (
    url.includes(`/repos/${upstream}`) &&
    method === "GET" &&
    !url.includes("/git/") &&
    !url.includes("/contents")
  ) {
    return { full_name: upstream, default_branch: "main" };
  }
  if (
    url.includes(`/repos/${forkRepo}`) &&
    method === "GET" &&
    !url.includes("/git/") &&
    !url.includes("/contents")
  ) {
    return { full_name: forkRepo, default_branch: "main", fork: true };
  }
  if (url.includes("/forks") && method === "POST") {
    return { full_name: forkRepo };
  }
  if (url.includes("/git/ref/heads/") && method === "GET") {
    return { object: { sha: "abc123deadbeef" } };
  }
  if (url.includes("/git/trees/") && method === "GET") {
    return {
      tree: [
        {
          path: "skills/jira/e2e-playwright-skill/references/REFERENCE.md",
          type: "blob",
          sha: "treesha1",
        },
      ],
    };
  }
  if (url.includes("/git/refs") && method === "POST") {
    return { ref: "refs/heads/skill/e2e-playwright-skill-1", object: { sha: "abc123" } };
  }
  if (url.includes("/contents/") && method === "GET") {
    return {
      sha: "filesha123",
      content: Buffer.from(MOCK_SKILL_MARKDOWN).toString("base64"),
    };
  }
  if (url.includes("/contents/") && method === "PUT") {
    return {
      content: {
        path: (body?.message as string) ?? "skills/e2e-playwright-skill/SKILL.md",
      },
    };
  }
  if (url.includes("/pulls") && method === "POST") {
    return {
      html_url: "https://github.com/e2e-tester/skills/pull/42",
      number: 42,
    };
  }
  if (url.match(/\/pulls\/\d+$/) && method === "GET") {
    return {
      number: 42,
      title: "skill: e2e-playwright-skill",
      html_url: "https://github.com/e2e-tester/skills/pull/42",
      head: {
        ref: "skill/e2e-playwright-skill-1778903200417",
        repo: { full_name: forkRepo },
      },
    };
  }
  if (url.includes("/pulls?") && method === "GET") {
    return [
      {
        number: 42,
        html_url: "https://github.com/e2e-tester/skills/pull/42",
      },
    ];
  }
  return {};
}

/** Clear persisted UI config between tests. */
export async function resetAppState(page: Page): Promise<void> {
  await page.addInitScript(() => {
    localStorage.removeItem("skills-composer-cfg");
  });
}
