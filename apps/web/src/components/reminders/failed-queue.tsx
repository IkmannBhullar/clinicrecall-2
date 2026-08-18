"use client";

/**
 * The failure-recovery queue (SPEC §8: "Failed items link to a fix-email recovery path").
 *
 * A hard bounce means the address is wrong. So "retry" on its own is useless — it would fail
 * identically, forever. The only action that helps is *correct the address and send again*, which
 * is what this does in one step.
 *
 * That is the difference between a queue where work gets done and a list of things that went
 * wrong, which is what a failed-items screen becomes when its only button is "try again".
 */

import { AlertTriangle, CheckCircle2 } from "lucide-react";
import { useRouter } from "next/navigation";
import * as React from "react";

import { Button } from "@/components/ui/button";
import {
  Card,
  CardHeader,
  CardTitle,
  CardDescription,
  EmptyState,
  FieldError,
  Input,
  Label,
  Spinner,
} from "@/components/ui/primitives";
import { toDisplayMessage, useApi } from "@/lib/use-api";
import type { FailedReminder } from "@/lib/settings";

export function FailedQueue({ initialFailures }: { initialFailures: FailedReminder[] }) {
  const api = useApi();
  const router = useRouter();

  const [failures, setFailures] = React.useState(initialFailures);
  const [editing, setEditing] = React.useState<string | null>(null);
  const [email, setEmail] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const [notice, setNotice] = React.useState<string | null>(null);

  function startEditing(failure: FailedReminder) {
    setEditing(failure.id);
    setEmail(failure.patient_email);
    setError(null);
    setNotice(null);
  }

  async function submit(failure: FailedReminder) {
    setBusy(true);
    setError(null);

    try {
      const result = await api.post<{ message: string }>(
        `/reminders/${failure.id}/fix-email`,
        { email, resend: true },
      );
      // Drop it from the queue: the address is fixed, so the entry is resolved whether or not
      // the resend itself went through. Leaving it would suggest there is still work to do.
      setFailures((current) => current.filter((f) => f.id !== failure.id));
      setEditing(null);
      setNotice(result.message);
      router.refresh();
    } catch (caught) {
      setError(toDisplayMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Reminders that failed</CardTitle>
        <CardDescription>
          These addresses were rejected. Correct one and the reminder is sent again straight away.
        </CardDescription>
      </CardHeader>

      {notice ? (
        <p
          role="status"
          className="mx-5 mb-3 flex items-start gap-2 rounded-control bg-success-bg px-3 py-2 text-sm text-success"
        >
          <CheckCircle2 className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
          {notice}
        </p>
      ) : null}

      {failures.length === 0 ? (
        <EmptyState
          icon={<CheckCircle2 className="size-8" />}
          title="Nothing has failed"
          description="Every reminder reached its patient. Failed deliveries appear here so you can correct the address."
        />
      ) : (
        <ul className="divide-y divide-border border-t border-border">
          {failures.map((failure) => (
            <li key={failure.id} className="px-5 py-4">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0">
                  <p className="text-sm font-medium text-ink">{failure.patient_name}</p>
                  <p className="truncate text-xs text-ink-subtle">{failure.patient_email}</p>
                  {failure.failure_reason ? (
                    <p className="mt-1.5 inline-flex items-start gap-1.5 text-xs text-danger">
                      <AlertTriangle className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
                      {failure.failure_reason}
                    </p>
                  ) : null}
                </div>

                {editing !== failure.id ? (
                  <Button variant="secondary" size="sm" onClick={() => startEditing(failure)}>
                    Fix email address
                    <span className="sr-only"> for {failure.patient_name}</span>
                  </Button>
                ) : null}
              </div>

              {editing === failure.id ? (
                <form
                  className="mt-3 flex flex-wrap items-end gap-2"
                  onSubmit={(event) => {
                    event.preventDefault();
                    submit(failure);
                  }}
                >
                  <div className="min-w-56 flex-1">
                    <Label htmlFor={`email-${failure.id}`}>Corrected email address</Label>
                    <Input
                      id={`email-${failure.id}`}
                      type="email"
                      required
                      value={email}
                      onChange={(event) => setEmail(event.target.value)}
                      aria-invalid={error ? true : undefined}
                      aria-describedby={error ? `error-${failure.id}` : undefined}
                      className="mt-1.5"
                    />
                  </div>
                  <Button type="submit" size="sm" disabled={busy}>
                    {busy ? <Spinner /> : null}
                    Save and resend
                  </Button>
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    disabled={busy}
                    onClick={() => setEditing(null)}
                  >
                    Cancel
                  </Button>
                  <div aria-live="polite" className="w-full">
                    <FieldError id={`error-${failure.id}`}>{error}</FieldError>
                  </div>
                </form>
              ) : null}
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}
