"use client";

/**
 * "Send Test Reminder" (SPEC §8).
 *
 * Goes through the mock provider like any other send, so what appears is the real message
 * rendered by the real template — not a preview that might differ from what patients receive.
 *
 * Recorded with `source = TEST`, so it is distinguishable in a patient's timeline. Staff need to
 * tell "we tested this" from "we chased this patient", or a demo leaves fake chases scattered
 * through real history.
 */

import { Send } from "lucide-react";
import { useRouter } from "next/navigation";
import * as React from "react";

import { Button } from "@/components/ui/button";
import { Card, CardHeader, CardTitle, CardDescription, CardContent, Spinner } from "@/components/ui/primitives";
import { toDisplayMessage, useApi } from "@/lib/use-api";

export function TestSend() {
  const api = useApi();
  const router = useRouter();

  const [busy, setBusy] = React.useState(false);
  const [result, setResult] = React.useState<{ text: string; tone: "ok" | "error" } | null>(null);

  async function send() {
    setBusy(true);
    setResult(null);

    try {
      const event = await api.post<{ rendered_subject: string | null; status: string }>(
        "/reminders/test",
        {},
      );
      setResult({
        text:
          event.status === "FAILED"
            ? "The test reminder was rejected by the mail provider — which is what a bounce looks like."
            : `Test reminder sent. Subject: “${event.rendered_subject ?? "reminder"}”`,
        tone: event.status === "FAILED" ? "error" : "ok",
      });
      router.refresh();
    } catch (caught) {
      setResult({ text: toDisplayMessage(caught), tone: "error" });
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Send a test reminder</CardTitle>
        <CardDescription>
          Sends the real message to your most overdue patient, so you can see exactly what they
          receive. It is recorded as a test, not as a chase.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <Button onClick={send} disabled={busy} variant="secondary">
          {busy ? <Spinner /> : <Send aria-hidden="true" />}
          {busy ? "Sending…" : "Send test reminder"}
        </Button>

        {result ? (
          <p
            role="status"
            aria-live="polite"
            className={
              result.tone === "ok"
                ? "mt-3 text-sm text-success"
                : "mt-3 text-sm text-danger"
            }
          >
            {result.text}
          </p>
        ) : null}
      </CardContent>
    </Card>
  );
}
