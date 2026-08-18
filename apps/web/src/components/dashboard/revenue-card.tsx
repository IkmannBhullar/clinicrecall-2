/**
 * Estimated Revenue Recovered (SPEC §8).
 *
 * The number that decides whether the product is worth paying for, and therefore the number an
 * office manager will poke at. SPEC §8 says so outright: "Revenue formula must be defined and
 * shown on hover, or an office manager will poke it."
 *
 * So the definition is not hidden behind a tooltip alone. It is:
 *
 * * shown on hover, via a details disclosure that also works on touch, where hover does not exist;
 * * available to screen readers as ordinary text rather than a title attribute;
 * * fetched from the API alongside the value, so the description cannot drift from the
 *   calculation that produced it;
 * * accompanied by the arithmetic — recovered × value per visit — so it can be checked, not just
 *   read.
 *
 * The word "Estimated" is in the label, not the fine print. Correlation is not proof, and
 * overclaiming here is the fastest way to lose a room.
 */

import { TrendingUp } from "lucide-react";

import { Card } from "@/components/ui/primitives";
import { formatCurrency } from "@/lib/format";

type RevenueCardProps = {
  appointmentsRecovered: number;
  estimatedValue: string | number;
  valuePerVisit: string | number;
  definition: string;
};

export function RevenueCard({
  appointmentsRecovered,
  estimatedValue,
  valuePerVisit,
  definition,
}: RevenueCardProps) {
  return (
    <Card className="p-5">
      <div className="flex items-start justify-between gap-2">
        <p className="text-sm font-medium text-ink-muted">Estimated revenue recovered</p>
        <TrendingUp className="size-4 shrink-0 text-ink-subtle" aria-hidden="true" />
      </div>

      <p className="mt-2 text-2xl font-semibold tabular-nums text-status-active">
        {formatCurrency(estimatedValue)}
      </p>

      <p className="mt-1 text-xs text-ink-subtle">
        {appointmentsRecovered} appointment{appointmentsRecovered === 1 ? "" : "s"} recovered
      </p>

      {/*
        A <details> rather than a hover-only tooltip.
        Hover does not exist on a phone or a tablet, and this is precisely the explanation
        someone reaches for when they are least able to hover — mid-conversation, being asked
        where the number came from. A disclosure works with a pointer, a finger, and a keyboard.
      */}
      <details className="group mt-3">
        <summary className="cursor-pointer list-none text-xs font-medium text-brand hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand">
          How is this calculated?
        </summary>
        <div className="mt-2 space-y-2 rounded-control bg-canvas p-3">
          <p className="text-xs leading-relaxed text-ink-muted">{definition}</p>
          <p className="text-xs tabular-nums text-ink-subtle">
            {appointmentsRecovered} recovered × {formatCurrency(valuePerVisit)} per visit ={" "}
            <span className="font-medium text-ink">{formatCurrency(estimatedValue)}</span>
          </p>
        </div>
      </details>
    </Card>
  );
}
