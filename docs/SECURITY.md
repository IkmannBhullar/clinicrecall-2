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

### Authentication (phase 4)

Access tokens are issued by Supabase Auth (GoTrue) and verified here against the public keys it
publishes. We hold no secret capable of *minting* a token — only of checking one.

**Asymmetric algorithms only.** `ALLOWED_ALGORITHMS` is `["ES256", "RS256"]`, and that allowlist
is a security control rather than configuration. The attack it prevents is algorithm confusion:
JWKS public keys are published to the world by design, so if `HS256` were accepted an attacker
could take that public key, use it as the HMAC *secret*, sign any payload they liked, and a
verifier trusting the token's own `alg` header would check the HMAC with the same public key and
accept it — minting a valid token for any user in the system. `none` is not listed and never will
be. Both cases have tests.

**Every claim is validated.** `iss`, `aud`, `exp` and `sub` are all *required*, not merely checked
if present: a token missing `exp` would be valid forever, and one missing `iss` could come from
any Supabase project on the internet. A 30-second leeway absorbs clock skew, so a machine running
a few seconds fast does not produce intermittent sign-outs.

**Key caching, and the bound.** Fetching the JWKS document per request would be absurd; never
refetching would mean a key rotation locks everyone out until a restart. So keys are cached with
a TTL, an unknown `kid` triggers a refresh (a rotation looks exactly like an unknown `kid`), and
refreshes are rate-limited to one per 10 seconds. That bound matters: without it, a client
sending tokens with random `kid` values causes one outbound request per token, turning our
verification path into a denial-of-service amplifier aimed at the auth server — using nothing but
unauthenticated requests. An empty cache is exempt from the bound, so a failed first fetch cannot
cause the outage the bound exists to prevent.

**Failing to verify is not the same as refusing.** An unreachable JWKS endpoint returns 503, not
401. Telling users their session is invalid when the real problem is that the auth server is down
sends them to re-enter a password that was never wrong.

### Tenant scope resolution (phase 4)

`get_current_user` performs the resolution SPEC §3.2 mandates:

```
Authorization header → verified JWT → JWT.sub → users.auth_user_id → users.organization_id
```

**The organization is never taken from the client** — not from the body, not from a query
parameter, not from a header, and not from a claim inside the token. `TokenClaims` deliberately
carries no organization and no role; a test asserts their absence structurally so a future
"convenience" field cannot quietly become a trust boundary. A test also sends a foreign
organization id by header *and* query string simultaneously and asserts the response still names
the caller's real practice.

A verified token whose subject has no application user is refused. That is a real situation, not a
theoretical one: a Supabase account can be created from the Supabase dashboard or by a
half-finished invite, and such an account must reach nothing at all.

### Transport and request handling (phase 4)

