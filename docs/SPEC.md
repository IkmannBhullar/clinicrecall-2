# ClinicRecall — Master Build Spec

> This file is the **contract** for the build, committed verbatim as handed over. It is not a
> prompt and not a design document to be improved — where the code and this file disagree, the
> code is wrong.
>
> Resolutions to the ambiguities this spec leaves open are recorded in
> [`ARCHITECTURE.md`](./ARCHITECTURE.md) under "Resolved ambiguities", not by editing this file.

## 1. Product

ClinicRecall helps medical practices reduce missed annual visits by identifying patients due for
their annual appointment and sending professional reminders.

**Workflow:** upload patients → system determines who is due → reminders are scheduled → patient
receives reminder → staff tracks activity → appointment marked scheduled → visit marked
completed → next due date recalculated.

**Governing principle:** a nontechnical clinic employee understands how to use this in ~30
seconds. When two designs both work, ship the one that needs less explanation.

### Out of scope — do not build

Charts, diagnoses, prescriptions, insurance, billing, clinical documentation, treatment plans,
labs, telehealth, EHR features, calendar/scheduling infrastructure, payments, SMS delivery, AI
features, workflow/automation builders, multi-step approval flows.

If a feature is not on the critical path from "upload CSV" to "revenue recovered," it is out of
scope. Scope creep is the primary failure mode for this project.

## 2. Non-negotiable demo constraints

These are ranked above code elegance. The product is demonstrated live, in a clinic, on someone
else's wifi.

| #  | Constraint | Why |
|----|------------|-----|
| D1 | Seed dates are relative to today, never hardcoded calendar dates. Every seeded date is computed as `today ± N days`. | A demo built with absolute dates is wrong within a week and every status badge lies. |
| D2 | Zero runtime network egress in demo mode. No CDN fonts, no remote images, no analytics, no telemetry, no external API calls. Self-host fonts; inline or bundle all assets. | Clinic guest wifi is captive-portalled or blocked. A missing font stalls first paint. |
| D3 | `make demo-reset` restores pristine seed state in under 30 seconds, and an admin-only "Reset demo data" control exists in Settings. | You will demo to several clinics. Each demo mutates state. |
| D4 | Named demo fixtures are deterministic. The seed must guarantee the exact patients the demo script names, in the exact states it claims (see §7.3). | The talk track must match what is on screen. |
| D5 | Entire stack starts from cold with two commands, documented and verified: `make setup` then `make dev`. | Anything longer will fail under demo pressure. |
| D6 | Nothing in the UI or docs claims HIPAA compliance. A "Demo Data — synthetic patients only" indicator is persistently visible in the app chrome. | Legal exposure and credibility. |

## 3. Stack — pinned

| Layer | Choice | Pin |
|-------|--------|-----|
| Monorepo | pnpm workspaces + Makefile at root | pnpm 9.x |
| Frontend | Next.js App Router, React, TypeScript strict, Tailwind, shadcn/ui, lucide-react | Node 20 LTS |
| Backend | Python, FastAPI, SQLAlchemy 2.x (typed), Pydantic v2, Alembic | Python 3.12, uv |
| Database | PostgreSQL | 16 |
| Auth | Supabase Auth, run locally via Supabase CLI (`supabase start`) | Supabase CLI latest |
| Backend lint/type | ruff (lint + format), mypy strict on `app/services` and `app/schemas` | — |
| Frontend lint | eslint + prettier, `tsc --noEmit` | — |
| Tests | pytest (+ pytest-asyncio, httpx AsyncClient), Playwright | — |

No Java. No ORM raw-string SQL. No hand-rolled date math (`dateutil.relativedelta` only).

### 3.1 Supabase, run locally

The user chose real Supabase Auth. To satisfy D2 and D5, run the entire Supabase stack locally
in Docker via the Supabase CLI — this gives genuine GoTrue auth, real JWTs, and real JWKS
verification with zero cloud dependency and zero network at demo time.

