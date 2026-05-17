import { defineConfig, devices } from "@playwright/test";

const baseURL = process.env.PLAYWRIGHT_BASE_URL ?? "http://localhost:3747";

/** Record README demo: e2e/tests/demo-recording.spec.ts → docs/demo.webm */
export default defineConfig({
  testDir: "./tests",
  testMatch: "demo-recording.spec.ts",
  fullyParallel: false,
  workers: 1,
  timeout: 180_000,
  expect: { timeout: 20_000 },
  reporter: "list",
  outputDir: "./demo-output",
  use: {
    baseURL,
    video: "on",
    screenshot: "off",
    trace: "off",
    viewport: { width: 1280, height: 720 },
    deviceScaleFactor: 1,
    launchOptions: { slowMo: 0 },
    ...devices["Desktop Chrome"],
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: process.env.PLAYWRIGHT_SKIP_WEBSERVER
    ? undefined
    : {
        command: "bash ../serve-app.sh --port 3747 --no-browser",
        url: baseURL,
        reuseExistingServer: true,
        timeout: 120_000,
      },
});
