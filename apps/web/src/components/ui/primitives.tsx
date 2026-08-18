/**
 * The small shared building blocks: card, input, label, skeleton, empty state, spinner.
 *
 * Grouped in one file because each is a handful of lines and a separate module per component
 * would be more navigation than code. Anything that grows past that gets its own file.
 */

import * as React from "react";

import { cn } from "@/lib/utils";

// ---------------------------------------------------------------------------------------------
// Card
// ---------------------------------------------------------------------------------------------

export function Card({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn(
        "rounded-card border border-border bg-surface shadow-card",
        className,
      )}
      {...props}
    />
  );
}

export function CardHeader({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("px-5 pt-5 pb-3", className)} {...props} />;
}

export function CardTitle({ className, ...props }: React.HTMLAttributes<HTMLHeadingElement>) {
  return (
    <h2 className={cn("text-sm font-semibold text-ink", className)} {...props} />
  );
}

export function CardDescription({ className, ...props }: React.HTMLAttributes<HTMLParagraphElement>) {
  return <p className={cn("mt-1 text-sm text-ink-muted", className)} {...props} />;
}

export function CardContent({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("px-5 pb-5", className)} {...props} />;
}

// ---------------------------------------------------------------------------------------------
// Form controls
// ---------------------------------------------------------------------------------------------

export const Input = React.forwardRef<HTMLInputElement, React.InputHTMLAttributes<HTMLInputElement>>(
  ({ className, ...props }, ref) => (
    <input
      ref={ref}
      className={cn(
        "flex h-9 w-full rounded-control border border-border-strong bg-surface px-3 py-1",
        "text-sm text-ink shadow-card transition-colors",
        "placeholder:text-ink-subtle",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand",
        "focus-visible:ring-offset-1 disabled:cursor-not-allowed disabled:opacity-60",
        // aria-invalid rather than a prop: the attribute is what a screen reader announces, so
        // driving the colour from it keeps the two from disagreeing.
        "aria-[invalid=true]:border-danger aria-[invalid=true]:ring-danger",
        className,
      )}
      {...props}
    />
  ),
);
Input.displayName = "Input";

export function Label({
  className,
  ...props
}: React.LabelHTMLAttributes<HTMLLabelElement>) {
  return (
    <label
      className={cn("block text-sm font-medium text-ink", className)}
      {...props}
    />
  );
}

/**
 * A form field error, wired for screen readers.
 *
 * `role="alert"` announces it the moment it appears. The caller points the input's
 * `aria-describedby` at this element's id, which is what SPEC §10 asks for — otherwise a screen
 * reader user hears "Email, invalid" with no idea what is wrong.
 */
export function FieldError({
  id,
  children,
  className,
}: {
  id: string;
  children: React.ReactNode;
  className?: string;
}) {
  if (!children) return null;

  return (
    <p id={id} role="alert" className={cn("mt-1.5 text-sm text-danger", className)}>
      {children}
    </p>
  );
}

// ---------------------------------------------------------------------------------------------
// Loading
// ---------------------------------------------------------------------------------------------

/**
 * A skeleton placeholder (SPEC §10).
 *
 * Preferred over a spinner for content whose shape is known: the page does not jump when the
 * data arrives, which is what makes an interface feel settled rather than twitchy.
 *
 * The pulse honours `prefers-reduced-motion` via the global rule in globals.css.
 */
export function Skeleton({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn("animate-pulse rounded-control bg-border", className)}
      // Hidden from assistive technology: announcing "loading" for each of twelve placeholder
      // bars is noise. The container that owns them carries aria-busy instead.
      aria-hidden="true"
      {...props}
    />
  );
}

export function Spinner({ className }: { className?: string }) {
  return (
    <svg
      className={cn("size-4 animate-spin", className)}
      viewBox="0 0 24 24"
      fill="none"
      aria-hidden="true"
    >
      <circle cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="3" opacity="0.25" />
      <path
        d="M12 2a10 10 0 0 1 10 10"
        stroke="currentColor"
        strokeWidth="3"
        strokeLinecap="round"
      />
    </svg>
  );
}

// ---------------------------------------------------------------------------------------------
// Empty state
// ---------------------------------------------------------------------------------------------

/**
 * What a screen shows when it has nothing to show (SPEC §10 asks for "excellent empty states").
 *
 * An empty table with no explanation reads as a broken product. Every empty state here says what
 * would normally be here, why it is not, and — where there is one — offers the action that would
 * fill it.
 */
export function EmptyState({
  icon,
  title,
  description,
  action,
  className,
}: {
  icon?: React.ReactNode;
  title: string;
  description?: string;
  action?: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("flex flex-col items-center px-6 py-12 text-center", className)}>
      {icon ? <div className="mb-3 text-ink-subtle">{icon}</div> : null}
      <p className="text-sm font-medium text-ink">{title}</p>
      {description ? (
        <p className="mt-1 max-w-sm text-sm text-ink-muted">{description}</p>
      ) : null}
      {action ? <div className="mt-4">{action}</div> : null}
    </div>
  );
}

// ---------------------------------------------------------------------------------------------
// Layout helpers
// ---------------------------------------------------------------------------------------------

export function PageHeader({
  title,
  description,
  action,
}: {
  title: string;
  description?: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="mb-6 flex flex-wrap items-start justify-between gap-4">
      <div>
        <h1 className="text-xl font-semibold tracking-tight text-ink">{title}</h1>
        {description ? <p className="mt-1 text-sm text-ink-muted">{description}</p> : null}
      </div>
      {action ? <div className="shrink-0">{action}</div> : null}
    </div>
  );
}

export function Separator({ className }: { className?: string }) {
  return <hr className={cn("border-0 border-t border-border", className)} role="presentation" />;
}
