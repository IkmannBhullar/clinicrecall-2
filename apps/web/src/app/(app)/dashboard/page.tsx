/**
 * Dashboard.
 *
 * Placeholder. The real screen arrives in phase 9 — this exists so the shell is navigable,
 * so the navigation's active state can be verified, and so the end-to-end suite has a route to
 * visit before the content lands.
 */

import { Card, PageHeader } from "@/components/ui/primitives";

export const metadata = { title: "Dashboard" };

export default function DashboardPage() {
  return (
    <>
      <PageHeader title="Dashboard" description="Here's your patient recall overview." />
      <Card className="p-6">
        <p className="text-sm text-ink-muted">
          This screen is built in phase 9.
        </p>
      </Card>
    </>
  );
}
