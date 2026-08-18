/**
 * Sign in.
 *
 * A server component wrapping the form in a Suspense boundary.
 *
 * The boundary is required rather than stylistic: the form reads `?next=` with
 * `useSearchParams`, and Next.js cannot statically prerender a route that does so without one —
 * the build fails outright. Suspense lets the page shell render immediately and the interactive
 * form hydrate after it, which is also the better loading behaviour.
 */

import { Suspense } from "react";

import { Card, Skeleton } from "@/components/ui/primitives";

import { SignInForm } from "./sign-in-form";

export const metadata = { title: "Sign in" };

export default function SignInPage() {
  return (
    <main
      id="main-content"
      className="flex min-h-screen flex-col items-center justify-center bg-canvas px-4 py-12"
    >
      <div className="w-full max-w-sm">
        <div className="mb-8 text-center">
          <p className="text-xl font-semibold tracking-tight text-ink">
            Clinic<span className="text-brand">Recall</span>
          </p>
          <p className="mt-1.5 text-sm text-ink-muted">
            Sign in to your practice&rsquo;s recall dashboard.
          </p>
        </div>

        <Suspense fallback={<SignInFormSkeleton />}>
          <SignInForm />
        </Suspense>
      </div>
    </main>
  );
}

/** Matches the form's dimensions, so hydration does not shift the page. */
function SignInFormSkeleton() {
  return (
    <Card className="space-y-4 p-6" aria-busy="true">
      <Skeleton className="h-4 w-12" />
      <Skeleton className="h-9 w-full" />
      <Skeleton className="h-4 w-16" />
      <Skeleton className="h-9 w-full" />
      <Skeleton className="h-9 w-full" />
    </Card>
  );
}
