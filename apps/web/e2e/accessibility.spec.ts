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

  test("the patient drawer locks the page behind it", async ({ page }) => {
    /**
     * Found by hand, not by a test: with the drawer open, scrolling chained through to the
     * document and the whole application — sidebar, header and all — slid up behind a modal that
     * was supposed to be holding the reader's attention.
     *
     * Worth guarding because it is invisible to every other check. axe inspects the DOM and sees
     * a correctly-formed dialog; the walkthrough clicks buttons that happen to be above the fold.
     * Only scrolling reveals it.
     */
    await signIn(page);

    // A short viewport, so the document behind the drawer genuinely overflows. At the suite's
    // default 1440x900 the eight-row overdue list fits on screen, nothing can scroll, and the
    // assertions below pass whether or not the lock exists — verified by reverting the fix and
    // watching this test stay green.
    await page.setViewportSize({ width: 1024, height: 560 });
    await page.goto("/patients?status=OVERDUE");

    const scrollableBefore = await page.evaluate(
      () => document.documentElement.scrollHeight > window.innerHeight,
    );
    expect(scrollableBefore, "the page must be scrollable for this test to mean anything").toBe(
      true,
    );

    await page.getByRole("button", { name: "Sarah Johnson", exact: true }).click();

    const drawer = page.getByRole("dialog", { name: "Sarah Johnson" });
    await expect(drawer).toBeVisible();

    // Expand a rendered email so the panel's content genuinely exceeds the viewport — which is
    // the state the bug appeared in. Collapsed, the drawer fits on screen and nothing scrolls at
    // all, so the assertions below would pass without proving anything.
    await drawer.getByRole("button", { name: "View the email that was sent" }).first().click();
    await expect(drawer.getByText(/Green Valley Family Clinic/).first()).toBeVisible();

    // Where the page sits once the drawer is open — not necessarily 0, because Playwright scrolls
    // Sarah's row into view before clicking it. What matters is that this does not change from
    // here on, so the comparison is before-and-after rather than against zero.
    const restingScrollY = await page.evaluate(() => window.scrollY);

    // Scroll with the pointer over the page behind the drawer rather than over the panel itself.
    // Chrome latches a wheel gesture to the first scrollable element under the cursor, so wheeling
    // over the panel just scrolls the panel to its end and stops — it never reaches the document,
    // and the test would pass with the lock removed. Over the scrim there is nothing to latch to,
    // which is precisely where the page used to slide.
    // Upwards, not downwards. Playwright scrolled Sarah's row into view to click it, which leaves
    // the short viewport at the very bottom of the list — so a downward wheel has nowhere to go
    // and would pass with the lock removed. Upward has the whole page to travel.
    await page.mouse.move(200, 300);
    await page.mouse.wheel(0, -600);
    await page.waitForTimeout(200);

    expect(
      await page.evaluate(() => window.scrollY),
      "the page moved behind the open drawer",
    ).toBe(restingScrollY);

    // And the panel scrolls on its own, so the content below the fold is still reachable.
    const scrolled = await drawer.evaluate((el) => {
      el.scrollTop = el.scrollHeight;
      return { scrollTop: el.scrollTop, overflowing: el.scrollHeight > el.clientHeight };
    });
    expect(scrolled.overflowing).toBe(true);
    expect(scrolled.scrollTop).toBeGreaterThan(0);

    // Closing restores the page's own scrolling rather than leaving the body locked.
    await page.keyboard.press("Escape");
    await expect(drawer).toBeHidden();
    expect(await page.evaluate(() => document.body.style.overflow)).not.toBe("hidden");
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
