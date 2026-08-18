"use client";

/**
 * The patients table (SPEC §8).
 *
 * Columns: Patient · Due date · Status · Last reminder · Action.
 *
 * Bulk selection is deliberately absent — SPEC §8 says to skip it, and it is the right call: a
 * checkbox column implies bulk operations, and the only sensible bulk operation here would be
 * "email 300 people at once", which is exactly the thing that should require deliberation.
 *
 * Opening a patient writes `?patient=…` into the URL rather than into React state, so a specific
 * patient can be linked to. That is what lets the dashboard's "View" buttons and the Playwright
 * suite jump straight to Sarah Johnson.
 */

import { Users } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";

import { Button } from "@/components/ui/button";
import { Card, EmptyState } from "@/components/ui/primitives";
import { StatusBadge } from "@/components/ui/status-badge";
import { formatDueDate, formatRelative } from "@/lib/format";
import type { PatientSummary } from "@/lib/patients";

export function PatientsTable({
  patients,
  today,
  hasFilters,
}: {
  patients: PatientSummary[];
  today: string;
  hasFilters: boolean;
}) {
  const router = useRouter();
  const searchParams = useSearchParams();

  function openPatient(publicId: string) {
    const params = new URLSearchParams(searchParams.toString());
    params.set("patient", publicId);
    router.push(`/patients?${params.toString()}`, { scroll: false });
  }

  if (patients.length === 0) {
    return (
      <Card>
        <EmptyState
          icon={<Users className="size-8" />}
          title={hasFilters ? "No patients match those filters" : "No patients yet"}
          description={
            hasFilters
              ? "Try clearing a filter or searching for a different name."
              : "Import a patient list to get started — Settings has the CSV format."
          }
          action={
            hasFilters ? (
              <Button variant="secondary" size="sm" onClick={() => router.push("/patients")}>
                Clear filters
              </Button>
            ) : null
          }
        />
      </Card>
    );
  }

  return (
    <Card className="overflow-hidden">
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <caption className="sr-only">
            Patients, with their next annual visit date, recall status, and most recent reminder.
          </caption>
          <thead>
            <tr className="border-b border-border text-left">
              <th scope="col" className="px-5 py-2.5 font-medium text-ink-muted">Patient</th>
              <th scope="col" className="px-5 py-2.5 font-medium text-ink-muted">Due date</th>
              <th scope="col" className="px-5 py-2.5 font-medium text-ink-muted">Status</th>
              <th scope="col" className="px-5 py-2.5 font-medium text-ink-muted">Last reminder</th>
              <th scope="col" className="px-5 py-2.5 text-right font-medium text-ink-muted">
                <span className="sr-only">Actions</span>
              </th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border">
            {patients.map((patient) => (
              <tr key={patient.public_id} className="hover:bg-canvas">
                <td className="px-5 py-3">
                  {/* A button rather than a link: this opens a drawer over the current view, and
                      an <a> would promise a navigation that does not happen. */}
                  <button
                    type="button"
                    onClick={() => openPatient(patient.public_id)}
                    className="rounded-control text-left font-medium text-ink hover:text-brand focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand"
                  >
                    {patient.first_name} {patient.last_name}
                  </button>
                  <p className="truncate text-xs text-ink-subtle">{patient.email}</p>
                </td>

                <td className="whitespace-nowrap px-5 py-3 text-ink-muted">
                  {formatDueDate(patient.next_annual_due_date, today)}
                </td>

                <td className="px-5 py-3">
                  <StatusBadge status={patient.status} />
                </td>

                <td className="whitespace-nowrap px-5 py-3 text-ink-muted">
                  {formatRelative(patient.last_reminder_at)}
                </td>

                <td className="px-5 py-3 text-right">
                  <Button
                    variant="secondary"
                    size="sm"
                    onClick={() => openPatient(patient.public_id)}
                  >
                    View
                    {/* The visible label says "View"; a screen reader hears which patient, since
                        a column of identical "View" buttons is useless without it. */}
                    <span className="sr-only">
                      {" "}
                      {patient.first_name} {patient.last_name}
                    </span>
                  </Button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}
