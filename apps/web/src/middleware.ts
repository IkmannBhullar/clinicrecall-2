/**
 * Route protection, and keeping the session fresh.
 *
 * Runs before every matching request. Two jobs:
 *
 * 1. **Refresh the Supabase session.** Access tokens expire after an hour. Without a refresh on
 *    each request, a staff member who leaves a tab open over lunch comes back to an application
 *    that appears signed in and fails every API call — which reads as the product being broken
 *    rather than as a session expiring.
 *
 * 2. **Redirect signed-out visitors to the sign-in page**, and signed-in ones away from it.
 *
 * This is a convenience, not a security boundary. The real control is the API, which verifies
 * the token on every request (SPEC §3.2) — middleware only decides which page to render, and a
 * page rendered without data is not a leak.
 */

import { createServerClient } from "@supabase/ssr";
import { NextResponse, type NextRequest } from "next/server";

/** What `@supabase/ssr` hands to `setAll`. The package does not export this shape. */
type CookieToSet = {
  name: string;
  value: string;
  options?: Record<string, unknown>;
};

/** Paths reachable without signing in. */
const PUBLIC_PATHS = ["/sign-in", "/auth"];

export async function middleware(request: NextRequest) {
  let response = NextResponse.next({ request });

  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const anonKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;

  // Without configuration there is no session to check. Letting the request through means the
  // page renders and explains the problem, rather than every route redirecting into a loop.
  if (!url || !anonKey) {
    return response;
  }

  const supabase = createServerClient(url, anonKey, {
    cookies: {
      getAll() {
        return request.cookies.getAll();
      },
      setAll(cookiesToSet: CookieToSet[]) {
        // Written to both: the request so any handler further along sees the refreshed session,
        // and the response so the browser stores it.
        for (const { name, value } of cookiesToSet) {
          request.cookies.set(name, value);
        }
        response = NextResponse.next({ request });
        for (const { name, value, options } of cookiesToSet) {
          response.cookies.set(name, value, options as Parameters<typeof response.cookies.set>[2]);
        }
      },
    },
  });

  // getUser() rather than getSession(): it validates the token with the auth server instead of
  // trusting whatever the cookie claims. Slower by a round trip, and the difference between
  // "there is a session cookie" and "there is a valid session".
  const {
    data: { user },
  } = await supabase.auth.getUser();

  const { pathname } = request.nextUrl;
  const isPublic = PUBLIC_PATHS.some((path) => pathname.startsWith(path));

  if (!user && !isPublic) {
    const signInUrl = request.nextUrl.clone();
    signInUrl.pathname = "/sign-in";
    // Remember where they were headed, so signing in lands them there rather than dumping
    // everyone on the dashboard.
    signInUrl.searchParams.set("next", pathname);
    return NextResponse.redirect(signInUrl);
  }

  if (user && pathname === "/sign-in") {
    const dashboardUrl = request.nextUrl.clone();
    dashboardUrl.pathname = "/dashboard";
    dashboardUrl.search = "";
    return NextResponse.redirect(dashboardUrl);
  }

  return response;
}

export const config = {
  matcher: [
    /*
     * Everything except static assets and the favicon.
     *
     * Excluding `_next/static` and `_next/image` matters for more than tidiness: the font file
     * is served from there, and running an auth check before it would add a round trip to the
     * auth server in front of the first paint.
     */
    "/((?!_next/static|_next/image|favicon.ico|.*\\.(?:svg|png|jpg|jpeg|gif|webp|woff2)$).*)",
  ],
};
