"use client";

/**
 * The activity feed (SPEC §8).
 *
 * Chronological, with All / Reminders / Patients / Imports filters.
 *
 * **Initials, never names.** SPEC §8 asks for that in the high-level list, and it is the right
 * default for a screen that is essentially a scrolling log: it is the page most likely to be left
 * open on a shared monitor at a front desk. Someone who needs the name clicks through to the
 * patient.
 *
 * The filter lives in the URL, like everywhere else, so a filtered view can be linked to and the
 * back button behaves.
 */

import { Activity as ActivityIcon } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import * as React from "react";

import { Button } from "@/components/ui/button";
import { Card, EmptyState, Spinner } from "@/components/ui/primitives";
import { formatRelative } from "@/lib/format";
import { toDisplayMessage, useApi } from "@/lib/use-api";
import type { ActivityEntry, ActivityResponse } from "@/lib/settings";
import { cn } from "@/lib/utils";

const FILTERS = [
  { value: "", label: "All" },
  { value: "reminders", label: "Reminders" },
  { value: "patients", label: "Patients" },
  { value: "imports", label: "Imports" },
] as const;

/** A dot colour per event family, so the feed is scannable without reading every line. */
function toneFor(type: string): string {
  if (type === "REMINDER_FAILED") return "bg-status-overdue";
  if (type === "REMINDER_DELIVERED" || type === "ANNUAL_VISIT_COMPLETED") return "bg-status-active";
  if (type === "APPOINTMENT_SCHEDULED") return "bg-status-scheduled";
  if (type === "PATIENT_OPTED_OUT" || type === "REMINDERS_PAUSED") return "bg-status-due-soon";
  return "bg-border-strong";
}

export function ActivityFeed({ initial }: { initial: ActivityResponse }) {
  const api = useApi();
  const router = useRouter();
  const searchParams = useSearchParams();
  const active = searchParams.get("filter") ?? "";

  const [entries, setEntries] = React.useState<ActivityEntry[]>(initial.entries);
  const [hasMore, setHasMore] = React.useState(initial.has_more);
  const [page, setPage] = React.useState(1);
  const [loading, setLoading] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);

  // Reset when the server sends a different filter's first page.
  React.useEffect(() => {
    setEntries(initial.entries);
    setHasMore(initial.has_more);
    setPage(1);
  }, [initial]);

  function setFilter(value: string) {
    router.push(value ? `/activity?filter=${value}` : "/activity");
  }

  async function loadMore() {
    setLoading(true);
    setError(null);

    try {
      const next = page + 1;
      const query = new URLSearchParams({ page: String(next) });
      if (active) query.set("filter", active);

      const result = await api.get<ActivityResponse>(`/activity?${query.toString()}`);
      setEntries((current) => [...current, ...result.entries]);
      setHasMore(result.has_more);
      setPage(next);
    } catch (caught) {
      setError(toDisplayMessage(caught));
    } finally {
      setLoading(false);
    }
  }

  return (
    <>
      <div className="mb-4 flex flex-wrap gap-1.5">
        {FILTERS.map((filter) => (
          <button
            key={filter.value}
            type="button"
            onClick={() => setFilter(filter.value)}
            aria-pressed={active === filter.value}
            className={cn(
              "rounded-pill border px-3 py-1 text-xs font-medium transition-colors",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand",
              active === filter.value
                ? "border-brand bg-brand-subtle text-brand"
                : "border-border bg-surface text-ink-muted hover:border-border-strong hover:text-ink",
            )}
          >
            {filter.label}
          </button>
        ))}
      </div>

      <Card>
        {entries.length === 0 ? (
          <EmptyState
            icon={<ActivityIcon className="size-8" />}
            title="Nothing here yet"
            description="Reminders, imports, and patient updates appear here as they happen."
          />
        ) : (
          <>
            <ol className="divide-y divide-border">
              {entries.map((entry) => (
                <li key={entry.id} className="flex items-start gap-3 px-5 py-3">
                  <span
                    className={cn("mt-1.5 size-2 shrink-0 rounded-pill", toneFor(entry.type))}
                    aria-hidden="true"
                  />

                  <div className="min-w-0 flex-1">
                    <p className="text-sm text-ink">
                      {entry.patient_public_id ? (
                        <Link
                          href={`/patients?patient=${entry.patient_public_id}`}
                          className="rounded-control hover:text-brand hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand"
                        >
                          {entry.summary}
                        </Link>
                      ) : (
                        entry.summary
                      )}
                    </p>
                    <p className="mt-0.5 text-xs text-ink-subtle">
                      {formatRelative(entry.created_at)}
                      {/* No actor means the system did it — the reminder job, or a status
                          recompute. Saying so is better than inventing a name. */}
                      {" · "}
                      {entry.actor_initials ? `by ${entry.actor_initials}` : "automatic"}
                    </p>
                  </div>
                </li>
              ))}
            </ol>

            {hasMore ? (
              <div className="border-t border-border px-5 py-3">
                <Button variant="secondary" size="sm" onClick={loadMore} disabled={loading}>
                  {loading ? <Spinner /> : null}
                  {loading ? "Loading…" : "Load more"}
                </Button>
                {error ? (
                  <p role="alert" className="mt-2 text-sm text-danger">
                    {error}
                  </p>
                ) : null}
              </div>
            ) : null}
          </>
        )}
      </Card>
    </>
  );
}
