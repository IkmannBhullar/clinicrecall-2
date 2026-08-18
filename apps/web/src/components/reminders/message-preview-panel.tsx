/**
 * The side-by-side message preview (SPEC §8).
 *
 * Shows what a patient receives, rendered from the practice's own settings — their name, phone
 * number, scheduling link and signature — so the preview is *theirs* rather than a generic
 * sample. That is what makes the toggles beside it concrete.
 *
 * Reproduced here as markup rather than fetched as HTML, because a rendered message only exists
 * once one has been sent. This is what the template will produce, laid out the same way; the
 * patient drawer shows the exact stored message for reminders that actually went out.
 *
 * Content rule, same as the template itself: nothing clinical, ever (SPEC §6.5).
 */

import { Card, CardHeader, CardTitle, CardDescription, CardContent } from "@/components/ui/primitives";
import type { ClinicSettings } from "@/lib/settings";

export function MessagePreviewPanel({ clinic }: { clinic: ClinicSettings }) {
  return (
    <Card className="lg:sticky lg:top-20">
      <CardHeader>
        <CardTitle>What patients receive</CardTitle>
        <CardDescription>
          Built from your clinic profile. Change your details in Settings and this changes with
          them.
        </CardDescription>
      </CardHeader>

      <CardContent>
        <div className="rounded-control border border-border bg-canvas p-4">
          <p className="mb-3 border-b border-border pb-2 text-xs text-ink-subtle">
            <span className="font-medium text-ink-muted">Subject:</span> A reminder from{" "}
            {clinic.clinic_name} about your annual visit
          </p>

          <div className="space-y-3 text-sm leading-relaxed text-ink-muted">
            <p className="font-semibold text-ink">{clinic.clinic_name}</p>
            <p className="text-ink">Hi Sarah,</p>
            <p>
              This is a friendly reminder from {clinic.clinic_name} that it may be time to
              schedule your annual visit.
            </p>
            <p>
              {clinic.phone
                ? `Please contact our office at ${clinic.phone}${clinic.scheduling_url ? " or use the button below" : ""}.`
                : clinic.scheduling_url
                  ? "Please use the button below to book a time that suits you."
                  : "Please contact our office to book a time that suits you."}
            </p>

            {clinic.scheduling_url ? (
              <p>
                <span className="inline-block rounded-control bg-brand px-4 py-2 text-sm font-semibold text-white">
                  Schedule Appointment
                </span>
              </p>
            ) : null}

            <hr className="border-0 border-t border-border" />

            {clinic.reminder_signature ? (
              <p className="whitespace-pre-line">{clinic.reminder_signature}</p>
            ) : null}
            <p className="font-medium text-ink">{clinic.clinic_name}</p>
            {clinic.phone ? <p className="text-xs">{clinic.phone}</p> : null}
            {clinic.website ? <p className="text-xs">{clinic.website}</p> : null}

            {/* The opt-out is part of every reminder (SPEC §6.5), so it is part of the preview.
                Clinics ask about it, and showing it here answers the question before it is put. */}
            <p className="border-t border-border pt-2 text-xs text-ink-subtle">
              You are receiving this because you are a patient of {clinic.clinic_name}.{" "}
              <span className="underline">Stop receiving these reminders</span>.
            </p>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