* `supabase start` provides Postgres + Auth + Studio locally.
* Alembic owns the application schema in the `public` schema. Supabase owns `auth.*`. Alembic
  must never migrate `auth.*`.
* The same code path works against a hosted Supabase project by changing env vars only. Document
  both in the README; default `.env.example` to local.

### 3.2 Supabase security rules (mandatory)

* The service role key is server-side only. It must never appear in any file under `apps/web/`
  that ships to the browser, and never in a `NEXT_PUBLIC_*` variable. Add a CI/test assertion
  that greps the built client bundle for the service key prefix.
* The FastAPI backend verifies the Supabase JWT via JWKS with a cached key set (refresh on `kid`
  miss, bounded retry). Validate `iss`, `aud`, `exp`. Reject otherwise.
* `organization_id` is never accepted from the client. It is resolved server-side:
  `JWT.sub → users.auth_user_id → users.organization_id`. Encode this in a single FastAPI
  dependency (`get_current_user`) that every protected route depends on.
* Enable RLS on all tenant tables as defense in depth, with policies keyed on `organization_id`.
  Document clearly in `docs/SECURITY.md` that the API connects with a role that bypasses RLS, so
  RLS is a second net guarding against direct DB access — not the primary control. The primary
  control is repository-level org scoping.
* Repository layer takes `organization_id` as a required first argument on every read and write.
  No repository method may be callable without it. This makes tenant leakage a type error rather
  than a review catch.

## 4. Data model

### 4.1 Tables

**organizations** — `id` (uuid pk), `name`, `slug` (unique), `created_at`, `updated_at`

**users** — `id`, `organization_id` (fk), `auth_user_id` (unique, from Supabase), `first_name`,
`last_name`, `email`, `role` (`ADMIN` | `STAFF`), `created_at`, `updated_at`

**patients** — `id` (uuid pk), `organization_id` (fk), `public_id` (short opaque slug used in all
URLs and API paths), `external_id` (nullable), `first_name`, `last_name`, `email`, `phone`
(nullable), `preferred_contact_method`, `last_annual_visit_date` (DATE), `next_annual_due_date`
(DATE, derived), `status` (derived cache — see §5.1), `scheduled_for` (DATE, nullable),
`reminders_enabled` (bool), `opted_out_at` (nullable timestamptz), `created_at`, `updated_at`

* Unique index: `(organization_id, external_id)` where `external_id` is not null
* Unique index: `(organization_id, lower(email))`
* Index: `(organization_id, next_annual_due_date)`, `(organization_id, status)`

**reminder_rules** — `id`, `organization_id`, `key` (stable enum: `T_MINUS_30`, `T_MINUS_7`,
`T_ZERO`, `T_PLUS_30`), `days_relative_to_due_date` (int, negative = before), `enabled`,
`template_id`, `created_at`, `updated_at`

**reminder_events** — `id`, `organization_id`, `patient_id`, `reminder_rule_id`,
`due_date_snapshot` (DATE — the due date the reminder was computed against), `channel`,
`scheduled_at`, `sent_at`, `status`, `provider_message_id` (nullable), `failure_reason`
(nullable), `created_at`

* Statuses: `SCHEDULED` | `SENDING` | `SENT` | `DELIVERED` | `FAILED` | `CANCELLED`
* Unique index: `(patient_id, reminder_rule_id, due_date_snapshot)` — this single constraint is
  what makes the reminder job idempotent. See §6.2.

**clinic_settings** — `organization_id` (pk/fk), `clinic_name`, `phone`, `email`, `website`,
`scheduling_url`, `timezone` (IANA string), `annual_interval_months` (int, default 12),
`estimated_annual_visit_value` (numeric), `reminder_signature`, `created_at`, `updated_at`

