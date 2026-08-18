/**
 * The dashboard (SPEC §8).
 *
 * The first screen anyone sees, and the one the demo opens on. A server component, so the numbers
 * are in the first HTML the browser receives — no spinner, no layout shift, and nothing that
 * looks like the product thinking.
 *
 * One API call fetches everything. Five calls would mean five chances to render half a screen,
 * and figures computed seconds apart that quietly disagree with each other.
 */

import { AlertTriangle, CalendarClock, Send, Users } from "lucide-react";

import { NeedsAttention } from "@/components/dashboard/needs-attention";
import { KpiCard } from "@/components/dashboard/kpi-card";
import { RecallOverview } from "@/components/dashboard/recall-overview";
import { RecentReminders } from "@/components/dashboard/recent-reminders";
import { RevenueCard } from "@/components/dashboard/revenue-card";
import { apiFetch } from "@/lib/api";
import { getAccessToken } from "@/lib/supabase/server";
import type { DashboardResponse } from "@/lib/patients";
import type { Session } from "@/lib/types";

export const metadata = { title: "Dashboard" };

export default async function DashboardPage() {
  const accessToken = await getAccessToken();

  const [dashboard, session] = await Promise.all([
    apiFetch<DashboardResponse>("/dashboard", { accessToken }),
    apiFetch<Session>("/me", { accessToken }),
  ]);

  const firstName = session.user.first_name;

  return (
    <>
      {/* SPEC §8: greeting, then the line that says what this screen is. */}
      <div className="mb-6">
        <h2 className="text-xl font-semibold tracking-tight text-ink">
          {greeting()}, {firstName}
        </h2>
        <p className="mt-1 text-sm text-ink-muted">Here&rsquo;s your patient recall overview.</p>
      </div>

      {/* The five KPI cards, in the order SPEC §8 lists them. Three of them link to the patient
          list already filtered — a number worth showing is usually a number someone wants to
          click through.

          A labelled <section> rather than a bare <div>: it gives screen-reader users a landmark
          to jump to, and it gives the rest of the page an unambiguous way to refer to "the KPI
          row" — the word "Overdue" also appears on eight status badges and in the recall
          overview legend. */}
      <section aria-label="Key metrics" className="grid gap-4 sm:grid-cols-2 xl:grid-cols-5">
        <KpiCard
          label="Total patients"
          value={dashboard.total_patients}
          icon={Users}
          href="/patients"
        />
        <KpiCard
          label="Due this month"
          value={dashboard.due_this_month}
          icon={CalendarClock}
          hint="Including anyone already overdue"
          href="/patients?status=DUE&status=DUE_SOON&status=OVERDUE"
        />
        <KpiCard
          label="Overdue"
          value={dashboard.overdue}
          icon={AlertTriangle}
          emphasis={dashboard.overdue > 0 ? "warning" : "default"}
          href="/patients?status=OVERDUE"
        />
        <KpiCard
          label="Reminders sent"
          value={dashboard.reminders_sent_this_month}
          icon={Send}
          hint="This month"
          href="/reminders"
        />
        <RevenueCard
          appointmentsRecovered={dashboard.revenue.appointments_recovered}
          estimatedValue={dashboard.revenue.estimated_value}
          valuePerVisit={dashboard.revenue.value_per_visit}
          definition={dashboard.revenue.definition}
        />
      </section>

      <div className="mt-6 grid gap-6 lg:grid-cols-3">
        <div className="lg:col-span-2">
          <NeedsAttention patients={dashboard.needs_attention} today={dashboard.today} />
        </div>
        <div className="space-y-6">
          <RecallOverview counts={dashboard.recall_overview} />
          <RecentReminders reminders={dashboard.recent_reminders} />
        </div>
      </div>
    </>
  );
}

/**
 * "Good morning" / "Good afternoon" / "Good evening".
 *
 * Uses the server's clock rather than the practice's timezone, which is a deliberate simplifi-
 * cation: getting this wrong greets someone with the wrong time of day, which is mildly odd.
 * Getting a *due date* wrong misstates a patient's care, which is why that one goes through
 * `today_for_org` and this one does not.
 */
function greeting(): string {
  const hour = new Date().getHours();
  if (hour < 12) return "Good morning";
  if (hour < 18) return "Good afternoon";
  return "Good evening";
}
