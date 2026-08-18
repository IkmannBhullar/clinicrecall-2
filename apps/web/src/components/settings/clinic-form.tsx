"use client";

/**
 * Clinic profile and reminder settings (SPEC §8).
 *
 * Two of these fields are not preferences. **Timezone** decides what "today" means for every
 * status in the product, and **annual interval** decides when every patient is next due — so
 * changing either re-derives the whole patient list. Both say so on screen, because a field that
 * silently rewrites 55 due dates should warn you first.
 *
 * Admin-only, enforced by the API. A staff member sees the values and cannot change them, which
 * is more useful than hiding the page entirely — the phone number in their reminders is worth
 * being able to check.
 */

import { Check } from "lucide-react";
import { useRouter } from "next/navigation";
import * as React from "react";

import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  FieldError,
  Input,
  Label,
  Spinner,
} from "@/components/ui/primitives";
import { toDisplayMessage, useApi } from "@/lib/use-api";
import type { ClinicSettings } from "@/lib/settings";

export function ClinicForm({
  initial,
  canEdit,
}: {
  initial: ClinicSettings;
  canEdit: boolean;
}) {
  const api = useApi();
  const router = useRouter();

  const [form, setForm] = React.useState(initial);
  const [saving, setSaving] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const [saved, setSaved] = React.useState(false);

  const recallChanged =
    form.timezone !== initial.timezone ||
    Number(form.annual_interval_months) !== Number(initial.annual_interval_months);

  function set<K extends keyof ClinicSettings>(key: K, value: ClinicSettings[K]) {
    setForm((current) => ({ ...current, [key]: value }));
    setSaved(false);
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setSaving(true);
    setError(null);

    try {
      await api.post<ClinicSettings>("/settings", {
        ...form,
        annual_interval_months: Number(form.annual_interval_months),
        estimated_annual_visit_value: String(form.estimated_annual_visit_value),
      });
      setSaved(true);
      // Re-render the server components: a changed interval alters every due date on every
      // other screen, and leaving them stale would be worse than not saving at all.
      router.refresh();
    } catch (caught) {
      setError(toDisplayMessage(caught));
    } finally {
      setSaving(false);
    }
  }

  return (
    <form onSubmit={submit} className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle>Clinic profile</CardTitle>
          <CardDescription>
            These details appear in every reminder your patients receive.
          </CardDescription>
        </CardHeader>
        <CardContent className="grid gap-4 sm:grid-cols-2">
          <Field label="Clinic name" id="clinic_name">
            <Input
              id="clinic_name"
              value={form.clinic_name}
              disabled={!canEdit}
              onChange={(e) => set("clinic_name", e.target.value)}
            />
          </Field>
          <Field label="Phone" id="phone">
            <Input
              id="phone"
              value={form.phone ?? ""}
              disabled={!canEdit}
              onChange={(e) => set("phone", e.target.value)}
            />
          </Field>
          <Field label="Office email" id="email">
            <Input
              id="email"
              type="email"
              value={form.email ?? ""}
              disabled={!canEdit}
              onChange={(e) => set("email", e.target.value)}
            />
          </Field>
          <Field label="Website" id="website">
            <Input
              id="website"
              value={form.website ?? ""}
              disabled={!canEdit}
              onChange={(e) => set("website", e.target.value)}
            />
          </Field>
          <Field
            label="Booking link"
            id="scheduling_url"
            hint="Where the “Schedule Appointment” button in the email sends patients."
            wide
          >
            <Input
              id="scheduling_url"
              value={form.scheduling_url ?? ""}
              disabled={!canEdit}
              onChange={(e) => set("scheduling_url", e.target.value)}
            />
          </Field>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Reminder settings</CardTitle>
          <CardDescription>
            How your recall cycle works, and what a recovered visit is worth.
          </CardDescription>
        </CardHeader>
        <CardContent className="grid gap-4 sm:grid-cols-2">
          <Field
            label="Recall interval (months)"
            id="annual_interval_months"
            hint="Changing this recalculates every patient's next due date."
          >
            <Input
              id="annual_interval_months"
              type="number"
              min={1}
              max={60}
              value={form.annual_interval_months}
              disabled={!canEdit}
              onChange={(e) => set("annual_interval_months", Number(e.target.value))}
            />
          </Field>

          <Field
            label="Timezone"
            id="timezone"
            hint="Decides what “today” means for every due date."
          >
            <Input
              id="timezone"
              value={form.timezone}
              disabled={!canEdit}
              onChange={(e) => set("timezone", e.target.value)}
            />
          </Field>

          <Field
            label="Value per annual visit"
            id="estimated_annual_visit_value"
            hint="Used for the estimated revenue figure on your dashboard."
          >
            <Input
              id="estimated_annual_visit_value"
              type="number"
              min={0}
              step="0.01"
              value={form.estimated_annual_visit_value}
              disabled={!canEdit}
              onChange={(e) => set("estimated_annual_visit_value", e.target.value)}
            />
          </Field>

          <Field label="Email signature" id="reminder_signature" wide>
            <textarea
              id="reminder_signature"
              rows={3}
              value={form.reminder_signature ?? ""}
              disabled={!canEdit}
              onChange={(e) => set("reminder_signature", e.target.value)}
              className="mt-1.5 w-full rounded-control border border-border-strong bg-surface px-3 py-2 text-sm text-ink shadow-card focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand disabled:opacity-60"
            />
          </Field>
        </CardContent>
      </Card>

      {/* Warn before the change, not after. This rewrites every due date in the practice. */}
      {recallChanged && canEdit ? (
        <p className="rounded-control bg-warning-bg px-3 py-2 text-sm text-warning">
          Saving will recalculate the next due date and recall status for every patient.
        </p>
      ) : null}

      <div aria-live="polite">
        <FieldError id="settings-error">{error}</FieldError>
      </div>

      {canEdit ? (
        <div className="flex items-center gap-3">
          <Button type="submit" disabled={saving}>
            {saving ? <Spinner /> : null}
            {saving ? "Saving…" : "Save changes"}
          </Button>
          {saved ? (
            <p role="status" className="flex items-center gap-1.5 text-sm text-success">
              <Check className="size-4" aria-hidden="true" />
              Saved
            </p>
          ) : null}
        </div>
      ) : (
        <p className="text-sm text-ink-muted">
          Only an administrator can change these settings.
        </p>
      )}
    </form>
  );
}

function Field({
  label,
  id,
  hint,
  wide,
  children,
}: {
  label: string;
  id: string;
  hint?: string;
  wide?: boolean;
  children: React.ReactNode;
}) {
  return (
    <div className={wide ? "sm:col-span-2" : undefined}>
      <Label htmlFor={id}>{label}</Label>
      {children}
      {hint ? <p className="mt-1 text-xs text-ink-subtle">{hint}</p> : null}
    </div>
  );
}
