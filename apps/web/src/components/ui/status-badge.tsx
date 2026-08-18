/**
 * The patient status badge.
 *
 * The most-read component in the product: an office manager scans this column faster than they
 * read the names beside it, so it has to be legible at a glance and unambiguous up close.
 *
 * **Colour is never the only signal** (SPEC §10). Every badge carries its label in words, so it
 * works for someone with colour-vision deficiency, in greyscale, and on a projector that has
 * flattened the palette. The colours make the column *scannable*; the words make it *readable*.
 */

import { cn } from "@/lib/utils";
import { STATUS_DESCRIPTIONS, STATUS_LABELS, type PatientStatus } from "@/lib/types";

/**
 * Colour pairs per status, from the design tokens in globals.css.
 *
 * Ordered here the way the recall cycle runs rather than alphabetically, so the palette can be
 * read as a progression: overdue is the most urgent, inactive the least.
 */
const STATUS_STYLES: Record<PatientStatus, string> = {
  OVERDUE: "bg-status-overdue-bg text-status-overdue",
  DUE: "bg-status-due-bg text-status-due",
  DUE_SOON: "bg-status-due-soon-bg text-status-due-soon",
  SCHEDULED: "bg-status-scheduled-bg text-status-scheduled",
  ACTIVE: "bg-status-active-bg text-status-active",
  COMPLETED: "bg-status-completed-bg text-status-completed",
  INACTIVE: "bg-status-inactive-bg text-status-inactive",
};

type StatusBadgeProps = {
  status: PatientStatus;
  className?: string;
  /** Show the plain-English definition on hover. Staff should not have to guess. */
  withTooltip?: boolean;
};

export function StatusBadge({ status, className, withTooltip = true }: StatusBadgeProps) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-pill px-2.5 py-0.5 text-xs font-medium",
        STATUS_STYLES[status],
        className,
      )}
      // `title` rather than a custom tooltip: it is native, keyboard accessible, works before
      // hydration, and needs no library. A richer tooltip would be more machinery for the same
      // sentence.
      title={withTooltip ? STATUS_DESCRIPTIONS[status] : undefined}
    >
      {STATUS_LABELS[status]}
    </span>
  );
}
