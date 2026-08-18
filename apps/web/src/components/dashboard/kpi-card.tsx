/**
 * A KPI card (SPEC §8).
 *
 * Five of these across the top of the dashboard. The number is the point, so it is the largest
 * thing in the card and everything else is subordinate to it.
 *
 * `tabular-nums` matters more than it sounds: without it, proportional digits make the five
 * cards' figures sit at visibly different widths, and a row of numbers that does not line up
 * reads as sloppy on the screen someone sees first.
 */

import Link from "next/link";
import type { LucideIcon } from "lucide-react";

import { Card } from "@/components/ui/primitives";
import { cn } from "@/lib/utils";

type KpiCardProps = {
  label: string;
  value: string | number;
  icon: LucideIcon;
  /** A single line of context under the number. */
  hint?: string;
  /** Shown on hover and to screen readers. Used for the revenue definition (SPEC §8). */
  tooltip?: string;
  /** Makes the whole card a link — used for the cards that map to a filtered patient list. */
  href?: string;
  /** Draws attention to a number that means work is outstanding. */
  emphasis?: "default" | "warning" | "positive";
};

const EMPHASIS_STYLES = {
  default: "text-ink",
  warning: "text-status-overdue",
  positive: "text-status-active",
} as const;

export function KpiCard({
  label,
  value,
  icon: Icon,
  hint,
  tooltip,
  href,
  emphasis = "default",
}: KpiCardProps) {
  const content = (
    <>
      <div className="flex items-start justify-between gap-2">
        <p className="text-sm font-medium text-ink-muted">{label}</p>
        <Icon className="size-4 shrink-0 text-ink-subtle" aria-hidden="true" />
      </div>
      <p className={cn("mt-2 text-2xl font-semibold tabular-nums", EMPHASIS_STYLES[emphasis])}>
        {value}
      </p>
      {hint ? <p className="mt-1 text-xs text-ink-subtle">{hint}</p> : null}
    </>
  );

  // `title` gives the hover explanation; the sr-only paragraph gives the same text to a screen
  // reader, which never receives a title attribute reliably.
  const shared = {
    title: tooltip,
    className: cn(
      "block p-5 transition-colors",
      href && "hover:border-border-strong focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand",
    ),
  };

  if (href) {
    return (
      <Card className="overflow-hidden">
        <Link href={href} {...shared}>
          {content}
          {tooltip ? <span className="sr-only">{tooltip}</span> : null}
        </Link>
      </Card>
    );
  }

  return (
    <Card {...shared}>
      {content}
      {tooltip ? <span className="sr-only">{tooltip}</span> : null}
    </Card>
  );
}
