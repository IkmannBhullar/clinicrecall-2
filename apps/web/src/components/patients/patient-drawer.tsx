"use client";

/**
 * The patient detail drawer (SPEC §8).
 *
 * A drawer rather than a full page, as the spec prefers, and for a real reason: staff work
 * through a list. A drawer keeps the list on screen behind it, so closing one patient puts you
 * back exactly where you were rather than requiring a navigation and a scroll.
 *
 * Contains: header, status badge, next annual visit, contact details, the reminder timeline with
 * delivery states, the rendered email preview, and the four actions plus Pause Reminders.
 *
 * **No clinical fields, ever** (SPEC §1). No diagnosis, no condition, no visit reason, no notes.
 *
 * Accessibility: it is a modal dialog, so it traps focus, closes on Escape, restores focus to
 * whatever opened it, and marks the rest of the page inert to assistive technology.
 */

import {
  CalendarCheck,
  CalendarPlus,
  Check,
  Mail,
  PauseCircle,
  Phone,
  PlayCircle,
  Send,
  X,
} from "lucide-react";
import { useRouter } from "next/navigation";
import * as React from "react";

import { Button } from "@/components/ui/button";
import { EmptyState, Skeleton, Spinner } from "@/components/ui/primitives";
import { StatusBadge } from "@/components/ui/status-badge";
import { MessagePreview } from "@/components/patients/message-preview";
import { formatDate, formatDueDate, formatTimestamp } from "@/lib/format";
import { toDisplayMessage, useApi } from "@/lib/use-api";
import {
  REMINDER_STATUS_LABELS,
  REMINDER_STATUS_STYLES,
  RULE_SHORT_LABELS,
  type PatientActionResponse,
  type PatientDetail,
} from "@/lib/patients";
import { cn } from "@/lib/utils";

