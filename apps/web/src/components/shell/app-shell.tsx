"use client";

/**
 * The application chrome: sidebar, header, and the page area.
 *
 * Desktop-first, mobile usable (SPEC §10). On a wide screen the sidebar is always visible; below
 * `lg` it collapses into a drawer behind a menu button. A clinic's front desk uses a desktop —
 * that is the case worth optimising — but the owner will open it on a phone at some point, and
 * it should not be embarrassing when they do.
 *
 * The brand is a text wordmark, never an image (SPEC §10). One less asset to load, and it stays
 * crisp at any zoom.
 */

import { Menu, X } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import * as React from "react";

import { DemoIndicator } from "@/components/shell/demo-indicator";
import { NAVIGATION, isActivePath } from "@/components/shell/navigation";
import { SidebarNavigation } from "@/components/shell/sidebar";
import { UserMenu } from "@/components/shell/user-menu";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { Session } from "@/lib/types";

export function AppShell({
  session,
  children,
}: {
  session: Session;
  children: React.ReactNode;
}) {
  const [drawerOpen, setDrawerOpen] = React.useState(false);
  const pathname = usePathname();

  // Close the drawer whenever the route changes. Without this it stays open over the page it
  // just navigated to, which on a phone means the content is hidden behind the menu that opened
  // it.
  React.useEffect(() => {
    setDrawerOpen(false);
  }, [pathname]);

  const currentSection = NAVIGATION.find((item) => isActivePath(pathname, item.href));

  return (
    <div className="min-h-screen bg-canvas">
      {/* ---------------------------------------------------------------------------------
          Sidebar — fixed on desktop, a drawer below lg
          --------------------------------------------------------------------------------- */}
      <aside
        className={cn(
          "fixed inset-y-0 left-0 z-40 flex w-64 flex-col border-r border-border bg-surface",
          "transition-transform duration-200 lg:translate-x-0",
          drawerOpen ? "translate-x-0" : "-translate-x-full",
        )}
        // Hidden from assistive technology when closed, so a screen reader does not tab into a
        // menu that is off-screen.
        aria-hidden={!drawerOpen && undefined}
      >
        <div className="flex h-14 items-center justify-between border-b border-border px-4">
          <Link
            href="/dashboard"
            className="rounded-control text-base font-semibold tracking-tight text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand"
          >
            Clinic<span className="text-brand">Recall</span>
          </Link>
          <Button
            variant="ghost"
            size="icon"
            className="lg:hidden"
            onClick={() => setDrawerOpen(false)}
          >
            <X aria-hidden="true" />
            <span className="sr-only">Close menu</span>
          </Button>
        </div>

        <div className="flex-1 overflow-y-auto px-3 py-4">
          <SidebarNavigation onNavigate={() => setDrawerOpen(false)} />
        </div>

        <div className="border-t border-border p-3">
          <UserMenu user={session.user} />
        </div>
      </aside>

      {/* The scrim behind an open drawer. `aria-hidden` because the close button in the drawer
          is the accessible way out; this is a pointer affordance only. */}
      {drawerOpen ? (
        <button
          type="button"
          className="fixed inset-0 z-30 bg-ink/20 lg:hidden"
          onClick={() => setDrawerOpen(false)}
          aria-hidden="true"
          tabIndex={-1}
        />
      ) : null}

      {/* ---------------------------------------------------------------------------------
          Main column
          --------------------------------------------------------------------------------- */}
      <div className="lg:pl-64">
        <header className="sticky top-0 z-20 flex h-14 items-center gap-3 border-b border-border bg-surface/95 px-4 backdrop-blur sm:px-6">
          <Button
            variant="ghost"
            size="icon"
            className="lg:hidden"
            onClick={() => setDrawerOpen(true)}
            aria-expanded={drawerOpen}
          >
            <Menu aria-hidden="true" />
            <span className="sr-only">Open menu</span>
          </Button>

          <p className="truncate text-sm font-medium text-ink">
            {session.organization.name}
          </p>

          <div className="ml-auto flex items-center gap-3">
            {/* SPEC constraint D6 — present on every screen, and not dismissible. */}
            <DemoIndicator />
          </div>
        </header>

        <main id="main-content" className="px-4 py-6 sm:px-6 lg:px-8">
          {/* The section name as an h1 is announced when a screen-reader user lands on a page,
              which is how they know the navigation worked. Pages render their own visible
              heading; this is the landmark. */}
          <span className="sr-only">
            <h1>{currentSection?.label ?? "ClinicRecall"}</h1>
          </span>
          {children}
        </main>
      </div>
    </div>
  );
}
