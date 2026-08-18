/**
 * Button.
 *
 * A shadcn/ui-style component: source in the repository rather than a dependency, so it can be
 * adjusted to the design system instead of fought with.
 *
 * Accessibility notes that are easy to lose in a refactor (SPEC §10):
 * - Renders a real `<button>`, so keyboard activation and screen-reader semantics come free.
 * - The focus ring is visible and high contrast, and never removed.
 * - Disabled buttons keep their text legible; `opacity-50` on a mid-grey would fail contrast.
 */

import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import * as React from "react";

import { cn } from "@/lib/utils";

const buttonVariants = cva(
  // Shared by every variant. `whitespace-nowrap` stops a two-word label wrapping into a
  // two-line button, which looks like a rendering bug.
  "inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-control text-sm " +
    "font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 " +
    "focus-visible:ring-brand focus-visible:ring-offset-2 disabled:pointer-events-none " +
    "disabled:opacity-60 [&_svg]:size-4 [&_svg]:shrink-0",
  {
    variants: {
      variant: {
        primary: "bg-brand text-white hover:bg-brand-hover shadow-card",
        secondary: "bg-surface text-ink border border-border-strong hover:bg-canvas shadow-card",
        ghost: "text-ink-muted hover:bg-canvas hover:text-ink",
        // Destructive actions are visually distinct so they are not clicked by muscle memory.
        danger: "bg-danger text-white hover:opacity-90 shadow-card",
        link: "text-brand underline-offset-4 hover:underline",
      },
      size: {
        sm: "h-8 px-3",
        md: "h-9 px-4",
        lg: "h-10 px-5",
        // Square, for icon-only buttons. Those always need an aria-label or an sr-only span —
        // an icon alone is invisible to a screen reader.
        icon: "size-9",
      },
    },
    defaultVariants: { variant: "primary", size: "md" },
  },
);

export type ButtonProps = React.ButtonHTMLAttributes<HTMLButtonElement> &
  VariantProps<typeof buttonVariants> & {
    /** Render as the child element instead of a `<button>` — for links that look like buttons. */
    asChild?: boolean;
  };

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, asChild = false, ...props }, ref) => {
    const Component = asChild ? Slot : "button";

    return (
      <Component
        ref={ref}
        className={cn(buttonVariants({ variant, size }), className)}
        {...props}
      />
    );
  },
);

Button.displayName = "Button";

export { buttonVariants };
