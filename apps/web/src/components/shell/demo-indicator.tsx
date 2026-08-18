/**
 * The persistent "Demo Data — synthetic patients only" indicator.
 *
 * SPEC constraint D6 requires this to be visible in the application chrome at all times, and the
 * reason is not decorative. This product looks like a system holding real patient records. It is
 * shown to clinic owners, screenshotted, and projected in meetings — and there must never be a
 * moment where someone reasonably believes those are their patients' names.
 *
 * It is deliberately not dismissible. A banner that can be closed is a banner that is absent from
 * every screenshot taken after the first click.
 */

import { FlaskConical } from "lucide-react";

import { cn } from "@/lib/utils";

export function DemoIndicator({ className }: { className?: string }) {
  return (
    <div
      className={cn(
        "flex items-center gap-2 rounded-pill bg-warning-bg px-3 py-1",
        "text-xs font-medium text-warning",
        className,
      )}
    >
      <FlaskConical className="size-3.5 shrink-0" aria-hidden="true" />
      <span>
        Demo Data
        {/* The em dash and the qualifier are hidden on narrow screens, where the badge would
            otherwise wrap and push the header around. "Demo Data" alone still carries the
            warning; the full sentence stays available to screen readers below. */}
        <span className="hidden sm:inline"> — synthetic patients only</span>
      </span>
      <span className="sr-only">
        This application contains synthetic demonstration data. No real patient records are
        present.
      </span>
    </div>
  );
}
