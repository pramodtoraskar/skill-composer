export const MOCK_SKILL_SLUG = "e2e-playwright-skill";

export const MOCK_SKILL_MARKDOWN = `---
name: ${MOCK_SKILL_SLUG}
description: >-
  E2E Playwright skill for release Slack comms. Use when testing Skills Composer
  or when the user mentions playwright e2e smoke tests.
---

# ${MOCK_SKILL_SLUG}

## When to use

Use when running automated end-to-end tests against Skills Composer locally.

## Procedure

1. Fill the create form.
2. Generate SKILL.md.
3. Validate, review, test, and push (mocked).

## References

See \`references/REFERENCE.md\` for eval handoff.
`;

export const MOCK_GENERATE_RESPONSE = {
  slug: MOCK_SKILL_SLUG,
  skill: MOCK_SKILL_MARKDOWN,
  issues: [] as { field: string; level: string; message: string }[],
};

export const MOCK_TEST_RESPONSE = {
  response: "E2E mock: skill context applied. This is a simulated Claude reply.",
};
