"use client";

/**
 * Pagination for the patients list.
 *
 * Server-side (SPEC §8): the page number is in the URL and the server returns that page. With 55
 * demo patients client-side slicing would work equally well; with a real practice's 40,000 it
 * would mean a multi-megabyte response before the first row appears.
 *
 * Deliberately just previous/next plus a position indicator, rather than numbered page links.
 * Nobody navigates a patient list by jumping to page 7 — they search or filter. Numbered pages
 * would be more controls for a journey nobody takes.
 */

import { ChevronLeft, ChevronRight } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";

import { Button } from "@/components/ui/button";

export function Pagination({
  page,
  totalPages,
  total,
  pageSize,
}: {
  page: number;
  totalPages: number;
  total: number;
  pageSize: number;
}) {
  const router = useRouter();
  const searchParams = useSearchParams();

  if (totalPages <= 1) return null;

  function goTo(nextPage: number) {
    const params = new URLSearchParams(searchParams.toString());
    params.set("page", String(nextPage));
    router.push(`/patients?${params.toString()}`);
  }

  const first = (page - 1) * pageSize + 1;
  const last = Math.min(page * pageSize, total);

  return (
    <nav
      aria-label="Patient list pages"
      className="mt-4 flex items-center justify-between gap-4"
    >
      {/* "Showing 26 to 50 of 55" — a position, not just a page number. Someone working through
          a list wants to know how much is left. */}
      <p className="text-sm tabular-nums text-ink-muted">
        Showing {first}&ndash;{last} of {total}
      </p>

      <div className="flex items-center gap-2">
        <Button
          variant="secondary"
          size="sm"
          onClick={() => goTo(page - 1)}
          disabled={page <= 1}
        >
          <ChevronLeft aria-hidden="true" />
          Previous
        </Button>

        <span className="text-sm tabular-nums text-ink-muted">
          Page {page} of {totalPages}
        </span>

        <Button
          variant="secondary"
          size="sm"
          onClick={() => goTo(page + 1)}
          disabled={page >= totalPages}
        >
          Next
          <ChevronRight aria-hidden="true" />
        </Button>
      </div>
    </nav>
  );
}
