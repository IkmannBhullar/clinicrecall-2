/**
 * Delivery performance (SPEC §8).
 *
 * Scheduled / Sent / Delivered / Failed, with the failed count linking into the recovery queue —
 * a number nobody can act on is just an accusation.
 *
 * A delivery rate is shown alongside the counts because "49 delivered" means nothing without
 * knowing whether that is out of 50 or out of 500.
 */

import { Card, CardHeader, CardTitle } from "@/components/ui/primitives";
import type { ReminderPerformance } from "@/lib/settings";
import { cn } from "@/lib/utils";

export function PerformanceStrip({ performance }: { performance: ReminderPerformance }) {
  const rate =
    performance.total > 0
      ? Math.round((performance.delivered / performance.total) * 100)
      : null;

  const cells = [
    { label: "Queued", value: performance.scheduled, tone: "text-ink" },
    { label: "Sent", value: performance.sent, tone: "text-status-scheduled" },
    { label: "Delivered", value: performance.delivered, tone: "text-status-active" },
    {
      label: "Failed",
      value: performance.failed,
      tone: performance.failed > 0 ? "text-status-overdue" : "text-ink",
    },
  ];

  return (
    <Card>
      <CardHeader className="flex items-baseline justify-between">
        <CardTitle>Delivery performance</CardTitle>
        {rate !== null ? (
          <p className="text-xs tabular-nums text-ink-subtle">{rate}% delivered</p>
        ) : null}
      </CardHeader>

      <dl className="grid grid-cols-2 divide-x divide-y divide-border border-t border-border sm:grid-cols-4 sm:divide-y-0">
        {cells.map((cell) => (
          <div key={cell.label} className="px-5 py-4">
            <dt className="text-xs font-medium text-ink-muted">{cell.label}</dt>
            <dd className={cn("mt-1 text-xl font-semibold tabular-nums", cell.tone)}>
              {cell.value}
            </dd>
          </div>
        ))}
      </dl>
    </Card>
  );
}
