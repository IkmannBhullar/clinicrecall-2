/**
 * Recent reminder activity (SPEC §8).
 *
 * Initials rather than names, matching the convention SPEC §8 sets for the activity feed. A
 * dashboard is the screen most likely to be projected in a meeting or screenshotted, and a
 * column of full names is the part of it that should not be.
 */

import Link from "next/link";
import { Inbox } from "lucide-react";

import {
  Card,
  CardHeader,
  CardTitle,
  CardContent,
  EmptyState,
} from "@/components/ui/primitives";
import { formatRelative } from "@/lib/format";
import { REMINDER_STATUS_LABELS, REMINDER_STATUS_STYLES, RULE_SHORT_LABELS } from "@/lib/patients";
import { cn } from "@/lib/utils";

type RecentReminder = {
  patient_public_id: string;
  patient_initials: string;
  status: string;
  sent_at: string | null;
  rule_key: string | null;
};

export function RecentReminders({ reminders }: { reminders: RecentReminder[] }) {
  return (
    <Card>
      <CardHeader className="flex items-center justify-between">
        <CardTitle>Recent reminders</CardTitle>
        <Link
          href="/reminders"
          className="rounded-control text-xs font-medium text-brand hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand"
        >
          View all
        </Link>
      </CardHeader>

      <CardContent className="pt-0">
        {reminders.length === 0 ? (
          <EmptyState
            icon={<Inbox className="size-7" />}
            title="No reminders yet"
            description="Reminders appear here once the recall campaign starts sending."
          />
        ) : (
          <ul className="divide-y divide-border">
            {reminders.map((reminder, index) => (
              <li
                key={`${reminder.patient_public_id}-${index}`}
                className="flex items-center gap-3 py-2.5 first:pt-0 last:pb-0"
              >
                <span
                  className="flex size-7 shrink-0 items-center justify-center rounded-pill bg-canvas text-[11px] font-semibold text-ink-muted"
                  aria-hidden="true"
                >
                  {reminder.patient_initials}
                </span>

                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm text-ink">
                    {reminder.rule_key
                      ? (RULE_SHORT_LABELS[reminder.rule_key] ?? reminder.rule_key)
                      : "Manual reminder"}
                  </p>
                  <p className="text-xs text-ink-subtle">{formatRelative(reminder.sent_at)}</p>
                </div>

                <span
                  className={cn(
                    "shrink-0 rounded-pill px-2 py-0.5 text-[11px] font-medium",
                    REMINDER_STATUS_STYLES[reminder.status] ??
                      "bg-status-inactive-bg text-status-inactive",
                  )}
                >
                  {REMINDER_STATUS_LABELS[reminder.status] ?? reminder.status}
                </span>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}