**activity_events** — `id`, `organization_id`, `actor_user_id` (nullable — null = system),
`type`, `subject_patient_id` (nullable), `payload` (jsonb, minimized — see §9), `created_at`

* Types: `PATIENT_IMPORTED`, `PATIENT_CREATED`, `PATIENT_UPDATED`, `REMINDER_SENT`,
  `REMINDER_DELIVERED`, `REMINDER_FAILED`, `APPOINTMENT_SCHEDULED`, `ANNUAL_VISIT_COMPLETED`,
  `REMINDERS_PAUSED`, `SETTINGS_UPDATED`

### 4.2 ID exposure

Never expose integer or database UUID primary keys in URLs or API responses for patients. Use
`public_id` (e.g. 12-char base32, generated at insert). Internal FKs stay UUID.

## 5. RecallService — the domain core

One module. All status and date logic lives here. No status logic anywhere else in the codebase.
This is the most-tested file in the repo.

### 5.1 Status is derived, not authored

`patients.status` is a denormalized cache of a pure function, not an independently editable
field. Define:

```python
def compute_status(patient: PatientRecallInput, today: date) -> PatientStatus
```

It is pure, takes `today` explicitly (never calls `date.today()` internally — this is what makes
it testable), and is the sole writer of `patients.status`. A scheduled recompute and every
mutation path call it. Add a test that walks every patient in a seeded org and asserts
`stored_status == compute_status(...)` — the drift guard.

### 5.2 Status bands

Let `d = (next_annual_due_date - today).days`.

| Condition | Status |
|-----------|--------|
| `reminders_enabled` is False or `opted_out_at` set, and not otherwise terminal | `INACTIVE` |
| `scheduled_for` is set and `scheduled_for >= today` | `SCHEDULED` |
| Visit completed within the last 30 days | `COMPLETED` |
| `d > 30` | `ACTIVE` |
| `1 <= d <= 30` | `DUE_SOON` |
| `-7 <= d <= 0` | `DUE` |
| `d < -7` | `OVERDUE` |

Precedence is top-to-bottom. Two clarifications the original brief left contradictory:

* **`COMPLETED` vs "return to ACTIVE".** Completing a visit advances `last_annual_visit_date` and
  recomputes `next_annual_due_date`, which naturally yields `ACTIVE`. `COMPLETED` is therefore a
  transient display state for 30 days after completion so staff get visible confirmation, then it
  decays to `ACTIVE` automatically.
* **`SCHEDULED` expires.** If `scheduled_for` passes without a completion, the patient falls back
  to the date-derived band. Without this, a demo left running shows patients permanently
  "Scheduled" and the funnel is a lie.

### 5.3 Date rules

* `next_annual_due_date = last_annual_visit_date + relativedelta(months=annual_interval_months)`
  where the interval comes from `clinic_settings`, not a constant.
