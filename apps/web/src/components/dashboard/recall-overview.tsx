/**
 * The Recall Overview (SPEC §8).
 *
 * A stacked proportional bar over the seven statuses, with a legend beneath it.
 *
 * **Why a bar rather than a pie or a donut.** The question this answers is "how much of my list
 * needs work?", which is a part-to-whole comparison of adjacent segments. People read length far
 * more accurately than angle or area, so a single bar answers it at a glance where a pie needs
 * the numbers printed on it to be useful at all.
 *
 * Every segment is also listed in the legend with its count, so the visualisation is a summary
 * rather than the only way to read the data — and colour is never the sole carrier of meaning
 * (SPEC §10).
 */

import Link from "next/link";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/primitives";
import { STATUS_DESCRIPTIONS, STATUS_LABELS, type PatientStatus } from "@/lib/types";
import { cn } from "@/lib/utils";

/** Background colours, from the status tokens in globals.css. */
const SEGMENT_COLOURS: Record<PatientStatus, string> = {
  OVERDUE: "bg-status-overdue",
  DUE: "bg-status-due",
  DUE_SOON: "bg-status-due-soon",
  SCHEDULED: "bg-status-scheduled",
  ACTIVE: "bg-status-active",
  COMPLETED: "bg-status-completed",
  INACTIVE: "bg-status-inactive",
};

export type StatusCount = { status: PatientStatus; count: number };

export function RecallOverview({ counts }: { counts: StatusCount[] }) {
  const total = counts.reduce((sum, entry) => sum + entry.count, 0);
  const present = counts.filter((entry) => entry.count > 0);

  return (
    <Card>
      <CardHeader>
        <CardTitle>Recall overview</CardTitle>
      </CardHeader>
      <CardContent>
        {total === 0 ? (
          <p className="text-sm text-ink-muted">No patients yet.</p>
        ) : (
          <>
            {/* The bar. role="img" with a label, because the segments themselves are decorative
                — the legend below carries the same information as text. */}
            <div
              className="flex h-2.5 w-full overflow-hidden rounded-pill"
              role="img"
              aria-label={present
                .map((entry) => `${entry.count} ${STATUS_LABELS[entry.status]}`)
                .join(", ")}
            >
              {present.map((entry) => (
                <div
                  key={entry.status}
                  className={cn(SEGMENT_COLOURS[entry.status])}
                  style={{ width: `${(entry.count / total) * 100}%` }}
                  title={`${STATUS_LABELS[entry.status]}: ${entry.count}`}
                />
              ))}
            </div>

            {/* Two columns, not three. This card sits in the narrow sidebar column, and at
                three the "Due soon" label wrapped onto a second line while its count stayed
                pinned right by ml-auto — so the number floated away from the label it
                belonged to. Two columns fit every label on one line at every width. */}
            <ul className="mt-4 grid grid-cols-2 gap-x-4 gap-y-2.5">
              {counts.map((entry) => (
                <li key={entry.status}>
                  <Link
                    href={`/patients?status=${entry.status}`}
                    title={STATUS_DESCRIPTIONS[entry.status]}
                    className="group flex items-center gap-2 rounded-control focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand"
                  >
                    <span
                      className={cn("size-2 shrink-0 rounded-pill", SEGMENT_COLOURS[entry.status])}
                      aria-hidden="true"
                    />
                    <span className="whitespace-nowrap text-sm text-ink-muted group-hover:text-ink">
                      {STATUS_LABELS[entry.status]}
                    </span>
                    <span className="ml-auto text-sm font-medium tabular-nums text-ink">
                      {entry.count}
                    </span>
                  </Link>
                </li>
              ))}
            </ul>
          </>
        )}
      </CardContent>
    </Card>
  );
}
