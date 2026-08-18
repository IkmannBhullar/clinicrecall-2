/**
 * Playwright configuration (SPEC §11).
 *
 * Runs the thirteen-step demo walkthrough and an accessibility pass, and is a gate in
 * `make verify` — so it must be able to start the whole stack itself rather than assuming
 * someone remembered to run `make dev` first.
 *
 * Deliberately **one worker and no retries**. Both are unusual defaults, and both are right here:
 *
 * - The walkthrough is a *sequence*. Step 6 sends a reminder that step 7 checks arrived; step 8
 *   books an appointment that step 9 sees on the dashboard. Running those in parallel, or
 *   retrying step 8 after step 9 already ran, would test something that never happens.
 * - A retry that turns a red run green is a flake being hidden. If this suite fails, the demo
 *   would have failed too, and that is the thing worth knowing.
 */

import { defineConfig, devices } from "@playwright/test";

const WEB_URL = "http://localhost:3000";
const API_URL = "http://127.0.0.1:8000";

export default defineConfig({
  testDir: "./e2e",

  // The walkthrough is a single ordered story; parallelism would break its causality.
  fullyParallel: false,
  workers: 1,
  retries: 0,

  // Generous: the first step waits on a real sign-in against a real auth server.
  timeout: 60_000,
  expect: { timeout: 10_000 },

  // Fail the run if a `test.only` is committed — otherwise `make verify` would pass while
  // silently running one test.
  forbidOnly: true,

  reporter: [["list"], ["html", { outputFolder: "playwright-report", open: "never" }]],

  use: {
    baseURL: WEB_URL,
    // A desktop viewport: SPEC §10 is desktop-first, and the screenshots land in the README.
    viewport: { width: 1440, height: 900 },
    trace: "retain-on-failure",
    video: "off",
    screenshot: "only-on-failure",
  },

  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],

  /**
   * Start the stack.
   *
   * `reuseExistingServer` so a developer with `make dev` already running does not get a second
   * pair of servers fighting for the ports — and so an interactive debugging session keeps its
   * own logs.
   */
  webServer: [
    {
      command: "uv --directory ../api run uvicorn app.main:app --host 127.0.0.1 --port 8000",
      url: `${API_URL}/health`,
      reuseExistingServer: true,
      timeout: 120_000,
      stdout: "pipe",
      stderr: "pipe",
    },
    {
      command: "pnpm dev",
      url: `${WEB_URL}/sign-in`,
      reuseExistingServer: true,
      timeout: 120_000,
      stdout: "pipe",
      stderr: "pipe",
    },
  ],
});
