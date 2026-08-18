/**
 * The activity feed (SPEC §8).
 *
 * A server component fetches the first page so the feed is in the initial HTML; the client
 * component beneath handles the filters and "Load more".
 */

import { apiFetch } from "@/lib/api";
import { getAccessToken } from "@/lib/supabase/server";
import { ActivityFeed } from "@/components/activity/activity-feed";
import { PageHeader } from "@/components/ui/primitives";
import type { ActivityResponse } from "@/lib/settings";

export const metadata = { title: "Activity" };

type SearchParams = Promise<Record<string, string | string[] | undefined>>;

export default async function ActivityPage({ searchParams }: { searchParams: SearchParams }) {
  const params = await searchParams;
  const accessToken = await getAccessToken();

  const filter = typeof params.filter === "string" ? params.filter : undefined;
  const query = filter ? `?filter=${encodeURIComponent(filter)}` : "";

  const initial = await apiFetch<ActivityResponse>(`/activity${query}`, { accessToken });

  return (
    <>
      <PageHeader
        title="Activity"
        description="Everything that has happened, most recent first."
      />
      <ActivityFeed initial={initial} />
    </>
  );
}
