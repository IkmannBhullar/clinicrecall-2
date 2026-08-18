"use client";

/**
 * Search and status filters for the patients list.
 *
 * **The URL is the state.** Search text, active filters, page, and the open patient all live in
 * the query string rather than in React state. That is a deliberate choice with several
 * consequences worth having:
 *
 * - The back button works the way people expect.
 * - A filtered view can be linked to — which is what lets the dashboard's KPI cards and the
 *   recall overview legend jump straight to "show me the overdue ones".
 * - Reloading the page keeps you where you were.
 * - The Playwright suite can navigate to a filtered state directly rather than clicking its way
 *   there.
 *
 * Search is debounced, because a keystroke is not a query: typing "johnson" unthrottled is seven
 * round trips, six of which are thrown away before anyone reads them.
 */

import { Search, X } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import * as React from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/primitives";
import { PATIENT_STATUSES, STATUS_DESCRIPTIONS, STATUS_LABELS } from "@/lib/types";
import { cn } from "@/lib/utils";

/** Long enough to skip the letters in the middle of a word, short enough to feel immediate. */
const SEARCH_DEBOUNCE_MS = 300;

export function PatientFilters({ total }: { total: number }) {
  const router = useRouter();
  const searchParams = useSearchParams();

  const activeStatuses = searchParams.getAll("status");
  const currentSearch = searchParams.get("search") ?? "";

  const [searchText, setSearchText] = React.useState(currentSearch);

  // Keep the input in step when the URL changes from elsewhere — a dashboard link, or the back
  // button. Without this, navigating to a filtered view leaves stale text in the box.
  React.useEffect(() => {
    setSearchText(currentSearch);
  }, [currentSearch]);

  const updateQuery = React.useCallback(
    (mutate: (params: URLSearchParams) => void) => {
      const params = new URLSearchParams(searchParams.toString());
      mutate(params);
      // Any filter change returns to page 1. Staying on page 4 of a result set that now has two
      // pages shows an empty table, which reads as "no matches" rather than "wrong page".
      params.delete("page");
      router.push(`/patients?${params.toString()}`);
    },
    [router, searchParams],
  );

  // Debounce the search box.
  React.useEffect(() => {
    if (searchText === currentSearch) return;

    const timer = setTimeout(() => {
      updateQuery((params) => {
        if (searchText) params.set("search", searchText);
        else params.delete("search");
      });
    }, SEARCH_DEBOUNCE_MS);

    return () => clearTimeout(timer);
  }, [searchText, currentSearch, updateQuery]);

  function toggleStatus(status: string) {
    updateQuery((params) => {
      const current = params.getAll("status");
      params.delete("status");
      const next = current.includes(status)
        ? current.filter((value) => value !== status)
        : [...current, status];
      for (const value of next) params.append("status", value);
    });
  }

  const hasFilters = activeStatuses.length > 0 || currentSearch.length > 0;

  return (
    <div className="mb-4 space-y-3">
      <div className="flex flex-wrap items-center gap-3">
        <div className="relative min-w-56 flex-1">
          <Search
            className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-ink-subtle"
            aria-hidden="true"
          />
          <label htmlFor="patient-search" className="sr-only">
            Search patients by name or email
          </label>
          <Input
            id="patient-search"
            type="search"
            placeholder="Search by name or email…"
            value={searchText}
            onChange={(event) => setSearchText(event.target.value)}
            className="pl-9"
          />
        </div>

        {/* aria-live so the result count is announced when filtering changes it, rather than
            only being visible (SPEC §10). */}
        <p className="text-sm tabular-nums text-ink-muted" aria-live="polite">
          {total} patient{total === 1 ? "" : "s"}
        </p>

        {hasFilters ? (
          <Button
            variant="ghost"
            size="sm"
            onClick={() => router.push("/patients")}
            className="text-ink-muted"
          >
            <X aria-hidden="true" />
            Clear filters
          </Button>
        ) : null}
      </div>

      {/* Filter chips. Real buttons with aria-pressed, so the selected state is announced as
          well as shown — a highlighted chip means nothing to a screen reader on its own. */}
      <div className="flex flex-wrap gap-1.5">
        {PATIENT_STATUSES.map((status) => {
          const active = activeStatuses.includes(status);
          return (
            <button
              key={status}
              type="button"
              onClick={() => toggleStatus(status)}
              aria-pressed={active}
              title={STATUS_DESCRIPTIONS[status]}
              className={cn(
                "rounded-pill border px-3 py-1 text-xs font-medium transition-colors",
                "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand",
                active
                  ? "border-brand bg-brand-subtle text-brand"
                  : "border-border bg-surface text-ink-muted hover:border-border-strong hover:text-ink",
              )}
            >
              {STATUS_LABELS[status]}
            </button>
          );
        })}
      </div>
    </div>
  );
}
