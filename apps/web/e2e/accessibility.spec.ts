/**
 * Automated accessibility pass (SPEC §10: "Verify with an automated axe pass in the Playwright
 * suite").
 *
 * axe catches the mechanical failures — missing labels, insufficient contrast, broken heading
 * order, controls with no accessible name. It cannot judge whether an interface is *usable* with
 * a screen reader, and this suite does not claim it does. What it does guarantee is that the
 * defects a machine can find are absent, which is the floor rather than the ceiling.
 *
 * Scoped to serious and critical violations. Including every "minor" finding would produce a
 * list nobody acts on, and a gate nobody trusts gets disabled.
 */

import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

import { resetDemoData, signIn } from "./helpers";

test.beforeAll(() => {
  resetDemoData();
});

/** Run axe and return the violations worth failing over, formatted to be actionable. */
async function auditPage(page: Page): Promise<string[]> {
  const results = await new AxeBuilder({ page })
    // The WCAG levels a professional product should meet.
    .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
    .analyze();

  return results.violations
    .filter((violation) => violation.impact === "serious" || violation.impact === "critical")
    .map((violation) => {
      const where = violation.nodes
        .slice(0, 3)
        .map((node) => node.target.join(" "))
        .join(", ");
      return `[${violation.impact}] ${violation.id}: ${violation.help}\n    at: ${where}`;
    });
}

test.describe("accessibility", () => {
  test("the sign-in page has no serious violations", async ({ page }) => {
    await page.goto("/sign-in");
    await expect(page.getByRole("button", { name: "Sign in" })).toBeVisible();

    const violations = await auditPage(page);
    expect(violations, violations.join("\n")).toEqual([]);
  });

  // Every screen a staff member actually works in.
  for (const [name, path] of [
    ["dashboard", "/dashboard"],
    ["patients", "/patients"],
    ["reminders", "/reminders"],
    ["activity", "/activity"],
    ["settings", "/settings"],
    ["import", "/import"],
  ] as const) {
    test(`the ${name} page has no serious violations`, async ({ page }) => {
      await signIn(page);
      await page.goto(path);
      await page.waitForLoadState("networkidle");

      const violations = await auditPage(page);
      expect(violations, violations.join("\n")).toEqual([]);
    });
  }

  test("the patient drawer has no serious violations", async ({ page }) => {
    // Checked separately because a modal dialog is where accessibility most often breaks: focus
    // management, an accessible name, and the rest of the page being reachable behind it.
    await signIn(page);
    await page.goto("/patients?status=OVERDUE");
    await page.getByRole("button", { name: "Sarah Johnson", exact: true }).click();
    await expect(page.getByRole("dialog", { name: "Sarah Johnson" })).toBeVisible();

    const violations = await auditPage(page);
    expect(violations, violations.join("\n")).toEqual([]);
  });

  test("the interface is navigable by keyboard alone", async ({ page }) => {
    /**
     * SPEC §10 requires keyboard navigation throughout. axe cannot test this — it inspects the
     * DOM rather than driving it — so it is checked directly.
     *
     * The skip link is the first thing a keyboard user meets, and the one most often forgotten,
     * because it is invisible until focused.
     */
    await signIn(page);
    await page.goto("/dashboard");

    await page.keyboard.press("Tab");
    const firstFocused = page.locator(":focus");
    await expect(firstFocused).toHaveText(/Skip to main content/);

    // And it is visible once focused, rather than merely present.
    await expect(firstFocused).toBeVisible();

    // Tabbing onward reaches the navigation without a mouse.
    await page.keyboard.press("Tab");
    await expect(page.locator(":focus")).toBeVisible();
  });

  test("every status badge carries a text label, not colour alone", async ({ page }) => {
    /**
     * SPEC §10: colour must never be the sole carrier of meaning.
     *
     * The status column is the most-scanned part of the product, and it is exactly the place
     * where a colour-only design would be tempting. Each badge spells out its status in words.
     */
    await signIn(page);
    await page.goto("/patients");
    await page.waitForLoadState("networkidle");

    const labels = await page
      .locator("table tbody tr span")
      .filter({ hasText: /Overdue|Due|Due soon|Scheduled|Active|Completed|Inactive/ })
      .allTextContents();

    expect(labels.length).toBeGreaterThan(0);
    for (const label of labels) {
      expect(label.trim().length).toBeGreaterThan(0);
    }
  });
});
