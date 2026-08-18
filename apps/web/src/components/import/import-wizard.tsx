"use client";

/**
 * The CSV import (SPEC §7.1).
 *
 * SPEC §7 opens by calling this "the single most scrutinized screen in the demo", and requires it
 * to "feel like a commercial import tool, not a file input". The flow is exactly the one it
 * specifies: drag-and-drop → parse → validate → preview with per-row errors → confirm → import.
 *
 * Three things make it feel like a tool rather than a form:
 *
 * - **Nothing is written until "Import" is pressed.** The preview is a genuine dry run against
 *   the practice's real data, so "12 new, 308 updates" is a promise, not an estimate.
 * - **Every rejected row is shown with its line number and a plain-English reason**, and the full
 *   list downloads as a CSV someone can open next to their export.
 * - **The headline numbers are the first thing on the screen**, because "327 records found,
 *   320 ready" is the question everyone has.
 */

import { AlertCircle, CheckCircle2, Download, FileUp, Upload, X } from "lucide-react";
import { useRouter } from "next/navigation";
import * as React from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle, Spinner } from "@/components/ui/primitives";
import { API_BASE_URL } from "@/lib/api";
import { toDisplayMessage, useApi } from "@/lib/use-api";
import { createClient } from "@/lib/supabase/client";
import { PROBLEM_LABELS, type ImportPreview, type ImportResult } from "@/lib/settings";
import { cn } from "@/lib/utils";

type Stage = "choose" | "previewing" | "preview" | "importing" | "done";

