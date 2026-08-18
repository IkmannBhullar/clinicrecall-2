/**
 * Formatting dates, money, and relative time for display.
 *
 * Centralised so the whole application phrases things the same way. A dashboard that says
 * "24 days overdue" beside a table that says "2026-07-24" reads as two products stitched
 * together.
 *
 * **Dates are treated as calendar dates, not instants.** A due date is a fact about a clinic's
 * day (SPEC §5.3), so `2026-07-24` must render as 24 July regardless of the reader's timezone.
 * Passing that string to `new Date()` parses it as midnight UTC, which renders as the 23rd for
 * anyone west of Greenwich — an off-by-one that would make every date in the product wrong for
 * half the world.
 */

const DATE_FORMAT = new Intl.DateTimeFormat("en-GB", {
  day: "numeric",
  month: "short",
  year: "numeric",
});

const CURRENCY_FORMAT = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  maximumFractionDigits: 0,
});

/** Parse a `YYYY-MM-DD` string as a local calendar date, avoiding the UTC shift described above. */
export function parseCalendarDate(value: string): Date {
  const [year, month, day] = value.split("-").map(Number);
  return new Date(year ?? 1970, (month ?? 1) - 1, day ?? 1);
}

/** `2026-07-24` → `24 Jul 2026`. */
export function formatDate(value: string | null | undefined): string {
  if (!value) return "—";
  return DATE_FORMAT.format(parseCalendarDate(value));
}

/** A timestamp → `24 Jul 2026`. */
export function formatTimestamp(value: string | null | undefined): string {
  if (!value) return "—";
  return DATE_FORMAT.format(new Date(value));
}

/** `1000` → `$1,000`. Whole dollars: cents on an estimate imply a precision it does not have. */
export function formatCurrency(value: number | string): string {
  return CURRENCY_FORMAT.format(typeof value === "string" ? Number(value) : value);
}

/**
 * How a due date reads relative to the practice's today.
 *
 * `today` is passed in rather than read from the browser clock, so the phrasing agrees with the
 * status badge next to it. A receptionist in New York looking at a California practice's data
 * must not see "due today" beside a badge that says overdue.
 */
export function formatDueDate(dueDate: string, today: string): string {
  const due = parseCalendarDate(dueDate);
  const now = parseCalendarDate(today);
  const days = Math.round((due.getTime() - now.getTime()) / 86_400_000);

  if (days === 0) return "Due today";
  if (days === 1) return "Due tomorrow";
  if (days === -1) return "1 day overdue";
  if (days < 0) return `${Math.abs(days)} days overdue`;
  if (days <= 30) return `Due in ${days} days`;

  return `Due ${formatDate(dueDate)}`;
}

/** A timestamp → "3 days ago". Falls back to a date once it is too old to be useful. */
export function formatRelative(value: string | null | undefined): string {
  if (!value) return "Never";

  const then = new Date(value).getTime();
  const days = Math.round((Date.now() - then) / 86_400_000);

  if (days <= 0) return "Today";
  if (days === 1) return "Yesterday";
  if (days < 30) return `${days} days ago`;

  return formatTimestamp(value);
}

/** Two-letter initials, matching the convention used in the activity feed (SPEC §8). */
export function initials(firstName: string, lastName: string): string {
  return `${firstName[0] ?? ""}${lastName[0] ?? ""}`.toUpperCase();
}
