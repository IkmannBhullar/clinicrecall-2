/**
 * The patients list (SPEC §8).
 *
 * A server component: it reads the filters out of the URL, fetches that exact page from the API,
 * and renders real rows into the first HTML the browser receives. The interactive parts —
 * filter chips, pagination, the drawer — are client components layered on top.
 *
 * Search, filtering, and pagination all happen server-side. That is the difference between a
 * screen that works for the 55 demo patients and one that works for a real practice's 40,000.
 */

import { apiFetch } from "@/lib/api";
import { getAccessToken } from "@/lib/supabase/server";
import Link from "next/link";

import { Button } from "@/components/ui/button";
import { PageHeader } from "@/components/ui/primitives";
import { PatientsView } from "@/components/patients/patients-view";
import type { DashboardResponse, PatientListResponse } from "@/lib/patients";

export const metadata = { title: "Patients" };

/** Next.js 15 passes search params as a promise. */
type SearchParams = Promise<Record<string, string | string[] | undefined>>;

export default async function PatientsPage({
  searchParams,
}: {
  searchParams: SearchParams;
}) {
  const params = await searchParams;
  const accessToken = await getAccessToken();

  // Rebuild the query for the API from the URL, so the two cannot disagree about what is being
  // shown. Anything not recognised here is simply not forwarded.
  const query = new URLSearchParams();

  const search = typeof params.search === "string" ? params.search : undefined;
  if (search) query.set("search", search);

  const statuses = params.status
    ? Array.isArray(params.status)
      ? params.status
      : [params.status]
    : [];
  for (const status of statuses) query.append("status", status);

  const page = typeof params.page === "string" ? params.page : "1";
  query.set("page", page);

  const [data, dashboard] = await Promise.all([
    apiFetch<PatientListResponse>(`/patients?${query.toString()}`, { accessToken }),
    // Fetched for `today` alone: due dates are phrased relative to the *practice's* current date,
    // not the reader's browser clock, so "due in 11 days" agrees with the status badge beside it
    // even for someone viewing from another timezone.
    apiFetch<DashboardResponse>("/dashboard", { accessToken }),
  ]);

  return (
    <>
      <PageHeader
        title="Patients"
        description="Search, filter, and act on individual patients."
        // Import lives here rather than in the main navigation, which stays at the five items
        // SPEC 8 names. Importing is something you do to your patient list.
        action={
          <Button asChild variant="secondary">
            <Link href="/import">Import patients</Link>
          </Button>
        }
      />
      <PatientsView data={data} today={dashboard.today} />
    </>
  );
}
