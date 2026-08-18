/**
 * Supabase client for the browser.
 *
 * Uses the **anon key only**. That key is public by design — it identifies the project and grants
 * no privileges on its own; every actual permission comes from the signed-in user's token.
 *
 * The service-role key must never appear anywhere in this directory (SPEC §3.2). Three separate
 * mechanisms enforce that, described in `docs/SECURITY.md`: the generated env file, an ESLint
 * rule, and a grep over the compiled bundle in `make verify`.
 */

import { createBrowserClient } from "@supabase/ssr";

/**
 * Create a Supabase client for use in a client component.
 *
 * Safe to call repeatedly — `createBrowserClient` returns the same underlying instance for a
 * given set of arguments, so components need not thread a single client through props.
 */
export function createClient() {
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const anonKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;

  if (!url || !anonKey) {
    // A clear failure beats a confusing one: without this the Supabase client constructs
    // successfully and every auth call fails later with an opaque network error.
    throw new Error(
      "NEXT_PUBLIC_SUPABASE_URL and NEXT_PUBLIC_SUPABASE_ANON_KEY are not set. " +
        "Run `make supabase-start`, which writes apps/web/.env.local.",
    );
  }

  return createBrowserClient(url, anonKey);
}