export function ImportWizard() {
  const api = useApi();
  const router = useRouter();

  const [stage, setStage] = React.useState<Stage>("choose");
  const [file, setFile] = React.useState<File | null>(null);
  const [preview, setPreview] = React.useState<ImportPreview | null>(null);
  const [result, setResult] = React.useState<ImportResult | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const [dragging, setDragging] = React.useState(false);

  const inputRef = React.useRef<HTMLInputElement>(null);

  async function choose(selected: File) {
    setFile(selected);
    setError(null);
    setStage("previewing");

    try {
      setPreview(await api.upload<ImportPreview>("/patients/import/preview", selected));
      setStage("preview");
    } catch (caught) {
      setError(toDisplayMessage(caught));
      setStage("choose");
    }
  }

  async function confirm() {
    if (!file) return;
    setStage("importing");
    setError(null);

    try {
      setResult(await api.upload<ImportResult>("/patients/import", file));
      setStage("done");
      // The patient list and every dashboard figure have just changed.
      router.refresh();
    } catch (caught) {
      setError(toDisplayMessage(caught));
      setStage("preview");
    }
  }

  /**
   * Download the error report.
   *
   * Built as a blob rather than linked directly, because the endpoint needs an Authorization
   * header and a plain `<a href>` cannot send one.
   */
  async function downloadErrors() {
    if (!file) return;

    const {
      data: { session },
    } = await createClient().auth.getSession();

    const body = new FormData();
    body.append("file", file);

    const response = await fetch(`${API_BASE_URL}/patients/import/errors`, {
      method: "POST",
      headers: session ? { Authorization: `Bearer ${session.access_token}` } : {},
      body,
    });

    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = "import-errors.csv";
    anchor.click();
    URL.revokeObjectURL(url);
  }

  function reset() {
    setStage("choose");
    setFile(null);
    setPreview(null);
    setResult(null);
    setError(null);
  }

  // ---------------------------------------------------------------------------------- Done
  if (stage === "done" && result) {
    return (
      <Card>
        <CardContent className="py-10 text-center">
          <CheckCircle2 className="mx-auto size-10 text-success" aria-hidden="true" />
          <p className="mt-3 text-base font-semibold text-ink">Import complete</p>
          <p className="mt-1 text-sm text-ink-muted">
            {result.created} patient{result.created === 1 ? "" : "s"} added
            {result.updated > 0 ? `, ${result.updated} updated` : null}
            {result.skipped > 0 ? `, ${result.skipped} skipped` : null}.
          </p>
          <div className="mt-5 flex justify-center gap-2">
            <Button onClick={() => router.push("/patients")}>View patients</Button>
            <Button variant="secondary" onClick={reset}>
              Import another file
            </Button>
          </div>
        </CardContent>
      </Card>
    );
  }

  // ------------------------------------------------------------------------------- Preview
  if ((stage === "preview" || stage === "importing") && preview) {
    return (
      <div className="space-y-4">
        <Card>
          <CardHeader className="flex items-start justify-between gap-3">
            <div>
              <CardTitle>{file?.name}</CardTitle>
              <p className="mt-1 text-sm text-ink-muted">
                Nothing has been imported yet. Check the numbers below, then confirm.
              </p>
            </div>
            <Button variant="ghost" size="icon" onClick={reset} disabled={stage === "importing"}>
              <X aria-hidden="true" />
              <span className="sr-only">Choose a different file</span>
            </Button>
          </CardHeader>

          {/* The four headline numbers, in the order the demo script reads them (SPEC §7.2). */}
          <dl className="grid grid-cols-2 divide-x divide-y divide-border border-t border-border sm:grid-cols-4 sm:divide-y-0">
            <Stat label="Records found" value={preview.total_rows} />
            <Stat label="Ready to import" value={preview.valid_rows} tone="text-status-active" />
            <Stat
              label="Missing information"
              value={preview.missing_required}
              tone={preview.missing_required > 0 ? "text-status-due-soon" : undefined}
            />
            <Stat
              label="Invalid emails"
              value={preview.invalid_email}
              tone={preview.invalid_email > 0 ? "text-status-overdue" : undefined}
            />
          </dl>

          <CardContent className="pt-4">
            {/* SPEC §7.1 asks for "X new, Y updates" distinctly — importing the same export
                twice is normal, and someone needs to know which they are about to do. */}
            <p className="text-sm text-ink-muted">
              <span className="font-medium text-ink">{preview.new_count}</span> new patient
              {preview.new_count === 1 ? "" : "s"} and{" "}
              <span className="font-medium text-ink">{preview.update_count}</span> update
              {preview.update_count === 1 ? "" : "s"} to patients you already have.
            </p>

            <div className="mt-4 flex flex-wrap gap-2">
              <Button onClick={confirm} disabled={stage === "importing" || preview.valid_rows === 0}>
                {stage === "importing" ? <Spinner /> : <Upload aria-hidden="true" />}
                {stage === "importing"
                  ? "Importing…"
                  : `Import ${preview.valid_rows} patient${preview.valid_rows === 1 ? "" : "s"}`}
              </Button>

              {preview.problems.length > 0 ? (
                <Button variant="secondary" onClick={downloadErrors}>
                  <Download aria-hidden="true" />
                  Download error report
                </Button>
              ) : null}
            </div>

            {error ? (
              <p role="alert" className="mt-3 text-sm text-danger">
                {error}
              </p>
            ) : null}
          </CardContent>
        </Card>

        {preview.problems.length > 0 ? (
          <Card>
            <CardHeader>
              <CardTitle>Rows that will be skipped</CardTitle>
              <p className="mt-1 text-sm text-ink-muted">
                Row numbers match your spreadsheet, counting the header as row 1. Everything else
                still imports.
              </p>
            </CardHeader>
            <div className="overflow-x-auto border-t border-border">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-border text-left">
                    <th scope="col" className="px-5 py-2 font-medium text-ink-muted">Row</th>
                    <th scope="col" className="px-5 py-2 font-medium text-ink-muted">Problem</th>
                    <th scope="col" className="px-5 py-2 font-medium text-ink-muted">Value</th>
                    <th scope="col" className="px-5 py-2 font-medium text-ink-muted">What to do</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  {preview.problems.map((problem, index) => (
                    <tr key={`${problem.row_number}-${index}`}>
                      <td className="px-5 py-2 tabular-nums text-ink">{problem.row_number}</td>
                      <td className="px-5 py-2 text-ink-muted">
                        {PROBLEM_LABELS[problem.category] ?? problem.category}
                      </td>
                      <td className="max-w-40 truncate px-5 py-2 text-ink-subtle">
                        {problem.value || "—"}
                      </td>
                      <td className="px-5 py-2 text-ink-muted">{problem.reason}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        ) : null}
      </div>
    );
  }

  // -------------------------------------------------------------------------------- Choose
  return (
    <Card>
      <CardContent className="py-6">
        <div
          onDragOver={(event) => {
            event.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={(event) => {
            event.preventDefault();
            setDragging(false);
            const dropped = event.dataTransfer.files[0];
            if (dropped) choose(dropped);
          }}
          className={cn(
            "flex flex-col items-center rounded-card border-2 border-dashed px-6 py-12 text-center transition-colors",
            dragging ? "border-brand bg-brand-subtle" : "border-border-strong",
          )}
        >
          {stage === "previewing" ? (
            <>
              <Spinner className="size-6 text-brand" />
              <p className="mt-3 text-sm text-ink-muted">Checking your file…</p>
            </>
          ) : (
            <>
              <FileUp className="size-8 text-ink-subtle" aria-hidden="true" />
              <p className="mt-3 text-sm font-medium text-ink">
                Drag your patient list here, or choose a file
              </p>
              <p className="mt-1 text-sm text-ink-muted">
                A CSV with first name, last name, email, and last visit date.
              </p>

              {/* A real file input, kept off-screen and driven by the button. Keeps the native
                  file picker, its keyboard behaviour, and its accessibility for free. */}
              <input
                ref={inputRef}
                type="file"
                accept=".csv,text/csv"
                className="sr-only"
                onChange={(event) => {
                  const selected = event.target.files?.[0];
                  if (selected) choose(selected);
                }}
              />
              <Button className="mt-4" onClick={() => inputRef.current?.click()}>
                Choose a file
              </Button>
            </>
          )}
        </div>

        {error ? (
          <p role="alert" className="mt-4 flex items-start gap-2 text-sm text-danger">
            <AlertCircle className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
            {error}
          </p>
        ) : null}
      </CardContent>
    </Card>
  );
}

function Stat({
  label,
  value,
  tone,
}: {
  label: string;
  value: number;
  tone?: string;
}) {
  return (
    <div className="px-5 py-4">
      <dt className="text-xs font-medium text-ink-muted">{label}</dt>
      <dd className={cn("mt-1 text-xl font-semibold tabular-nums", tone ?? "text-ink")}>
        {value}
      </dd>
    </div>
  );
}
