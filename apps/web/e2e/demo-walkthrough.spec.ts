/**
 * The thirteen-step demo walkthrough (SPEC §11).
 *
 * This is the executable form of the demo script. Every step the talk track performs is performed
 * here, in order, against the real stack — a real Supabase sign-in, the real API, the real
 * database, the real mock email provider.
 *
 * Three things it proves that unit tests cannot:
 *
 * 1. **The pages actually render.** A screenshot per step lands in `docs/screenshots/`, which
 *    fills the README's placeholders and is evidence rather than assertion.
 * 2. **The steps compose.** Step 6 sends a reminder that step 7 checks arrived; step 8 books an
 *    appointment that step 9 sees reflected on the dashboard. That causality is the product.
 * 3. **Nothing errors in the console** — a broken fetch or a React warning is invisible in a demo
 *    right up until it is not.
 *
 * It runs as one ordered test rather than thirteen independent ones, because it is one story. A
 * suite where step 9 could run before step 8 would be testing a sequence that never happens.
 */

import { expect, test } from "@playwright/test";
import path from "node:path";

import {
  REPO_ROOT,
  captureStep,
  navigateTo,
  resetDemoData,
  signIn,
  watchConsole,
} from "./helpers";

// Pristine data before the walk. The steps below assert exact numbers, and the walk itself
// changes them — so without this the suite would pass once and fail on every rerun.
test.beforeAll(() => {
  resetDemoData();
});

test.describe.configure({ mode: "serial" });

