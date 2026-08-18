"use client";

/**
 * The demo utilities (SPEC §8: "clearly fenced as admin-only", and SPEC constraint D3).
 *
 * Two buttons: run the reminder job, and reset the demo data.
 *
 * They are **fenced deliberately and visibly** — a distinct border, an explicit heading, and a
 * sentence saying what they are. These are demo aids, not product features: one mails patients on
 * demand and the other wipes the database. Someone who wandered into this section should be able
 * to tell in one glance that it is not part of the ordinary interface.
 *
 * The reset asks for confirmation. It is destructive, instant, and irreversible, which is exactly
 * the shape of action that should not happen on a stray click mid-demo.
 */

import { AlertTriangle, PlayCircle, RotateCcw } from "lucide-react";
import { useRouter } from "next/navigation";
import * as React from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle, Spinner } from "@/components/ui/primitives";
import { toDisplayMessage, useApi } from "@/lib/use-api";

type JobSummary = {
  evaluated: number;
  eligible: number;
  created: number;
  skipped_duplicate: number;
  sent: number;
  failed: number;
};

export function DemoUtilities() {
  const api = useApi();
  const router = useRouter();

  const [busy, setBusy] = React.useState<string | null>(null);
  const [notice, setNotice] = React.useState<{ text: string; tone: "ok" | "error" } | null>(null);
  const [confirmingReset, setConfirmingReset] = React.useState(false);

  async function runJob() {
    setBusy("job");
    setNotice(null);
    try {
      const summary = await api.post<JobSummary>("/internal/jobs/process-reminders/mine", {});
      setNotice({
        tone: "ok",
        // The full summary, not just "done". `skipped_duplicate` is the idempotency guarantee
        // made visible — running it twice should send nothing the second time, and being able to
        // point at that number on stage is the whole reason it is in the response.
        text:
          `Evaluated ${summary.evaluated} · sent ${summary.sent} · ` +
          `skipped as already sent ${summary.skipped_duplicate} · failed ${summary.failed}`,
      });
      router.refresh();
    } catch (caught) {
      setNotice({ text: toDisplayMessage(caught), tone: "error" });
    } finally {
      setBusy(null);
    }
  }

  async function resetDemo() {
    setBusy("reset");
    setNotice(null);
    setConfirmingReset(false);
    try {
      const result = await api.post<{ message: string; seconds: number }>("/demo/reset", {});
      setNotice({ tone: "ok", text: `${result.message} (${result.seconds}s)` });
      router.refresh();
    } catch (caught) {
      setNotice({ text: toDisplayMessage(caught), tone: "error" });
    } finally {
      setBusy(null);
    }
  }

  return (
    <Card className="border-warning/40">
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-warning">
          <AlertTriangle className="size-4" aria-hidden="true" />
          Demo utilities
        </CardTitle>
        <CardDescription>
          Administrator only, and only available in a demo environment. These are not part of the
          product — they exist so a demonstration can be run and reset.
        </CardDescription>
      </CardHeader>

      <CardContent className="space-y-4">
        <div className="flex flex-wrap items-center gap-3">
          <Button variant="secondary" size="sm" onClick={runJob} disabled={busy !== null}>
            {busy === "job" ? <Spinner /> : <PlayCircle aria-hidden="true" />}
            Run reminder job
          </Button>

          {confirmingReset ? (
            <span className="flex flex-wrap items-center gap-2">
              <span className="text-sm text-ink">
                This wipes all current data and reloads the demo set. Continue?
              </span>
              <Button variant="danger" size="sm" onClick={resetDemo} disabled={busy !== null}>
                {busy === "reset" ? <Spinner /> : null}
                Yes, reset
              </Button>
              <Button variant="ghost" size="sm" onClick={() => setConfirmingReset(false)}>
                Cancel
              </Button>
            </span>
          ) : (
            <Button
              variant="secondary"
              size="sm"
              onClick={() => setConfirmingReset(true)}
              disabled={busy !== null}
            >
              <RotateCcw aria-hidden="true" />
              Reset demo data
            </Button>
          )}
        </div>

        {notice ? (
          <p
            role="status"
            aria-live="polite"
            className={
              notice.tone === "ok"
                ? "rounded-control bg-success-bg px-3 py-2 text-sm text-success"
                : "rounded-control bg-danger-bg px-3 py-2 text-sm text-danger"
            }
          >
            {notice.text}
          </p>
        ) : null}
      </CardContent>
    </Card>
  );
}
