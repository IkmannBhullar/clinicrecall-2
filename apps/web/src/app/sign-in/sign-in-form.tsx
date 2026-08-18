"use client";

/**
 * The sign-in form.
 *
 * Email and password against Supabase Auth. No sign-up, no password reset, no social providers —
 * a clinic's staff accounts are created by whoever administers the practice, and SPEC §1 rules
 * out anything not on the path from "upload CSV" to "revenue recovered".
 *
 * The demo credentials are shown on the page. That is deliberate: this is a local instance
 * holding synthetic records, and a demo whose password has to be looked up in a README is a demo
 * that stalls in front of the customer.
 */

import { useRouter, useSearchParams } from "next/navigation";
import * as React from "react";

import { Button } from "@/components/ui/button";
import { DemoIndicator } from "@/components/shell/demo-indicator";
import { Card, FieldError, Input, Label, Spinner } from "@/components/ui/primitives";
import { createClient } from "@/lib/supabase/client";

/** Matches the account the seed creates. Kept here so the demo can be run without the README. */
const DEMO_EMAIL = "alex.morgan@greenvalley.example.com";
const DEMO_PASSWORD = "ClinicRecallDemo2026!";

export function SignInForm() {
  const router = useRouter();
  const searchParams = useSearchParams();

  const [email, setEmail] = React.useState(DEMO_EMAIL);
  const [password, setPassword] = React.useState(DEMO_PASSWORD);
  const [error, setError] = React.useState<string | null>(null);
  const [submitting, setSubmitting] = React.useState(false);

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setSubmitting(true);

    const { error: signInError } = await createClient().auth.signInWithPassword({
      email,
      password,
    });

    if (signInError) {
      // Supabase's own message is shown for a genuine credential failure, but anything that
      // looks like a connection problem gets a message that names the real cause — "Invalid
      // login credentials" would send someone hunting for a typo when the auth server is down.
      setError(
        signInError.message.toLowerCase().includes("fetch")
          ? "Could not reach the sign-in service. Is the local stack running? Try `make dev`."
          : signInError.message,
      );
      setSubmitting(false);
      return;
    }

    // Where they were headed before being redirected here, if anywhere.
    const next = searchParams.get("next");
    // `refresh` first so server components re-render with the new session cookie; without it the
    // dashboard renders as though still signed out.
    router.replace(next && next.startsWith("/") ? next : "/dashboard");
    router.refresh();
  }

  return (
    <>
      <Card className="p-6">
          <form onSubmit={handleSubmit} className="space-y-4" noValidate>
            <div>
              <Label htmlFor="email">Email</Label>
              <Input
                id="email"
                name="email"
                type="email"
                autoComplete="username"
                required
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                aria-invalid={error ? true : undefined}
                aria-describedby={error ? "sign-in-error" : undefined}
                className="mt-1.5"
              />
            </div>

            <div>
              <Label htmlFor="password">Password</Label>
              <Input
                id="password"
                name="password"
                type="password"
                autoComplete="current-password"
                required
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                aria-invalid={error ? true : undefined}
                aria-describedby={error ? "sign-in-error" : undefined}
                className="mt-1.5"
              />
            </div>

            {/* aria-live so the failure is announced when it appears, not only when focus
                happens to land on it (SPEC §10). */}
            <div aria-live="polite">
              <FieldError id="sign-in-error">{error}</FieldError>
            </div>

            <Button type="submit" disabled={submitting} className="w-full">
              {submitting ? <Spinner /> : null}
              {submitting ? "Signing in…" : "Sign in"}
            </Button>
          </form>
      </Card>

      <div className="mt-6 flex flex-col items-center gap-3">
        <DemoIndicator />
        <p className="text-center text-xs leading-relaxed text-ink-subtle">
          Demo credentials are filled in above. This instance contains synthetic patient records
          only.
        </p>
      </div>
    </>
  );
}
