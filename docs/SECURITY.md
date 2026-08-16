# Security

> **Status:** written incrementally as the build proceeds; completed in phase 12. Controls are
> documented here as they land, so this file never describes protection that does not exist yet.

## This is not a compliance document

ClinicRecall is **not HIPAA compliant** and nothing in this repository claims otherwise.

A real healthcare deployment handling actual patient information would additionally require, at
minimum:

* Properly configured production infrastructure (network isolation, encryption at rest and in
  transit, key management, backup and recovery).
* An independent security review and ongoing vulnerability management.
* Business Associate Agreements with every vendor that touches protected health information —
  the hosting provider, the email provider, and any monitoring service.
* Operational controls: access provisioning and revocation, audit logging with retention,
  incident response, breach notification procedures.
* Written policies, workforce training, and a risk analysis.
* Legal and compliance review by people qualified to do it.

None of that is in scope here. This project holds **synthetic patient records only**, and the
application chrome says so on every screen.

## Controls implemented so far

### Secret handling (phase 1)

The Supabase service-role key grants unrestricted database access. Three independent checks stop
it reaching a browser:

1. **Generated web env file.** `scripts/supabase-up.sh` writes `apps/web/.env.local` from a fixed
   allowlist of browser-safe variables. The service-role key is not on that list, so it cannot
   arrive there by accident or by a careless edit that gets copied forward.
2. **Lint rule.** `apps/web/eslint.config.mjs` makes reading any secret-bearing environment
   variable inside `apps/web/` an error, caught while you type.
3. **Build-output grep.** `scripts/check-bundle-secrets.sh` searches the compiled `.next` output
   for the literal key and for the `service_role` marker. This runs as a gate in `make verify`,
   so a leak fails the build rather than relying on anyone noticing.

Only `.env.example` is tracked by git. Every real `.env` file is gitignored, and the two
per-machine secrets (`JOB_TOKEN`, `UNSUBSCRIBE_TOKEN_SECRET`) are generated locally at setup with
`openssl rand`, never shipped.

### Browser security headers (phase 1)

Set in `apps/web/next.config.mjs` for every route:

| Header | What it does here |
|--------|-------------------|
| `Content-Security-Policy` | Restricts every resource type to the app's own origin. `connect-src` allows exactly two destinations: this project's API and its Supabase instance. This is what makes [SPEC constraint D2](./SPEC.md#2-non-negotiable-demo-constraints) — zero runtime network egress — enforced by the browser rather than merely intended. |
| `X-Content-Type-Options: nosniff` | Stops the browser second-guessing a declared content type, which is one route by which an uploaded file becomes executable script. |
| `Referrer-Policy: same-origin` | Patient identifiers appear in URLs; this keeps them out of `Referer` headers sent to third parties. |
| `X-Frame-Options: DENY` and `frame-ancestors 'none'` | Clickjacking protection. |
| `Permissions-Policy` | Explicitly declines camera, microphone, geolocation, payment, and USB access. |
| `Strict-Transport-Security` | Inert on localhost, required once deployed over HTTPS. |

### Tenant isolation (phase 2)

The primary control is **repository-level scoping**: `organization_id` is a required first
argument on every method of every repository over a tenant-owned table. A forgotten scope is a
`TypeError` at the call site rather than a silent data leak. A structural test walks the
repository classes and fails if any public method lacks it — that test found and removed a real
leak (an unscoped `flush()` passthrough) the first time it ran.

**Row-level security** is enabled on all seven tenant tables as a second net, with policies keyed
on `current_setting('app.current_organization_id', true)::uuid`.

Being precise about what RLS does and does not do here, because it is easy to overstate:

* RLS is enabled but **not** `FORCE`d. In Postgres a table's owner is exempt from its policies
  unless `FORCE` is set, and the API connects as the owner — so these policies do not affect
  normal application requests. That is the arrangement SPEC §3.2 describes.
* What they protect is every path that does **not** go through the application: a Supabase Studio
  session, a future role granted direct table access, or a deployment running as a non-owner
  role (which is what a real production deployment should do).
* Supabase's `anon` and `authenticated` roles are granted **no** access to these tables at all,
  so PostgREST cannot reach patient data even before RLS is consulted. Patient data is served
  exclusively by the FastAPI backend.
* The failure direction is correct: an unset session variable yields `NULL`, `NULL` equals
  nothing, and the connection sees zero rows. Forgetting the scope denies access rather than
  granting it.

`tests/test_rls_policies.py` proves the policies actually filter, by creating a throwaway role
inside the test transaction and watching rows appear and disappear as the organization variable
changes. Inspecting the catalog alone would only show that a policy exists — a policy of
`USING (true)` would look perfectly healthy there and protect nothing.

### Data minimisation (phase 2)

`activity_events.payload` is restricted by convention *and* by documentation at the point of
writing: it may hold identifiers, initials, enum values, counts, and dates — never names, email
addresses, or phone numbers (SPEC §9). The activity feed renders readable sentences by joining to
the live patient row at display time, so the log itself carries nothing sensitive.

This matters because an audit table is the one most likely to be exported, shipped to a log
aggregator, or retained long after the record it describes has been deleted.

Patient identifiers in URLs are opaque `public_id` values, never database keys — so a bookmarked
URL leaks neither the row's identity nor how many patients a practice has.

## Controls still to come

| Control | Phase |
|---------|-------|
| Supabase JWT verification via cached JWKS; `iss` / `aud` / `exp` validation | 4 |
| Server-side `organization_id` resolution (never accepted from the client) | 4 |
| CORS restricted to the configured web origin | 4 |
| Rate limiting on auth, import, and send-reminder endpoints | 4 |
| Error envelope with correlation IDs; no stack traces to clients | 4 |
| PII redaction filter on application logs | 4 |
| Request size and row-count limits on CSV upload | 6 |
| Tokenized, signed unsubscribe links | 5 |