* Feb 29 + 12 months → Feb 28 (`relativedelta`'s behavior; assert it in a test).
* All due dates are calendar dates, never timestamps.
* "Today" is `datetime.now(ZoneInfo(clinic_settings.timezone)).date()`. A demo at 9pm Pacific must
  not show tomorrow's statuses. One helper: `today_for_org(org)`.

### 5.4 Required pytest coverage

Annual date calculation · leap-year boundary · interval override from settings · each status band
including both edges of every band · scheduled-then-expired · completion recompute · opted-out
precedence · timezone boundary (23:30 local vs UTC) · drift guard.

## 6. ReminderService

### 6.1 Eligibility

A patient is eligible for rule R when: reminders are enabled, not opted out, status is not
`SCHEDULED`/`COMPLETED`/`INACTIVE`, a valid email exists, and
`today == next_annual_due_date + R.days_relative_to_due_date`, and `R.enabled`.

Include a bounded catch-up window (e.g. rule fires if the target date fell within the last 3 days
and no event exists) so a missed job run does not silently drop reminders. Document the window.

### 6.2 Idempotency

Do not implement idempotency with an application-level "have I sent this?" query — that races.
Rely on the unique index `(patient_id, reminder_rule_id, due_date_snapshot)`: insert the
`reminder_events` row first inside the transaction, catch `IntegrityError`, skip. Only then hand
to the provider.

Test: run `process_reminders` three times in a row against the same seed; assert the
`reminder_events` count is identical after runs 2 and 3, and the provider was called exactly once
per eligible patient.

**Manual sends are exempt.** The "Send Reminder" button on patient detail creates an event with
`reminder_rule_id = NULL` and a `MANUAL` source, so it never collides with the unique index.
Without this carve-out, clicking Send Reminder during a demo on a patient whose rule already fired
returns a duplicate error on stage. Rate-limit manual sends per patient (e.g. 1 per hour) instead.

### 6.3 Job endpoint

`POST /internal/jobs/process-reminders`, authenticated by a shared secret header (`X-Job-Token`,
constant-time compare against `JOB_TOKEN` env var). Returns a structured summary: `evaluated`,
`eligible`, `created`, `skipped_duplicate`, `sent`, `failed`.

Also expose an admin-only "Run reminder job" control in Settings, visibly labeled as a demo/admin
utility, so you can trigger it live.

### 6.4 Provider interface

```python
class EmailProvider(Protocol):
    def send(self, message: OutboundEmail) -> SendResult: ...
```

`MockEmailProvider` is the default and must be fully functional: it records the rendered message,
returns a synthetic provider message ID, and marks the event `SENT` then `DELIVERED` on a short
delay so the UI shows a realistic transition. Store rendered messages so the app can display the
exact email that "went out" — this is a strong demo beat. A real provider (Resend/SES) may sit
behind `EMAIL_PROVIDER=`, but the app must be complete and demoable with zero paid services and
zero network.

Define the channel enum with `SMS` present but unimplemented. Do not build SMS.

### 6.5 Email template

Responsive HTML + plain-text alternative. Restrained, no clinical content whatsoever.

```
Hi {{first_name}},

This is a friendly reminder from {{clinic_name}} that it may be time to schedule
your annual visit.

Please contact our office at {{clinic_phone}} or use the button below.

[ Schedule Appointment ]  → {{scheduling_url}}

{{reminder_signature}}
{{clinic_name}}

Reply STOP or click here to stop receiving these reminders.
```

The opt-out link is required — it hits a tokenized unsubscribe endpoint that sets `opted_out_at`
and writes an activity event. Clinics will ask about this; not having it undermines trust in a
demo. Never interpolate diagnoses, conditions, or visit reasons.

## 7. CSV import

The single most scrutinized screen in the demo. It must feel like a commercial import tool, not a
file input.

### 7.1 Flow

* Drag-and-drop → parse → validate → preview with per-row errors → confirm → import. Never
  silently drop a row.
* Columns: `first_name`, `last_name`, `email`, `phone` (optional), `last_annual_visit_date`,
  `external_id` (optional).
* Accept several date formats; normalize; reject ambiguous ones with a clear message.
* Validate email syntax; normalize case.
* Reject future `last_annual_visit_date`.
* Dedupe by `external_id` first, then `(organization_id, lower(email))`. Existing match = update,
  and the preview must say "X new, Y updates" distinctly.
* Enforce max upload size and max row count; stream-parse rather than loading whole file.
* Downloadable error report CSV containing original row number, the offending value, and a
  plain-English reason.
* Import runs in a single transaction with a summary activity event; partial failure does not
  leave half-imported state.

### 7.2 Shipped sample files

* `docs/samples/patients-sample.csv` — clean file, ~40 rows, dates generated relative to today by
  a small script so it never goes stale.
* `docs/samples/patients-messy.csv` — constructed to produce exactly the demo numbers:

```
327 records found
320 ready to import
  5 missing required information
  2 invalid email addresses
```

Add a pytest that runs the validator over this exact file and asserts those four numbers. If the
file drifts, the test fails before your demo does.

### 7.3 Deterministic demo fixtures

Green Valley Family Clinic · admin Alex Morgan · 55 synthetic patients spread across all statuses
with realistic distribution (not 8 per bucket).

The seed must guarantee these, asserted by a test:

| Patient | State | Detail |
|---------|-------|--------|
| Sarah Johnson | `OVERDUE` | due ~24 days ago; exactly 2 prior `DELIVERED` reminders (T-30, T-7). `T_ZERO` is deliberately left unsent so the live "Send Reminder" beat in the demo has something to do; the 3-day catch-up window (§6.1) is short enough that the job will not backfill it. This is the patient the demo opens. |
| Michael Brennan | `DUE_SOON` | due in ~11 days; 1 delivered reminder |
| Jennifer Tran | `DUE` | due today; 1 reminder delivered this morning |
| David Okafor | `SCHEDULED` | `scheduled_for` ~9 days out |
| Maria Castillo | `COMPLETED` | completed 6 days ago; next due ~359 days out |
| Robert Hale | `FAILED` reminder | hard bounce, surfaces the failure-recovery UI |

All identities fictional; emails on `@example.com`; phone numbers in the 555 range. Seed 60–90
days of backdated reminder and activity history so every chart and feed is populated at first
login. Seed must be idempotent — re-running does not duplicate.

Demo credentials created via the Supabase admin API in the seed script and documented in the
README.

## 8. Application surface

Navigation: **Dashboard · Patients · Reminders · Activity · Settings**. Nothing else.

### Dashboard

Greeting + "Here's your patient recall overview." KPI cards: Total Patients · Due This Month ·
Overdue · Reminders Sent · Appointments Recovered. Recall Overview visualization across the six
statuses. "Patients Needing Attention" table (Patient · Due Date · Status · Last Reminder ·
Action). Recent reminder activity. Estimated Revenue Recovered.

Revenue formula must be defined and shown on hover, or an office manager will poke it:

> Appointments recovered = patients who were `DUE` or `OVERDUE`, received ≥1 delivered reminder,
> and were marked scheduled within 30 days of that reminder. Estimated value =
> recovered × `estimated_annual_visit_value`.

Label it "Estimated" and expose the definition in the UI.

### Patients

Search, status filters, server-side pagination and sorting. Columns per original brief. Row
actions: View · Send Reminder · Mark Scheduled · Mark Annual Visit Completed. Bulk selection is
not required — skip it.

### Patient detail (drawer preferred over full page)

Header, status badge, next annual visit, contact info, reminder timeline with delivery states,
rendered-email preview, and the four actions plus Pause Reminders. No clinical fields.

### Reminders

Annual Recall Campaign: four rules with enable/disable toggles only — no automation builder.
Side-by-side live preview. Send Test Reminder (goes to mock provider). Performance strip:
Scheduled / Sent / Delivered / Failed. Failed items link to a fix-email recovery path.

### Activity

Chronological feed, initials rather than full names in the high-level list, filters:
All / Reminders / Patients / Imports.

### Settings

Clinic profile · reminder settings (value, signature, annual interval) · account/role · demo
utilities (Run reminder job, Reset demo data) clearly fenced as admin-only.

### Onboarding

Five steps, skippable, and skipped by default in the seeded demo environment.

## 9. Security & privacy

Beyond §3.2:

* Pydantic validation on every input; explicit request size limits; CORS restricted to the
  configured web origin; security headers (CSP, HSTS, X-Content-Type-Options, Referrer-Policy)
  via middleware; rate limiting on auth, import, and send-reminder endpoints (slowapi — do not
  hand-roll).
* Error responses use a consistent envelope with a stable code, a safe human message, and a
  correlation ID. Stack traces never reach the client. Log the trace server-side against the
  correlation ID.
* Data minimization: store only what recall requires. `activity_events.payload` holds IDs and
  initials, not names, emails, or phone numbers. No patient PII in application logs — add a log
  filter that redacts email/phone patterns and test it.
* `docs/SECURITY.md` explains each control and why, and states plainly that a real healthcare
  deployment additionally requires proper infrastructure configuration, security review, vendor
  agreements/BAAs where applicable, operational controls, policies, and legal/compliance review.
  **Do not claim compliance anywhere.**

## 10. Design

Premium B2B healthcare SaaS: clean, calm, trustworthy, generous whitespace, strong typographic
hierarchy, subtle borders and shadows, polished tables, precise badges, excellent empty states,
skeleton loaders, responsive (desktop-first, mobile usable).

**Avoid:** gradients, oversized rounded cards, animation, neon, cartoon medical imagery, emoji,
marketing headers inside the app, generic AI-template look.

**Accessibility:** semantic HTML, keyboard navigation throughout, visible focus rings, labeled
inputs, form errors associated via `aria-describedby`, adequate contrast, `aria-live` for async
results. Verify with an automated axe pass in the Playwright suite.

**Brand:** CSS/text wordmark only, no image assets.

## 11. Definition of done — executable

The checklist form is unverifiable; replace it with a command. `make verify` must exit 0.

```
make verify:
  supabase status          # stack up
  alembic upgrade head     # migrations apply to a fresh DB
  make seed                # seed is idempotent; run twice, assert stable counts
  ruff check . && ruff format --check .
  mypy app/services app/schemas
  pytest -q                # includes org-isolation and idempotency suites
  pnpm -C apps/web tsc --noEmit
  pnpm -C apps/web lint
  pnpm -C apps/web test:e2e   # Playwright: the full 13-step demo path
  scripts/check-bundle-secrets.sh   # service key absent from client bundle
```

The Playwright suite must walk the entire demo sequence and capture a screenshot at each step
into `docs/screenshots/` — this fills the README placeholders and proves the pages actually
render:

1. Sign in
2. Dashboard shows populated metrics
3. Filter to overdue
4. Open Sarah Johnson
5. Timeline shows 2 delivered reminders
6. Send reminder
7. Mock delivery success visible
8. Mark appointment scheduled
9. Dashboard KPI reflects the change
10. Import `patients-messy.csv`
11. Preview shows 327/320/5/2
12. Reminders page shows rules and performance
13. Revenue recovered renders with its definition

Also required: no console errors during the E2E run (assert on `page.on('console')`), and
`make demo-reset` returns the DB to seed state verified by a test.

## 12. Documentation deliverables

* `README.md` — overview, screenshots section, architecture, stack, structure, prerequisites,
  setup, env vars, DB setup, migrations, seed, run frontend, run backend, run tests, demo
  credentials, CSV format, reminder engine explanation, security model, privacy, deployment
  guidance. Every command copy-pasteable and verified to work from a clean clone. Assume the
  reader is still building development skills — do not skip steps an experienced engineer would
  consider obvious.
* `docs/ARCHITECTURE.md` — module boundaries, request lifecycle, tenancy enforcement, status
  state machine diagram.
* `docs/SECURITY.md` — per §9.
* `docs/DEMO_SCRIPT.md` — the literal talk track for demoing to a clinic owner: what to click,
  what to say, what number to point at, and the three questions they will ask (Is it HIPAA
  compliant? How do patients opt out? Where does the revenue number come from?) with honest
  answers.
* `.env.example` — every variable documented with a comment. No real credentials, ever.

## 13. Working agreement for the build

* Choose the simplest professional solution when ambiguous; do not expand scope.
* One approval gate: the Phase 0 plan. After that, proceed without stopping to ask.
* Never a single giant Python file. Layers: routers / services / repositories / models / schemas
  / database / security / jobs.
* Prefer boring, readable code over clever abstraction. This will be read by the person who
  commissioned it.
