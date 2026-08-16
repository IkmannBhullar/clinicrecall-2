/**
 * Small shared helpers used across the UI.
 */

import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

/**
 * Merge Tailwind class names, resolving conflicts in favour of the last one given.
 *
 * This is the standard shadcn/ui helper, and it solves a specific annoyance: writing
 * `className="px-4"` on a component that already sets `px-2` would otherwise produce
 * `"px-2 px-4"`, where which one wins depends on stylesheet order rather than on what you
 * wrote. `cn()` makes the later class win, every time.
 *
 * @example
 *   cn("px-2 text-ink", isActive && "px-4")  // -> "text-ink px-4"
 */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}