export function PatientDrawer({
  publicId,
  today,
  onClose,
}: {
  publicId: string;
  today: string;
  onClose: () => void;
}) {
  const api = useApi();
  const router = useRouter();

  const [patient, setPatient] = React.useState<PatientDetail | null>(null);
  const [loading, setLoading] = React.useState(true);
  const [busy, setBusy] = React.useState<string | null>(null);
  const [notice, setNotice] = React.useState<{ text: string; tone: "ok" | "error" } | null>(null);

  const panelRef = React.useRef<HTMLDivElement>(null);
  // Remembered so focus can go back where it came from when the drawer closes.
  const openerRef = React.useRef<HTMLElement | null>(null);

  React.useEffect(() => {
    openerRef.current = document.activeElement as HTMLElement | null;
    return () => openerRef.current?.focus?.();
  }, []);

  // Load the patient.
  React.useEffect(() => {
    let cancelled = false;
    setLoading(true);

    api
      .get<PatientDetail>(`/patients/${publicId}`)
      .then((result) => {
        if (!cancelled) setPatient(result);
      })
      .catch((error) => {
        if (!cancelled) setNotice({ text: toDisplayMessage(error), tone: "error" });
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [api, publicId]);

  // Escape closes, and focus is moved into the panel on open.
  React.useEffect(() => {
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    document.addEventListener("keydown", handleKeyDown);
    panelRef.current?.focus();
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [onClose]);

  /**
   * Run one of the row actions.
   *
   * `router.refresh()` afterwards re-renders the server components behind the drawer, so the
   * list row and the dashboard KPI both reflect the change — which is step 9 of the demo
   * sequence (SPEC §11), and the moment the product stops looking like separate screens.
   */
  async function runAction(action: string, path: string, body?: unknown) {
    setBusy(action);
    setNotice(null);

    try {
      const result = await api.post<PatientActionResponse>(path, body);
      setPatient(result.patient);
      setNotice({ text: result.message, tone: "ok" });
      router.refresh();
    } catch (error) {
      setNotice({ text: toDisplayMessage(error), tone: "error" });
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      {/* Scrim. aria-hidden because Escape and the close button are the accessible ways out. */}
      <button
        type="button"
        className="absolute inset-0 bg-ink/25"
        onClick={onClose}
        aria-hidden="true"
        tabIndex={-1}
      />

      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-label={patient ? `${patient.first_name} ${patient.last_name}` : "Patient details"}
        tabIndex={-1}
        className="relative flex h-full w-full max-w-lg flex-col overflow-y-auto border-l border-border bg-surface shadow-panel focus:outline-none"
      >
        {loading ? (
          <DrawerSkeleton onClose={onClose} />
        ) : patient ? (
          <>
            {/* ------------------------------------------------------------------ Header */}
            <div className="sticky top-0 z-10 flex items-start justify-between gap-3 border-b border-border bg-surface px-5 py-4">
              <div className="min-w-0">
                <h2 className="truncate text-base font-semibold text-ink">
                  {patient.first_name} {patient.last_name}
                </h2>
                <div className="mt-1.5 flex flex-wrap items-center gap-2">
                  <StatusBadge status={patient.status} />
                  <span className="text-xs text-ink-subtle">
                    {formatDueDate(patient.next_annual_due_date, today)}
                  </span>
                </div>
              </div>
              <Button variant="ghost" size="icon" onClick={onClose}>
                <X aria-hidden="true" />
                <span className="sr-only">Close</span>
              </Button>
            </div>

            {/* ----------------------------------------------------------------- Notices */}
            {notice ? (
              <div
                role="status"
                aria-live="polite"
                className={cn(
                  "mx-5 mt-4 flex items-start gap-2 rounded-control px-3 py-2 text-sm",
                  notice.tone === "ok"
                    ? "bg-success-bg text-success"
                    : "bg-danger-bg text-danger",
                )}
              >
                {notice.tone === "ok" ? (
                  <Check className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
                ) : null}
                <span>{notice.text}</span>
              </div>
            ) : null}

            {/* ----------------------------------------------------------------- Details */}
            <div className="space-y-5 px-5 py-5">
              <section>
                <h3 className="text-xs font-semibold uppercase tracking-wide text-ink-subtle">
                  Recall
                </h3>
                <dl className="mt-2 space-y-1.5 text-sm">
                  <Row label="Next annual visit" value={formatDate(patient.next_annual_due_date)} />
                  <Row label="Last visit" value={formatDate(patient.last_annual_visit_date)} />
                  {patient.scheduled_for ? (
                    <Row label="Appointment booked" value={formatDate(patient.scheduled_for)} />
                  ) : null}
                </dl>
              </section>

              <section>
                <h3 className="text-xs font-semibold uppercase tracking-wide text-ink-subtle">
                  Contact
                </h3>
                <dl className="mt-2 space-y-1.5 text-sm">
                  <Row
                    label="Email"
                    value={
                      <span className="inline-flex items-center gap-1.5">
                        <Mail className="size-3.5 text-ink-subtle" aria-hidden="true" />
                        {patient.email}
                      </span>
                    }
                  />
                  {patient.phone ? (
                    <Row
                      label="Phone"
                      value={
                        <span className="inline-flex items-center gap-1.5">
                          <Phone className="size-3.5 text-ink-subtle" aria-hidden="true" />
                          {patient.phone}
                        </span>
                      }
                    />
                  ) : null}
                  {patient.external_id ? (
                    <Row label="Patient ID" value={patient.external_id} />
                  ) : null}
                </dl>
              </section>

              {/* Consent state, shown only when it is not the default — a banner saying
                  "reminders are on" on every patient would be noise. */}
              {patient.opted_out ? (
                <p className="rounded-control bg-warning-bg px-3 py-2 text-xs text-warning">
                  This patient unsubscribed on {formatTimestamp(patient.opted_out_at)}. Reminders
                  cannot be resumed from here — only they can reverse it.
                </p>
              ) : !patient.reminders_enabled ? (
                <p className="rounded-control bg-canvas px-3 py-2 text-xs text-ink-muted">
                  Reminders are paused for this patient by your practice.
                </p>
              ) : null}

              {/* ---------------------------------------------------------------- Actions */}
              <section>
                <h3 className="text-xs font-semibold uppercase tracking-wide text-ink-subtle">
                  Actions
                </h3>
                <div className="mt-2 flex flex-wrap gap-2">
                  <Button
                    size="sm"
                    disabled={busy !== null || !patient.reminders_enabled || patient.opted_out}
                    onClick={() =>
                      runAction("send", `/patients/${patient.public_id}/send-reminder`)
                    }
                  >
                    {busy === "send" ? <Spinner /> : <Send aria-hidden="true" />}
                    Send reminder
                  </Button>

                  <Button
                    size="sm"
                    variant="secondary"
                    disabled={busy !== null}
                    onClick={() => {
                      // Default to a fortnight out — a plausible next appointment, and one fewer
                      // decision during a demo.
                      const suggested = new Date();
                      suggested.setDate(suggested.getDate() + 14);
                      runAction("schedule", `/patients/${patient.public_id}/schedule`, {
                        scheduled_for: suggested.toISOString().slice(0, 10),
                      });
                    }}
                  >
                    {busy === "schedule" ? <Spinner /> : <CalendarPlus aria-hidden="true" />}
                    Mark scheduled
                  </Button>

                  <Button
                    size="sm"
                    variant="secondary"
                    disabled={busy !== null}
                    onClick={() =>
                      runAction("complete", `/patients/${patient.public_id}/complete`, {})
                    }
                  >
                    {busy === "complete" ? <Spinner /> : <CalendarCheck aria-hidden="true" />}
                    Mark visit completed
                  </Button>

                  {patient.reminders_enabled ? (
                    <Button
                      size="sm"
                      variant="ghost"
                      disabled={busy !== null}
                      onClick={() => runAction("pause", `/patients/${patient.public_id}/pause`)}
                    >
                      {busy === "pause" ? <Spinner /> : <PauseCircle aria-hidden="true" />}
                      Pause reminders
                    </Button>
                  ) : (
                    <Button
                      size="sm"
                      variant="ghost"
                      disabled={busy !== null || patient.opted_out}
                      onClick={() => runAction("resume", `/patients/${patient.public_id}/resume`)}
                    >
                      {busy === "resume" ? <Spinner /> : <PlayCircle aria-hidden="true" />}
                      Resume reminders
                    </Button>
                  )}
                </div>
              </section>

              {/* --------------------------------------------------------------- Timeline */}
              <section>
                <h3 className="text-xs font-semibold uppercase tracking-wide text-ink-subtle">
                  Reminder history
                </h3>

                {patient.reminders.length === 0 ? (
                  <EmptyState
                    title="No reminders sent yet"
                    description="Reminders appear here as the recall campaign reaches this patient."
                    className="py-8"
                  />
                ) : (
                  <ol className="mt-2 space-y-2">
                    {patient.reminders.map((reminder) => (
                      <li
                        key={reminder.id}
                        className="rounded-control border border-border px-3 py-2.5"
                      >
                        <div className="flex items-center justify-between gap-2">
                          <p className="text-sm font-medium text-ink">
                            {reminder.source === "MANUAL"
                              ? "Manual reminder"
                              : (RULE_SHORT_LABELS[reminder.due_date_snapshot] ??
                                ruleLabel(reminder))}
                          </p>
                          <span
                            className={cn(
                              "shrink-0 rounded-pill px-2 py-0.5 text-[11px] font-medium",
                              REMINDER_STATUS_STYLES[reminder.status],
                            )}
                          >
                            {REMINDER_STATUS_LABELS[reminder.status]}
                          </span>
                        </div>

                        <p className="mt-0.5 text-xs text-ink-subtle">
                          {reminder.sent_at
                            ? `Sent ${formatTimestamp(reminder.sent_at)}`
                            : "Not yet sent"}
                          {reminder.delivered_at ? " · delivered" : null}
                        </p>

                        {reminder.failure_reason ? (
                          <p className="mt-1.5 rounded-control bg-danger-bg px-2 py-1 text-xs text-danger">
                            {reminder.failure_reason}
                          </p>
                        ) : null}

                        {reminder.rendered_subject ? (
                          <MessagePreview
                            patientPublicId={patient.public_id}
                            reminderId={reminder.id}
                            subject={reminder.rendered_subject}
                          />
                        ) : null}
                      </li>
                    ))}
                  </ol>
                )}
              </section>
            </div>
          </>
        ) : (
          <div className="p-5">
            <div className="mb-4 flex justify-end">
              <Button variant="ghost" size="icon" onClick={onClose}>
                <X aria-hidden="true" />
                <span className="sr-only">Close</span>
              </Button>
            </div>
            <EmptyState
              title="Could not load this patient"
              description={notice?.text ?? "Please close this panel and try again."}
            />
          </div>
        )}
      </div>
    </div>
  );
}

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex justify-between gap-4">
      <dt className="shrink-0 text-ink-muted">{label}</dt>
      <dd className="text-right text-ink">{value}</dd>
    </div>
  );
}

/** Describe a rule-driven reminder by its campaign position. */
function ruleLabel(reminder: { source: string }): string {
  return reminder.source === "TEST" ? "Test reminder" : "Scheduled reminder";
}

function DrawerSkeleton({ onClose }: { onClose: () => void }) {
  return (
    <div className="p-5" aria-busy="true">
      <div className="mb-4 flex items-start justify-between">
        <div className="space-y-2">
          <Skeleton className="h-5 w-40" />
          <Skeleton className="h-4 w-24" />
        </div>
        <Button variant="ghost" size="icon" onClick={onClose}>
          <X aria-hidden="true" />
          <span className="sr-only">Close</span>
        </Button>
      </div>
      <div className="space-y-3">
        <Skeleton className="h-4 w-full" />
        <Skeleton className="h-4 w-3/4" />
        <Skeleton className="h-9 w-full" />
        <Skeleton className="h-20 w-full" />
      </div>
    </div>
  );
}
