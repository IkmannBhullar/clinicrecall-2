/**
 * Patients.
 *
 * Placeholder. The real screen arrives in phase 9 — this exists so the shell is navigable,
 * so the navigation's active state can be verified, and so the end-to-end suite has a route to
 * visit before the content lands.
 */

import { Card, PageHeader } from "@/components/ui/primitives";

export const metadata = { title: "Patients" };

export default function PatientsPage() {
  return (
    <>
      <PageHeader title="Patients" description="Search, filter, and act on individual patients." />
      <Card className="p-6">
        <p className="text-sm text-ink-muted">
          This screen is built in phase 9.
        </p>
      </Card>
    </>
  );
}
