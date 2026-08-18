/**
 * The root path.
 *
 * Nothing lives at `/` — it exists only to send people where they were going. The middleware has
 * already decided whether they are signed in, so this is a redirect either way: to the dashboard
 * if the session is valid, and (via the middleware) back to sign-in if it is not.
 */

import { redirect } from "next/navigation";

export default function RootPage() {
  redirect("/dashboard");
}
