"use client";

/**
 * Calling the API from a client component.
 *
 * Server components get their token from cookies via `getAccessToken()`. Client components need
 * it from the browser's Supabase session, which is what this hook provides — wrapped so no
 * component has to remember to attach the header.
 *
 * `useApi` returns a stable object, so it is safe in a `useEffect` dependency list without
 * causing a re-fetch on every render.
 */

import * as React from "react";

import { apiFetch, apiUpload, ApiError } from "@/lib/api";
import { createClient } from "@/lib/supabase/client";

export type ApiClient = {
  get: <T>(path: string) => Promise<T>;
  post: <T>(path: string, body?: unknown) => Promise<T>;
  upload: <T>(path: string, file: File) => Promise<T>;
};

export function useApi(): ApiClient {
  return React.useMemo(() => {
    async function token(): Promise<string | null> {
      // Read the session per call rather than caching it in state. Supabase refreshes the access
      // token in the background, and a token captured once would go stale after an hour — the
      // user would see every request start failing while appearing perfectly signed in.
      const {
        data: { session },
      } = await createClient().auth.getSession();
      return session?.access_token ?? null;
    }

    return {
      get: async <T,>(path: string) => apiFetch<T>(path, { accessToken: await token() }),
      post: async <T,>(path: string, body?: unknown) =>
        apiFetch<T>(path, { method: "POST", body, accessToken: await token() }),
      upload: async <T,>(path: string, file: File) =>
        apiUpload<T>(path, file, { accessToken: await token() }),
    };
  }, []);
}

/**
 * Turn any thrown value into a message worth showing.
 *
 * The API's own message is already written for a receptionist (SPEC §9), so it is used as-is.
 * Anything else means the request never reached the server — a different problem, and one the
 * user can act on differently.
 */
export function toDisplayMessage(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  if (error instanceof Error && error.message.toLowerCase().includes("fetch")) {
    return "Could not reach the server. Check that it is running and try again.";
  }
  return "Something went wrong. Please try again.";
}
