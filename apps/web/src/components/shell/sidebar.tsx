"use client";

/**
 * The sidebar navigation.
 *
 * A client component because it needs `usePathname` to mark the current section. Everything else
 * in the shell stays on the server.
 *
 * Accessibility (SPEC §10):
 * - A real `<nav>` with an accessible name, so screen-reader users can jump straight to it.
 * - `aria-current="page"` on the active link — the highlight is visual, this is the announced
 *   equivalent, and neither substitutes for the other.
 * - Ordinary links, so the browser's own affordances (open in new tab, back button) all work.
 */

import Link from "next/link";
import { usePathname } from "next/navigation";

import { cn } from "@/lib/utils";
import { NAVIGATION, isActivePath } from "@/components/shell/navigation";

export function SidebarNavigation({ onNavigate }: { onNavigate?: () => void }) {
  const pathname = usePathname();

  return (
    <nav aria-label="Main" className="flex flex-col gap-0.5">
      {NAVIGATION.map((item) => {
        const active = isActivePath(pathname, item.href);
        const Icon = item.icon;

        return (
          <Link
            key={item.href}
            href={item.href}
            // Closes the mobile drawer after a tap. Undefined on desktop, where there is nothing
            // to close.
            onClick={onNavigate}
            aria-current={active ? "page" : undefined}
            className={cn(
              "flex items-center gap-3 rounded-control px-3 py-2 text-sm font-medium transition-colors",
              active
                ? "bg-brand-subtle text-brand"
                : "text-ink-muted hover:bg-canvas hover:text-ink",
            )}
          >
            <Icon className="size-4 shrink-0" aria-hidden="true" />
            {item.label}
          </Link>
        );
      })}
    </nav>
  );
}
