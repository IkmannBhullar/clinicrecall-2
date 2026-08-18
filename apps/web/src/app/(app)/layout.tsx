/**
 * Layout for every signed-in page.
 *
 * A server component, so the session is resolved before anything renders. That avoids the flash
 * of an empty shell that a client-side auth check produces — the user's name and practice are in
 * the first HTML the browser receives.
 *
 * The `(app)` route group means these pages share this chrome without `/app` appearing in any
 * URL: the dashboard is at `/dashboard`, not `/app/dashboard`.
 */

import { redirect } from "next/navigation";

import { AppShell } from "@/components/shell/app-shell";
import { apiFetch, ApiError } from "@/lib/api";
import { getAccessToken } from "@/lib/supabase/server";
import type { Session } from "@/lib/types";

export default async function AuthenticatedLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const accessToken = await getAccessToken();

  // The middleware should already have redirected, but a layout that trusts that would render a
  // broken shell in the window where a session expires between the two.
  if (!accessToken) {
    redirect("/sign-in");
  }

  let session: Session;
  try {
    session = await apiFetch<Session>("/me", { accessToken });
  } catch (error) {
    // A rejected token means the session is genuinely gone — sign in again.
    if (error instanceof ApiError && error.isAuthError) {
      redirect("/sign-in");
    }
    // Anything else is the API being unreachable or broken, which is not the user's problem to
    // solve by signing in again. Let it surface as an error page that says so.
    throw error;
  }

  return <AppShell session={session}>{children}</AppShell>;
}
