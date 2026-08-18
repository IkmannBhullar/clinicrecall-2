/**
 * Settings (SPEC §8).
 *
 * Clinic profile, reminder settings, the account you are signed in as, and — fenced off, admin
 * only, and absent in production — the demo utilities.
 */

import { apiFetch } from "@/lib/api";
import { getAccessToken } from "@/lib/supabase/server";
import { ClinicForm } from "@/components/settings/clinic-form";
import { DemoUtilities } from "@/components/settings/demo-utilities";
import { Card, CardContent, CardHeader, CardTitle, PageHeader } from "@/components/ui/primitives";
import type { SettingsPage } from "@/lib/settings";

export const metadata = { title: "Settings" };

export default async function SettingsScreen() {
  const accessToken = await getAccessToken();
  const settings = await apiFetch<SettingsPage>("/settings", { accessToken });

  const isAdmin = settings.account.role === "ADMIN";

  return (
    <>
      <PageHeader
        title="Settings"
        description="Your clinic profile, reminder settings, and account."
      />

      <div className="max-w-3xl space-y-6">
        <ClinicForm initial={settings.clinic} canEdit={isAdmin} />

        <Card>
          <CardHeader>
            <CardTitle>Your account</CardTitle>
          </CardHeader>
          <CardContent>
            <dl className="space-y-1.5 text-sm">
              <div className="flex justify-between gap-4">
                <dt className="text-ink-muted">Name</dt>
                <dd className="text-ink">
                  {settings.account.first_name} {settings.account.last_name}
                </dd>
              </div>
              <div className="flex justify-between gap-4">
                <dt className="text-ink-muted">Email</dt>
                <dd className="text-ink">{settings.account.email}</dd>
              </div>
              <div className="flex justify-between gap-4">
                <dt className="text-ink-muted">Role</dt>
                <dd className="text-ink">
                  {isAdmin ? "Administrator" : "Staff"}
                  {!isAdmin ? (
                    <span className="ml-2 text-xs text-ink-subtle">
                      Ask an administrator to change clinic settings
                    </span>
                  ) : null}
                </dd>
              </div>
            </dl>
          </CardContent>
        </Card>

        {/* Only rendered for an admin in a demo environment. The API enforces both independently
            — this is presentation, not the control. */}
        {isAdmin && settings.demo_utilities_enabled ? <DemoUtilities /> : null}
      </div>
    </>
  );
}
