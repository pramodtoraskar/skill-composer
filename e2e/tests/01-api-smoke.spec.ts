import { test, expect } from "@playwright/test";

test.describe("API smoke", () => {
  test("GET / serves index.html", async ({ request }) => {
    const res = await request.get("/");
    expect(res.ok()).toBeTruthy();
    const html = await res.text();
    expect(html).toMatch(/Skill(s)? Composer/i);
    expect(html).toContain('id="panel-0"');
  });

  test("GET /api/config returns bootstrap JSON", async ({ request }) => {
    const res = await request.get("/api/config");
    expect(res.ok()).toBeTruthy();
    const data = await res.json();
    expect(data).toHaveProperty("anthropicKeyConfigured");
    expect(typeof data.anthropicKeyConfigured).toBe("boolean");
  });

  test("GET /api/skill-index returns hub catalog", async ({ request }) => {
    const res = await request.get("/api/skill-index");
    expect(res.ok()).toBeTruthy();
    const data = await res.json();
    expect(data.skills?.length).toBeGreaterThan(10);
    expect(data.skills[0]).toMatchObject({
      name: expect.any(String),
      category: expect.any(String),
    });
  });

  test("POST /api/validate accepts minimal skill fields", async ({ request }) => {
    const res = await request.post("/api/validate", {
      data: {
        name: "e2e-validate-skill",
        description:
          "Validates E2E playwright flow for Skills Composer local testing on port 3747.",
        body: "## When to use\n\nFor automated tests only.\n",
        skillKind: "generic",
      },
    });
    expect(res.ok()).toBeTruthy();
    const data = await res.json();
    expect(data).toHaveProperty("valid");
    expect(Array.isArray(data.issues)).toBe(true);
  });
});
