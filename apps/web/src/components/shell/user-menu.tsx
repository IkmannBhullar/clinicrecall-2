"use client";

/**
 * Who is signed in, and how to sign out.
 *
 * Deliberately a plain disclosure rather than a dropdown library. There are two items in it, and
 * a menu primitive would be several kilobytes of JavaScript and a focus-management contract for
 * something a `<details>`-shaped component handles correctly on its own.
 *
 * Signing out clears the Supabase session and does a full navigation rather than a client-side
 * route change — `router.push` would leave the previous user's fetched data in React's cache,
 * and the next person to sign in on that machine would see a flash of it.
 */

import { ChevronDown, LogOut } from "lucide-react";
import * as React from "react";

import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/primitives";
import { createClient } from "@/lib/supabase/client";
import { cn } from "@/lib/utils";
import type { CurrentUser } from "@/lib/types";

export function UserMenu({ user }: { user: CurrentUser }) {
  const [open, setOpen] = React.useState(false);
  const [signingOut, setSigningOut] = React.useState(false);
  const containerRef = React.useRef<HTMLDivElement>(null);

  // Close on an outside click or on Escape — the two things anyone expects from an open menu.
  React.useEffect(() => {
    if (!open) return;

    function handlePointerDown(event: MouseEvent) {
      if (!containerRef.current?.contains(event.target as Node)) setOpen(false);
    }
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") setOpen(false);
    }

    document.addEventListener("mousedown", handlePointerDown);
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("mousedown", handlePointerDown);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [open]);

  async function handleSignOut() {
    setSigningOut(true);
    await createClient().auth.signOut();
    // Full reload, not a client-side push. See the note at the top of this file.
    window.location.href = "/sign-in";
  }

  const initials = `${user.first_name[0] ?? ""}${user.last_name[0] ?? ""}`.toUpperCase();

  return (
    <div ref={containerRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        aria-haspopup="menu"
        className={cn(
          "flex w-full items-center gap-2.5 rounded-control px-2 py-1.5 text-left",
          "transition-colors hover:bg-canvas",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand",
        )}
      >
        <span
          className="flex size-8 shrink-0 items-center justify-center rounded-pill bg-brand-subtle text-xs font-semibold text-brand"
          aria-hidden="true"
        >
          {initials}
        </span>
        <span className="min-w-0 flex-1">
          <span className="block truncate text-sm font-medium text-ink">
            {user.first_name} {user.last_name}
          </span>
          <span className="block text-xs text-ink-subtle">
            {user.role === "ADMIN" ? "Administrator" : "Staff"}
          </span>
        </span>
        <ChevronDown
          className={cn("size-4 shrink-0 text-ink-subtle transition-transform", open && "rotate-180")}
          aria-hidden="true"
        />
      </button>

      {open ? (
        <div
          role="menu"
          className="absolute bottom-full left-0 z-20 mb-1 w-full overflow-hidden rounded-card border border-border bg-surface shadow-panel"
        >
          <div className="border-b border-border px-3 py-2">
            <p className="truncate text-xs text-ink-subtle">{user.email}</p>
          </div>
          <Button
            variant="ghost"
            size="sm"
            role="menuitem"
            onClick={handleSignOut}
            disabled={signingOut}
            className="w-full justify-start rounded-none px-3"
          >
            {signingOut ? <Spinner /> : <LogOut aria-hidden="true" />}
            {signingOut ? "Signing out…" : "Sign out"}
          </Button>
        </div>
      ) : null}
    </div>
  );
}
