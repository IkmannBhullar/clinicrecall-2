/**
 * Temporary landing page.
 *
 * Phase 1 delivers the toolchain, not the product. This page exists so `make dev` shows
 * something honest and so the design tokens in globals.css are exercised end to end.
 *
 * Phase 8 replaces it: signed-out visitors are redirected to /sign-in, signed-in users to
 * /dashboard.
 */

import { CheckCircle2, Circle } from "lucide-react";

/** The build phases, so the placeholder page doubles as a visible progress report. */
const PHASES: ReadonlyArray<{ number: number; name: string; done: boolean }> = [
  { number: 1, name: "Foundation and toolchain", done: true },
  { number: 2, name: "Schema, migrations, and tenancy", done: false },
  { number: 3, name: "RecallService domain core", done: false },
  { number: 4, name: "Authentication and security", done: false },
  { number: 5, name: "ReminderService", done: false },
  { number: 6, name: "CSV import", done: false },
  { number: 7, name: "Seed data and demo reset", done: false },
  { number: 8, name: "Web foundation and app shell", done: false },
  { number: 9, name: "Dashboard and Patients", done: false },
  { number: 10, name: "Reminders, Activity, Settings, Import", done: false },
  { number: 11, name: "End-to-end verification", done: false },
  { number: 12, name: "Documentation", done: false },
];

export default function HomePage() {
  return (
    <main id="main-content" className="mx-auto max-w-2xl px-6 py-20">
      {/* SPEC constraint D6: it must be impossible to mistake this for a system holding real
          patient records. The indicator becomes part of the permanent app chrome in phase 8. */}
      <p className="mb-8 inline-flex items-center gap-2 rounded-pill bg-warning-bg px-3 py-1 text-xs font-medium text-warning">
        Demo Data — synthetic patients only
      </p>

      <h1 className="text-3xl font-semibold tracking-tight text-ink">ClinicRecall</h1>

      <p className="mt-3 text-base leading-relaxed text-ink-muted">
        Identify patients due for their annual visit and send professional reminders. The
        toolchain is in place; the product is being built phase by phase.
      </p>

      <h2 className="mt-12 text-sm font-semibold uppercase tracking-wide text-ink-subtle">
        Build progress
      </h2>

      <ol className="mt-4 divide-y divide-border rounded-card border border-border bg-surface shadow-card">
        {PHASES.map((phase) => (
          <li key={phase.number} className="flex items-center gap-3 px-4 py-3">
            {phase.done ? (
              <CheckCircle2
                className="size-4 shrink-0 text-success"
                aria-hidden="true"
              />
            ) : (
              <Circle className="size-4 shrink-0 text-ink-subtle" aria-hidden="true" />
            )}
            <span className="w-8 shrink-0 text-sm tabular-nums text-ink-subtle">
              {phase.number}
            </span>
            <span
              className={
                phase.done ? "text-sm text-ink" : "text-sm text-ink-muted"
              }
            >
              {phase.name}
            </span>
            <span className="sr-only">{phase.done ? "Complete" : "Not started"}</span>
          </li>
        ))}
      </ol>
    </main>
  );
}