test("the thirteen-step demo walkthrough", async ({ page }) => {
  const assertNoConsoleErrors = watchConsole(page);

  // ------------------------------------------------------------------------------- 1. Sign in
  await test.step("1. Sign in", async () => {
    await page.goto("/sign-in");
    await expect(page.getByRole("button", { name: "Sign in" })).toBeVisible();
    // The synthetic-data indicator is present before anyone is even signed in (SPEC D6).
    await expect(page.getByText("Demo Data").first()).toBeVisible();
    await captureStep(page, 1, "sign-in");

    await signIn(page);
  });

  // ------------------------------------------------------- 2. Dashboard shows populated metrics
  await test.step("2. Dashboard shows populated metrics", async () => {
    // Matched without the apostrophe: the page renders a typographic ’ (from &rsquo;), which
    // does not equal the straight ' in a source literal.
    await expect(page.getByText(/patient recall overview/)).toBeVisible();

    // Scoped to the KPI landmark. "Overdue" also labels eight status badges and a legend
    // entry, so an unscoped match would be ambiguous — and a test that resolves ambiguity with
    // `.first()` is asserting against whichever element happens to come first in the DOM.
    const metrics = page.getByRole("region", { name: "Key metrics" });

    // 55 seeded patients (SPEC §7.3). Asserted as a number rather than "is visible", because a
    // dashboard of zeroes would satisfy the weaker check while being exactly the failure that
    // matters.
    await expect(metrics.getByText("55", { exact: true })).toBeVisible();

    // Every KPI card SPEC §8 names.
    for (const label of ["Total patients", "Due this month", "Overdue", "Reminders sent"]) {
      await expect(metrics.getByText(label, { exact: true })).toBeVisible();
    }

    // And the chrome that must never be absent (SPEC D6).
    await expect(page.getByText("Demo Data").first()).toBeVisible();

    await captureStep(page, 2, "dashboard");
  });

  // --------------------------------------------------------------------- 3. Filter to overdue
  await test.step("3. Filter to overdue", async () => {
    await navigateTo(page, "Patients");
    await expect(page).toHaveURL(/\/patients/);

    // Scoped to the filter group: "Overdue" is also the text of a badge on every overdue row.
    // The chip's accessible name is its visible text — the `title` is a description, and the
    // accname algorithm prefers content over it.
    await page
      .getByRole("group", { name: "Filter by status" })
      .getByRole("button", { name: "Overdue", exact: true })
      .click();
    await expect(page).toHaveURL(/status=OVERDUE/);

    // Eight overdue patients in the seed (SPEC §7.3's distribution).
    await expect(page.getByText("8 patients")).toBeVisible();
    await expect(page.getByRole("button", { name: "Sarah Johnson", exact: true })).toBeVisible();

    await captureStep(page, 3, "patients-overdue");
  });

  // ---------------------------------------------------------------------- 4. Open Sarah Johnson
  await test.step("4. Open Sarah Johnson", async () => {
    await page.getByRole("button", { name: "Sarah Johnson", exact: true }).click();

    const drawer = page.getByRole("dialog", { name: "Sarah Johnson" });
    await expect(drawer).toBeVisible();
    // ~24 days overdue (SPEC §7.3).
    await expect(drawer.getByText(/24 days overdue/)).toBeVisible();
    await expect(drawer.getByText("Overdue", { exact: true })).toBeVisible();

    await captureStep(page, 4, "patient-drawer");
  });

  // ------------------------------------------------- 5. Timeline shows 2 delivered reminders
  await test.step("5. Timeline shows two delivered reminders", async () => {
    const drawer = page.getByRole("dialog", { name: "Sarah Johnson" });

    // Exactly two. SPEC §7.3 leaves T_ZERO deliberately unsent so step 6 has something to do,
    // and this is the assertion that catches it if the catch-up window ever backfills it.
    //
    // `exact` matters here: Playwright's default text matching is case-insensitive *substring*,
    // so a bare "Delivered" also matches each entry's "Sent 17 Jul · delivered" caption and
    // counts four where there are two.
    await expect(drawer.getByText("Delivered", { exact: true })).toHaveCount(2);
    await expect(drawer.getByText("Scheduled reminder", { exact: true })).toHaveCount(2);

    // The rendered email is available — "here is exactly what your patient received". Open it
    // rather than merely asserting the button exists: the stored message body is the evidence
    // that these reminders were really sent, and a screenshot of the closed drawer is just
    // step 4's frame again.
    await drawer.getByRole("button", { name: "View the email that was sent" }).first().click();
    await expect(drawer.getByText(/Green Valley Family Clinic/).first()).toBeVisible();

    await captureStep(page, 5, "reminder-timeline");
  });

  // ---------------------------------------------------------------------- 6. Send a reminder
  await test.step("6. Send a reminder", async () => {
    const drawer = page.getByRole("dialog", { name: "Sarah Johnson" });
    await drawer.getByRole("button", { name: "Send reminder" }).click();

    await expect(drawer.getByRole("status")).toContainText("Reminder sent to Sarah");
    await captureStep(page, 6, "send-reminder");
  });

  // ----------------------------------------------------------- 7. Mock delivery success visible
  await test.step("7. Mock delivery success is visible", async () => {
    const drawer = page.getByRole("dialog", { name: "Sarah Johnson" });

    // The timeline has grown by one, and the new entry is a manual send that the provider
    // accepted. This is the mock provider behaving like a real one (SPEC §6.4).
    await expect(drawer.getByText("Manual reminder", { exact: true })).toBeVisible();
    await expect(drawer.getByText("Sent", { exact: true })).toBeVisible();

    await captureStep(page, 7, "delivery-success");
  });

  // -------------------------------------------------------------- 8. Mark appointment scheduled
  await test.step("8. Mark the appointment as scheduled", async () => {
    const drawer = page.getByRole("dialog", { name: "Sarah Johnson" });
    await drawer.getByRole("button", { name: "Mark scheduled" }).click();

    await expect(drawer.getByRole("status")).toContainText("Appointment recorded");
    // The status badge moves from Overdue to Scheduled — the recall state machine, on screen.
    await expect(drawer.getByText("Scheduled", { exact: true }).first()).toBeVisible();

    await captureStep(page, 8, "mark-scheduled");
  });

  // --------------------------------------------------------- 9. Dashboard KPI reflects the change
  await test.step("9. The dashboard reflects the change", async () => {
    // Close the drawer first. It is a modal covering the sidebar, so the navigation is not
    // clickable underneath it — which is correct behaviour for a modal, and exactly what a
    // person does at this point in the demo.
    await page.keyboard.press("Escape");
    await expect(page.getByRole("dialog", { name: "Sarah Johnson" })).toBeHidden();

    await navigateTo(page, "Dashboard");
    await expect(page).toHaveURL(/\/dashboard/);

    // Sarah has moved out of Overdue and into Scheduled, so the counts shift: 8 overdue becomes
    // 7, and 4 scheduled becomes 5. This is the step that proves the screens are one product
    // rather than several — the drawer wrote, and the dashboard reads.
    const metrics = page.getByRole("region", { name: "Key metrics" });
    await expect(metrics.getByText("7", { exact: true })).toBeVisible();

    await expect(page.getByRole("link", { name: /Scheduled\s*5/ })).toBeVisible();

    await captureStep(page, 9, "dashboard-updated");
  });

  // ------------------------------------------------------------ 10. Import patients-messy.csv
  await test.step("10. Import patients-messy.csv", async () => {
    await page.goto("/import");
    await expect(page.getByText("Drag your patient list here")).toBeVisible();

    // Captured *before* the file is attached. captureStep waits for networkidle, which also waits
    // out the preview request — so capturing after setInputFiles produced a frame identical to
    // step 11's and this screenshot showed the preview while claiming to show the upload.
    await captureStep(page, 10, "import-upload");

    await page
      .locator('input[type="file"]')
      .setInputFiles(path.join(REPO_ROOT, "docs", "samples", "patients-messy.csv"));

    await expect(page.getByText("patients-messy.csv")).toBeVisible();
  });

  // ------------------------------------------------------------ 11. Preview shows 327/320/5/2
  await test.step("11. The preview shows 327 / 320 / 5 / 2", async () => {
    // The four numbers the demo reads aloud (SPEC §7.2). Each is asserted next to its own label,
    // so a coincidental "327" elsewhere on the page cannot make this pass.
    const stat = (label: string, value: string) =>
      expect(
        page.locator("div", { has: page.getByText(label, { exact: true }) }).getByText(value, {
          exact: true,
        }).first(),
      ).toBeVisible();

    await stat("Records found", "327");
    await stat("Ready to import", "320");
    await stat("Missing information", "5");
    await stat("Invalid emails", "2");

    // And the per-row errors, which are what make it an import tool rather than a file input.
    await expect(page.getByText("Rows that will be skipped")).toBeVisible();
    await expect(page.getByRole("button", { name: "Download error report" })).toBeVisible();

    await captureStep(page, 11, "import-preview");
  });

  // ------------------------------------------- 12. Reminders page shows rules and performance
  await test.step("12. Reminders shows the rules and performance", async () => {
    await navigateTo(page, "Reminders");
    await expect(page).toHaveURL(/\/reminders/);

    // Four rules, four toggles, all on (SPEC §8: toggles only, no automation builder).
    // Matched as a heading: the phrase also appears in the card's description below it.
    await expect(page.getByRole("heading", { name: "Annual recall campaign" })).toBeVisible();
    await expect(page.locator('input[role="switch"]')).toHaveCount(4);

    // The delivery strip.
    await expect(page.getByRole("heading", { name: "Delivery performance" })).toBeVisible();
    for (const label of ["Queued", "Sent", "Delivered", "Failed"]) {
      await expect(page.getByText(label, { exact: true })).toBeVisible();
    }

    // The side-by-side preview of what patients receive.
    await expect(page.getByRole("heading", { name: "What patients receive" })).toBeVisible();

    // And the failure-recovery path, with the seeded hard bounce in it (SPEC §7.3's Robert Hale).
    await expect(page.getByRole("heading", { name: "Reminders that failed" })).toBeVisible();
    await expect(page.getByText("Robert Hale", { exact: true })).toBeVisible();

    await captureStep(page, 12, "reminders");
  });

  // ------------------------------------------ 13. Revenue recovered renders with its definition
  await test.step("13. Revenue recovered renders with its definition", async () => {
    await navigateTo(page, "Dashboard");
    await expect(page).toHaveURL(/\/dashboard/);

    await expect(page.getByText("Estimated revenue recovered")).toBeVisible();
    await expect(page.getByText(/appointments? recovered/)).toBeVisible();

    // SPEC §8 requires the formula to be available, because "an office manager will poke it".
    const disclosure = page.getByRole("group").filter({ hasText: "How is this calculated?" });
    await expect(disclosure).toBeVisible();
    await disclosure.getByText("How is this calculated?").click();

    // The definition, and the arithmetic behind it.
    await expect(page.getByText(/delivered reminder/)).toBeVisible();
    await expect(page.getByText(/within 30 days/)).toBeVisible();
    await expect(page.getByText(/per visit/)).toBeVisible();

    await captureStep(page, 13, "revenue-recovered");
  });

  assertNoConsoleErrors();
});
