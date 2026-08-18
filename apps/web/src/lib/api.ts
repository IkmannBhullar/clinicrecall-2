/**
 * Talking to the FastAPI backend.
 *
 * Every request carries the signed-in user's Supabase access token as a bearer credential. The
 * backend verifies it against Supabase's public keys and resolves the organization from it —
 * which is why nothing here ever sends an organization id. It would be ignored (SPEC §3.2), and
 * including it would suggest otherwise to whoever reads this next.
 *
 * Errors arrive in the envelope from SPEC §9, and `ApiError` preserves all three parts: the
 * stable `code` for branching, the safe `message` for display, and the `correlationId` for
 * finding the failure in the server log.
 */

import type { ErrorResponse } from "@/lib/types";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000";

/**
 * A failed API call, with the server's own explanation preserved.
 *
 * Throwing rather than returning a result type keeps call sites readable — the happy path is the
 * body of the function, and error handling sits in one place at the edge.
 */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly correlationId: string;
  readonly details?: { field: string; problem: string }[];

  constructor(
    status: number,
    code: string,
    message: string,
    correlationId: string,
    details?: { field: string; problem: string }[],
  ) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.correlationId = correlationId;
    this.details = details;
  }

  /** Whether the user's session is the problem, so the UI should send them to sign in again. */
  get isAuthError(): boolean {
    return this.status === 401;
  }
}

type RequestOptions = {
  method?: string;
  body?: unknown;
  accessToken?: string | null;
  /** Passed through so Next.js can cache or revalidate a server-side fetch. */
  next?: NextFetchRequestConfig;
  signal?: AbortSignal;
};

/**
 * Make a request to the API and return the parsed body.
 *
 * @throws {ApiError} on any non-2xx response
 */
export async function apiFetch<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = "GET", body, accessToken, next, signal } = options;

  const headers: Record<string, string> = { Accept: "application/json" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (accessToken) headers["Authorization"] = `Bearer ${accessToken}`;

  const response = await fetch(`${API_BASE_URL}${path}`, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
    // Patient data is per-user and changes as staff work. Caching it would show one person's
    // dashboard to the next, and a stale "Overdue" count is worse than a slow one.
    cache: "no-store",
    next,
    signal,
  });

  if (!response.ok) {
    throw await toApiError(response);
  }

  // 204, and any other body-less success.
  if (response.status === 204 || response.headers.get("content-length") === "0") {
    return undefined as T;
  }

  return (await response.json()) as T;
}

/**
 * Upload a file as multipart form data — the CSV import path.
 *
 * Separate from `apiFetch` because the Content-Type header must be left unset: the browser sets
 * it, including the multipart boundary, and overriding it produces a request the server cannot
 * parse.
 */
export async function apiUpload<T>(
  path: string,
  file: File,
  options: { accessToken?: string | null } = {},
): Promise<T> {
  const formData = new FormData();
  formData.append("file", file);

  const headers: Record<string, string> = { Accept: "application/json" };
  if (options.accessToken) headers["Authorization"] = `Bearer ${options.accessToken}`;

  const response = await fetch(`${API_BASE_URL}${path}`, {
    method: "POST",
    headers,
    body: formData,
    cache: "no-store",
  });

  if (!response.ok) {
    throw await toApiError(response);
  }

  return (await response.json()) as T;
}

/** Turn an error response into an `ApiError`, falling back gracefully if it is not our envelope. */
async function toApiError(response: Response): Promise<ApiError> {
  const correlationId = response.headers.get("X-Correlation-ID") ?? "";

  try {
    const body = (await response.json()) as ErrorResponse;
    return new ApiError(
      response.status,
      body.error.code,
      body.error.message,
      body.error.correlation_id || correlationId,
      body.error.details,
    );
  } catch {
    // Not JSON, or not our shape — a proxy error page, or the API being down entirely. Say
    // something true rather than surfacing a JSON parse failure to the user.
    return new ApiError(
      response.status,
      "UNEXPECTED_RESPONSE",
      response.status >= 500
        ? "The server could not complete that request. Please try again."
        : "That request could not be completed.",
      correlationId,
    );
  }
}

export { API_BASE_URL };