| Control | Detail |
|---|---|
| CORS | Exactly one origin, from configuration. Never a wildcard — with credentials enabled that would let any site on the internet make authenticated requests on a signed-in user's behalf. |
| Security headers | `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, `Cache-Control: no-store`, HSTS, and a `default-src 'none'` CSP. Present on error responses too, since a 401 reaches a browser exactly as a 200 does. |
| Request size limit | 10 MB, refused on the declared `Content-Length` before a byte is read. An unbounded upload lets one request exhaust server memory, and it takes no authentication to attempt. The CSV importer also counts bytes while streaming, because a chunked request sends no `Content-Length`. |
| Rate limiting | slowapi, never hand-rolled. Separate limits for reads, auth, import, manual sends, and admin utilities — each sized to the specific abuse rather than picked to look prudent. |

### Error responses (phase 4)

Every error uses one envelope: a stable `code` the frontend branches on, a `message` safe to show
a receptionist, and a `correlation_id`.

**Stack traces never reach the client.** A traceback tells an attacker the framework, the file
layout, the ORM, and often a SQL fragment, while telling the person reading it precisely nothing.
The full exception is logged server-side against the correlation ID; the client gets a generic
message and that twelve-character identifier. A test fetches several failing routes and asserts
the response body contains no traceback, no `sqlalchemy`, no `psycopg`, and no filesystem path.

`NotFoundError` is returned both for records that do not exist and for records belonging to
another practice. Distinguishing them would let someone holding a foreign patient identifier
learn that it is real.

### Reminders and opt-out (phase 5)

**Signed unsubscribe links.** Every reminder carries a one-click opt-out, reached by someone with
no session at all — a patient, on a phone, from an email. The naive URL `/unsubscribe/{public_id}`
fails badly: anyone could walk the identifier space and opt out a clinic's entire recall list, and
because opt-out is honoured permanently that is a quiet, hard-to-notice denial of service against
the product's core function. So the link carries an HMAC over the patient's public id, verified
with a constant-time comparison.

The links **never expire**, deliberately. A patient who finds a two-year-old reminder and wants to
stop hearing from the practice must be able to. An expired consent mechanism stops working exactly
when someone tries to use it.

Failures are indistinguishable: a bad signature and an unknown patient produce the same page, so
the endpoint cannot be used as an oracle for discovering which identifiers are real. It is also
rate-limited, since without that it is a fast way to test forged tokens.

**The job token.** `POST /internal/jobs/process-reminders` is called by a scheduler, which has no
user session, so it authenticates on a shared `X-Job-Token` header — compared in constant time,
for the same reason as the unsubscribe signature. A missing token and a wrong one return byte-
identical responses.

**Manual send throttle.** Manual sends are deliberately exempt from the reminder unique index (see
`ARCHITECTURE.md`), so nothing structural stops a staff member — or a double-click — emailing the
same person repeatedly. A one-hour per-patient cooldown fills that gap. It protects a patient's
inbox rather than the server, which is why it is time-based rather than a database constraint.

**What a reminder may contain.** Nothing clinical, ever (SPEC §6.5). No diagnosis, condition, or
visit reason. A recall email goes to an address the practice cannot vouch for, may be read on a
shared screen or a lock-screen preview, and sits in an inbox indefinitely. A test asserts a list
of clinical terms appears nowhere in the rendered message, because this is exactly the copy
someone later "improves" by adding a helpful detail.

The email also loads nothing from the network — no images, no web fonts, no scripts — which is
both SPEC constraint D2 and ordinary good practice, since most mail clients block remote content
by default.

### CSV import (phase 6)

A patient list is the most sensitive thing a practice ever hands over, and the import endpoint is
where it arrives.

**Two independent size limits.** The middleware refuses an oversized `Content-Length` before
reading a byte — but a chunked upload declares no length at all, so the parser also counts bytes
while streaming and stops at 10 MB. A row cap (50,000) bounds the transaction separately, since a
hundred million rows of `a,b,c` compresses very well over the wire.

**Streamed, not loaded.** The file is parsed row by row rather than read into memory whole.

**No network during validation.** Email checking runs with `check_deliverability=False`, so
importing never makes a DNS query. That is SPEC constraint D2, and it is also what stops a
clinic's own domain failing a lookup on conference wifi from rejecting their entire file.

**The organization comes from the token.** As everywhere else — a test posts a foreign
`organization_id` in the form data and asserts it has no effect.

**Errors say what to do, not what broke.** Every rejection message is written for a receptionist:
"'13/04/2025' looks like a day-first date… please use the format 2024-01-15", never a stack trace
or a parser exception. The downloadable error report carries a row number matching what the
practice sees when they open the file in a spreadsheet.

**All or nothing.** The import runs in one transaction. Tested in two halves rather than by
making an endpoint blow up: that the service stages writes without committing (verified from a
separate connection, which cannot see uncommitted work), and that `get_db` rolls back when a
request raises.

## Controls still to come

Nothing outstanding from SPEC §9. Later phases add UI surfaces for controls already implemented.
