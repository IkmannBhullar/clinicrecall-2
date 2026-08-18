/**
 * "Patients Needing Attention" (SPEC §8).
 *
 * Columns exactly as specified: Patient · Due Date · Status · Last Reminder · Action.
 *
 * This is the dashboard's work queue — overdue first, longest-waiting at the top — so the first
 * row is the person someone should call now. It is deliberately short: a table of eight is a
 * to-do list, and a table of eighty is a report nobody acts on. "View all" leads to the full,
 * filterable list.
 */

import Link from "next/link";
import { CheckCircle2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Card,
  CardHeader,
  CardTitle,
  EmptyState,
} from "@/components/ui/primitives";
import { StatusBadge } from "@/components/ui/status-badge";
import { formatDueDate, formatRelative } from "@/lib/format";
import type { PatientSummary } from "@/lib/patients";

export function NeedsAttention({
  patients,
  today,
}: {
  patients: PatientSummary[];
  today: string;
}) {
  return (
    <Card>
      <CardHeader className="flex items-center justify-between">
        <CardTitle>Patients needing attention</CardTitle>
        <Link
          href="/patients?status=OVERDUE&status=DUE"
          className="rounded-control text-xs font-medium text-brand hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand"
        >
          View all
        </Link>
      </CardHeader>

      {patients.length === 0 ? (
        <EmptyState
          icon={<CheckCircle2 className="size-8" />}
          title="Nobody is overdue"
          description="Every patient is either up to date or already booked in. This is what a healthy recall list looks like."
        />
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-y border-border text-left">
                <th scope="col" className="px-5 py-2.5 font-medium text-ink-muted">
                  Patient
                </th>
                <th scope="col" className="px-5 py-2.5 font-medium text-ink-muted">
                  Due date
                </th>
                <th scope="col" className="px-5 py-2.5 font-medium text-ink-muted">
                  Status
                </th>
                <th scope="col" className="px-5 py-2.5 font-medium text-ink-muted">
                  Last reminder
                </th>
                <th scope="col" className="px-5 py-2.5 text-right font-medium text-ink-muted">
                  <span className="sr-only">Action</span>
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {patients.map((patient) => (
                <tr key={patient.public_id} className="hover:bg-canvas">
                  <td className="px-5 py-3">
                    <Link
                      href={`/patients?patient=${patient.public_id}`}
                      className="rounded-control font-medium text-ink hover:text-brand focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand"
                    >
                      {patient.first_name} {patient.last_name}
                    </Link>
                  </td>
                  <td className="px-5 py-3 text-ink-muted">
                    {formatDueDate(patient.next_annual_due_date, today)}
                  </td>
                  <td className="px-5 py-3">
                    <StatusBadge status={patient.status} />
                  </td>
                  <td className="px-5 py-3 text-ink-muted">
                    {formatRelative(patient.last_reminder_at)}
                  </td>
                  <td className="px-5 py-3 text-right">
                    <Button asChild variant="secondary" size="sm">
                      <Link href={`/patients?patient=${patient.public_id}`}>View</Link>
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}
