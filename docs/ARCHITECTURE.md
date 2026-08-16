# Architecture

> **Status:** written incrementally as the build proceeds; completed in phase 12. What is here
> now is accurate — it is simply not yet complete.

## Module boundaries

The backend has four layers, and each may only call the one below it.

```
  routers/        HTTP. Parse the request, authorise it, delegate, shape the response.
      ↓           Never issues a query. Never contains a business rule.
  services/       Domain rules. "Is this patient overdue?" "Should this reminder fire?"
      ↓           Knows nothing about HTTP. Knows nothing about SQL.
  repositories/   Database access. Every method takes organization_id as its first argument.
      ↓
  models/         SQLAlchemy table definitions.
```

The rule is enforceable by reading a file: if you find a `select()` in a router, or a `Response`
in a service, the layering has been broken.

Two supporting packages sit alongside rather than inside this stack:

* `core/` — configuration, database session management, security primitives. Cross-cutting.
* `email/` — message rendering and the pluggable delivery provider.
* `jobs/` — the reminder processor, which is a service with a scheduler-shaped entry point.

## Why synchronous SQLAlchemy

FastAPI is frequently written with `async def` endpoints and an async database driver. This
project uses ordinary synchronous SQLAlchemy, and endpoints that need the database are plain
`def` functions, which FastAPI runs in a thread pool.

The reason is [SPEC §13](./SPEC.md#13-working-agreement-for-the-build): prefer boring, readable
code. Async SQLAlchemy adds a class of mistakes — forgotten `await`s, greenlet errors on lazy
loads, session lifetimes that behave differently under concurrency — in exchange for throughput
this application does not need. A single clinic has tens of thousands of patients at the outside,
and the demo has fifty-five.

## Resolved ambiguities

The spec leaves a handful of decisions open. They are recorded here rather than by editing
[`SPEC.md`](./SPEC.md), which is kept verbatim as the contract.

| # | Question the spec leaves open | Resolution |
|---|-------------------------------|------------|
| 1 | How is "visit completed within the last 30 days" detected, given there is no `completed_at` column? | `COMPLETED` is `last_annual_visit_date >= today - 30 days`. A consequence worth knowing: importing a patient seen three weeks ago shows them as COMPLETED, which is honest and matches how staff think about it. |
| 2 | Manual sends must not collide with the reminder idempotency index. | `reminder_events.reminder_rule_id` is nullable and a `source` enum (`RULE` / `MANUAL` / `TEST`) is added. Manual sends carry a null rule ID, so the unique index cannot fire on them. They are rate-limited to one per patient per hour instead. |
| 3 | §4.1's activity type list has no entry for opting out, but §6.5 requires the unsubscribe endpoint to write an activity event. | Added `PATIENT_OPTED_OUT`. |
| 4 | "Appointments recovered" needs the moment an appointment was *marked* scheduled. `scheduled_for` is the appointment date, which is not the same thing. | The `APPOINTMENT_SCHEDULED` activity event's `created_at` is the recovery timestamp. |
| 5 | How often is the cached `patients.status` recomputed? | After every mutation, on API startup, and at the start of the reminder job. This matters for demos left running overnight: statuses self-correct rather than silently going stale. |

## Request lifecycle

_Arrives with phase 4, alongside the authentication dependency it describes._

## Tenancy enforcement

_Arrives with phase 2, alongside the repository layer it describes._

## Status state machine

_Arrives with phase 3, alongside `RecallService`._
