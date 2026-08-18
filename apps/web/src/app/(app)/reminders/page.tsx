/**
 * The Reminders screen (SPEC §8).
 *
 * The campaign and its four toggles, a live preview of the message beside them, the test send,
 * the delivery-performance strip, and the failure-recovery queue.
 *
 * The preview sits next to the rules deliberately — SPEC §8 asks for "side-by-side live preview",
 * and the reason is that a toggle labelled "7 days before due" is abstract until you can see the
 * thing it sends.
 */

import { apiFetch } from "@/lib/api";
import { getAccessToken } from "@/lib/supabase/server";
import { CampaignRules } from "@/components/reminders/campaign-rules";
import { FailedQueue } from "@/components/reminders/failed-queue";
import { MessagePreviewPanel } from "@/components/reminders/message-preview-panel";
import { PageHeader } from "@/components/ui/primitives";
import { PerformanceStrip } from "@/components/reminders/performance-strip";
import { TestSend } from "@/components/reminders/test-send";
import type { FailedReminder, ReminderPerformance, SettingsPage } from "@/lib/settings";

export const metadata = { title: "Reminders" };

export default async function RemindersPage() {
  const accessToken = await getAccessToken();

  const [settings, performance, failed] = await Promise.all([
    apiFetch<SettingsPage>("/settings", { accessToken }),
    apiFetch<ReminderPerformance>("/reminders/performance", { accessToken }),
    apiFetch<FailedReminder[]>("/reminders/failed", { accessToken }),
  ]);

  return (
    <>
      <PageHeader
        title="Reminders"
        description="Your annual recall campaign, and how it is performing."
      />

      <PerformanceStrip performance={performance} />

      <div className="mt-6 grid gap-6 lg:grid-cols-2">
        <div className="space-y-6">
          <CampaignRules initialRules={settings.rules} />
          <TestSend />
        </div>

        <MessagePreviewPanel clinic={settings.clinic} />
      </div>

      <div className="mt-6">
        <FailedQueue initialFailures={failed} />
      </div>
    </>
  );
}
