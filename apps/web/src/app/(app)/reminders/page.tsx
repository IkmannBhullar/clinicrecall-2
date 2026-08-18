/**
 * Reminders.
 *
 * Placeholder. The real screen arrives in phase 10 — this exists so the shell is navigable,
 * so the navigation's active state can be verified, and so the end-to-end suite has a route to
 * visit before the content lands.
 */

import { Card, PageHeader } from "@/components/ui/primitives";

export const metadata = { title: "Reminders" };

export default function RemindersPage() {
  return (
    <>
      <PageHeader title="Reminders" description="Your annual recall campaign and how it is performing." />
      <Card className="p-6">
        <p className="text-sm text-ink-muted">
          This screen is built in phase 10.
        </p>
      </Card>
    </>
  );
}
