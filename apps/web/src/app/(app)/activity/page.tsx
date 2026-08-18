/**
 * Activity.
 *
 * Placeholder. The real screen arrives in phase 10 — this exists so the shell is navigable,
 * so the navigation's active state can be verified, and so the end-to-end suite has a route to
 * visit before the content lands.
 */

import { Card, PageHeader } from "@/components/ui/primitives";

export const metadata = { title: "Activity" };

export default function ActivityPage() {
  return (
    <>
      <PageHeader title="Activity" description="Everything that has happened, most recent first." />
      <Card className="p-6">
        <p className="text-sm text-ink-muted">
          This screen is built in phase 10.
        </p>
      </Card>
    </>
  );
}
