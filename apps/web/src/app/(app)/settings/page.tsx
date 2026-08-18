/**
 * Settings.
 *
 * Placeholder. The real screen arrives in phase 10 — this exists so the shell is navigable,
 * so the navigation's active state can be verified, and so the end-to-end suite has a route to
 * visit before the content lands.
 */

import { Card, PageHeader } from "@/components/ui/primitives";

export const metadata = { title: "Settings" };

export default function SettingsPage() {
  return (
    <>
      <PageHeader title="Settings" description="Clinic profile, reminder settings, and your account." />
      <Card className="p-6">
        <p className="text-sm text-ink-muted">
          This screen is built in phase 10.
        </p>
      </Card>
    </>
  );
}
