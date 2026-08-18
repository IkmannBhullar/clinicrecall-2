/**
 * Shared helpers for the end-to-end suite.
 */

import { expect, type Page, type TestInfo } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { mkdirSync } from "node:fs";
import path from "node:path";

export const REPO_ROOT = path.resolve(__dirname, "../../..");

/** Screenshots land here and fill the README's placeholders (SPEC §11). */
export const SCREENSHOT_DIR = path.join(REPO_ROOT, "docs", "screenshots");

/** Matches the account the seed creates. */
export const DEMO_EMAIL = "alex.morgan@greenvalley.example.com";
export const DEMO_PASSWORD = "ClinicRecallDemo2026!";

/**
 * Restore pristine demo data.
 *
 * The walkthrough asserts exact numbers — Sarah Johnson has *exactly* two delivered reminders
 * before step 6 sends the third. Those numbers only hold from a clean seed, and the suite itself
 * mutates them, so a previous run would break the next one. Resetting first makes the suite
 * repeatable rather than something that passes once.
 */
export function resetDemoData(): void {
  execFileSync("bash", [path.join(REPO_ROOT, "scripts", "demo-reset.sh")], {
    cwd: REPO_ROOT,
    stdio: "pipe",
    timeout: 120_000,
  });
}

/**
 * Capture a numbered screenshot of one demo step.
 *
 * These are the README's illustrations, so they are full-page and named in demo order — the
 * directory listing reads as the demo script.
 */
export async function captureStep(page: Page, step: number, name: string): Promise<void> {
  mkdirSync(SCREENSHOT_DIR, { recursive: true });
  const file = `${String(step).padStart(2, "0")}-${name}.png`;

  // Settle animations and any in-flight fetch before capturing, so a screenshot never catches a
  // half-rendered skeleton.
  await page.waitForLoadState("networkidle");
  await page.screenshot({ path: path.join(SCREENSHOT_DIR, file), fullPage: true });
}

/**
 * Watch for console errors, and fail the test if any appear (SPEC §11).
 *
 * "no console errors during the E2E run (assert on page.on('console'))". A console error is
 * usually a React warning about a broken key or a failed fetch nobody noticed — the kind of
 * thing that is invisible in a demo right up until it is not.
 *
 * Returns a function to call at the end of the test, so failures are attributed to the test that
 * produced them rather than to whichever one happened to finish last.
 */
export function watchConsole(page: Page): () => void {
  const errors: string[] = [];

  page.on("console", (message) => {
    if (message.type() !== "error") return;

    const text = message.text();

    // Next.js emits this in development when a route is prefetched and then navigated away from.
    // It is a development-server artifact, not a defect in the application.
    if (text.includes("Failed to load resource") && text.includes("_rsc=")) return;

    // The email preview renders the stored message inside `<iframe sandbox="" srcdoc=...>` with
    // no `allow-scripts`, so nothing in that markup can execute. Playwright injects its own
    // instrumentation script into every frame it controls, the sandbox blocks it, and Chromium
    // reports the block here.
    //
    // This is the sandbox working, not a defect: the stored HTML contains no script at all, and
    // a bare `<iframe sandbox="" srcdoc="<p>hello</p>">` under Playwright emits this same message
    // verbatim. Ignoring it is safe because it says a script was *blocked* — the failure mode we
    // would want to catch is script that runs, which would produce no message at all.
    if (text.includes("Blocked script execution in 'about:srcdoc'")) return;

    errors.push(text);
  });

  page.on("pageerror", (error) => {
    errors.push(`Uncaught: ${error.message}`);
  });

  return () => {
    expect(errors, `Console errors during this step:\n${errors.join("\n")}`).toEqual([]);
  };
}

/** Sign in as the demo administrator. */
export async function signIn(page: Page): Promise<void> {
  await page.goto("/sign-in");

  // The demo credentials are pre-filled, but they are typed anyway so the test exercises the real
  // form rather than depending on a convenience that might be removed.
  await page.getByLabel("Email").fill(DEMO_EMAIL);
  await page.getByLabel("Password").fill(DEMO_PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();

  await expect(page).toHaveURL(/\/dashboard/);
}

/** Attach a screenshot to the HTML report as well, so a failure is diagnosable from the report. */
export async function attachScreenshot(
  page: Page,
  testInfo: TestInfo,
  name: string,
): Promise<void> {
  await testInfo.attach(name, {
    body: await page.screenshot({ fullPage: true }),
    contentType: "image/png",
  });
}

/**
 * Click an item in the sidebar navigation.
 *
 * Scoped to the `<nav aria-label="Main">` landmark rather than searching the whole page.
 * Playwright matches an accessible name by *substring* unless told otherwise, so a bare
 * `getByRole("link", { name: "Patients" })` also matches the "Total patients" KPI card and fails
 * with a strict-mode violation. Scoping is better than `exact: true` here: it says what is meant
 * — navigate using the navigation — and stays correct if a card is ever renamed.
 */
export async function navigateTo(page: Page, label: string): Promise<void> {
  await page
    .getByRole("navigation", { name: "Main" })
    .getByRole("link", { name: label, exact: true })
    .click();
}
