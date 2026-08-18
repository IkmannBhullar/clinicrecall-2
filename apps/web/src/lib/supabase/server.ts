/**
 * Supabase client for server components and route handlers.
 *
 * The session lives in cookies rather than in `localStorage`, which is what lets a server
 * component know who is signed in without a round trip to the browser. `@supabase/ssr` handles
 * reading and refreshing it; this module just wires it to Next.js's cookie store.
 *
 * Anon key only here too — a server component runs on our machine, but the data it renders is
 * still scoped by the user's own token, exactly as it would be in the browser.
 */

import { createServerClient } from "@supabase/ssr";
import { cookies } from "next/headers";

/** What `@supabase/ssr` hands to `setAll`. The package does not export this shape. */
type CookieToSet = {
  name: string;
  value: string;
  options?: Record<string, unknown>;
};

/**
 * Create a Supabase client bound to the current request's cookies.
 *
 * Must be awaited: `cookies()` is asynchronous in Next.js 15.
 */
export async function createClient() {
  const cookieStore = await cookies();

  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const anonKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;

  if (!url || !anonKey) {
    throw new Error(
      "NEXT_PUBLIC_SUPABASE_URL and NEXT_PUBLIC_SUPABASE_ANON_KEY are not set. " +
        "Run `make supabase-start`, which writes apps/web/.env.local.",
    );
  }

  return createServerClient(url, anonKey, {
    cookies: {
      getAll() {
        return cookieStore.getAll();
      },
      setAll(cookiesToSet: CookieToSet[]) {
        try {
          for (const { name, value, options } of cookiesToSet) {
            cookieStore.set(name, value, options as Parameters<typeof cookieStore.set>[2]);
          }
        } catch {
          // Server *components* cannot set cookies — only route handlers and server actions can.
          // Supabase calls setAll when it refreshes an expiring token, so this fires routinely
          // and is not an error: the middleware refreshes the session on every request, so the
          // cookie is already up to date by the time a component renders.
        }
      },
    },
  });
}

/**
 * The access token for the current request, or null when signed out.
 *
 * This is the token the FastAPI backend verifies against Supabase's JWKS. Fetching data
 * server-side means passing it explicitly, since a server component has no `Authorization`
 * header of its own to forward.
 */
export async function getAccessToken(): Promise<string | null> {
  const supabase = await createClient();
  const {
    data: { session },
  } = await supabase.auth.getSession();

  return session?.access_token ?? null;
}
