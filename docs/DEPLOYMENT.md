# Deploying the public demo

How to put ClinicRecall on the internet so a link goes straight to a working product.

Three free services, roughly 40 minutes the first time:

| Piece | Runs on | Why |
|---|---|---|
| Frontend (Next.js) | **Vercel** | Free, no meaningful cold start, gives you the URL you share |
| Database + auth | **Supabase Cloud** | The same Postgres and the same GoTrue auth you run locally |
| API (FastAPI) | **Render** | Free Docker hosting; see the cold-start note below |

> **This stays a demo.** It runs on synthetic records, every screen carries the "Demo Data —
> synthetic patients only" marker, and reminders are rendered and stored but never actually
> emailed. Do not put real patient information in it — see [`SECURITY.md`](./SECURITY.md).

## The cold-start problem, and how this handles it

Render's free plan spins a service down after about 15 minutes without traffic, and waking it
takes the better part of a minute. A reviewer clicking a link from a CV will not wait that long.

[`demo-keepalive.yml`](../.github/workflows/demo-keepalive.yml) pings `/health` every 10 minutes,
which is shorter than the idle timeout, so the service stays awake. The same workflow resets the
demo data hourly, because a public demo is a shared mutable object: the first visitor who marks
Sarah Johnson as scheduled changes what everyone after them sees.

GitHub's scheduled runners are best-effort and can run a few minutes late. That is fine for both
jobs, but it does mean the first visitor after a quiet stretch may occasionally wait for a wake-up.

**Two things will silently stop the schedule**, and both matter if this link is on a CV:

- GitHub disables scheduled workflows in a repository with **no commits for 60 days**, and emails
  you when it does. Any commit re-arms it.
- Scheduled workflows only run from the **default branch**, so the workflow file has to be on
  `main`.
If that matters, Render's Starter plan (about $7/month) removes the spin-down entirely and makes
the ping job unnecessary — the reset job is still worth keeping.

---

## 1. Supabase Cloud

1. Create a project at [supabase.com](https://supabase.com). Choose a strong database password and
   save it; you need it in the next step.
2. From **Project Settings → Database**, copy the **session pooler** connection string.
3. From **Project Settings → API**, copy the **Project URL**, the **anon** key, and the
   **service_role** key.

> The service-role key bypasses every row-level security policy. It goes into Render only. It must
> never reach Vercel, never be prefixed `NEXT_PUBLIC_`, and never be committed — `make verify`
> fails the build if it appears in the frontend bundle.

Apply the schema from your machine:

```bash
DATABASE_URL='postgresql+psycopg://postgres.<ref>:<password>@<host>:5432/postgres' uv --directory apps/api run alembic upgrade head
```

Then create the demo practice, the two accounts, and the 55 patients:

```bash
DATABASE_URL='...' SUPABASE_URL='https://<ref>.supabase.co' SUPABASE_SERVICE_ROLE_KEY='...' uv --directory apps/api run python -m app.seed
```

## 2. Render (the API)

1. **New → Blueprint**, point it at your repository. It reads [`render.yaml`](../render.yaml) and
   creates the service.
2. Fill in the six variables the blueprint marks `sync: false`:

   | Variable | Value |
   |---|---|
   | `DATABASE_URL` | The pooler string, with `postgresql+psycopg://` as the scheme |
   | `SUPABASE_URL` | `https://<ref>.supabase.co` |
   | `SUPABASE_ANON_KEY` | From Project Settings → API |
   | `SUPABASE_SERVICE_ROLE_KEY` | Same page — this service only |
   | `WEB_ORIGIN` | Your Vercel URL, no trailing slash (come back after step 3) |
   | `EXTRA_WEB_ORIGINS` | Optional; comma-separated, for Vercel preview URLs |

3. `JOB_TOKEN` and `UNSUBSCRIBE_TOKEN_SECRET` are generated for you. **Copy `JOB_TOKEN` now** —
   step 4 needs it.

Confirm it came up:

```bash
curl https://<your-service>.onrender.com/health
```

## 3. Vercel (the frontend)

1. **Add New → Project**, import the repository, and set the **root directory** to `apps/web`.
2. Add three environment variables:

   | Variable | Value |
   |---|---|
   | `NEXT_PUBLIC_SUPABASE_URL` | `https://<ref>.supabase.co` |
   | `NEXT_PUBLIC_SUPABASE_ANON_KEY` | The anon key |
   | `NEXT_PUBLIC_API_BASE_URL` | `https://<your-service>.onrender.com` |

   All three are public by design. The anon key is safe in a browser — it is governed by row-level
   security, which is exactly why the service-role key is not here.

3. Deploy, then go back to Render and set `WEB_ORIGIN` to the Vercel URL. Without this the browser
   blocks every API call on CORS.

## 4. The keepalive and reset workflow

In your repository, under **Settings → Secrets and variables → Actions**:

- **Variables** tab: add `DEMO_API_URL` = `https://<your-service>.onrender.com`
- **Secrets** tab: add `DEMO_JOB_TOKEN` = the `JOB_TOKEN` you copied from Render

Then run it once by hand — **Actions → Demo keepalive → Run workflow** — to confirm both jobs pass
before relying on the schedule.

## 5. Check it end to end

Open the Vercel URL and confirm:

- [ ] The sign-in page loads with the demo credentials already filled in.
- [ ] Signing in reaches a dashboard showing 55 patients and 8 overdue.
- [ ] Sarah Johnson opens as **Overdue** with two delivered reminders.
- [ ] **Send reminder** works and the timeline grows.
- [ ] The browser console is clean.

If sign-in works but every screen is empty, the frontend reached Supabase but not the API — check
`NEXT_PUBLIC_API_BASE_URL` and `WEB_ORIGIN`.

---

## What is different about the hosted demo

- **Reminders are rendered and stored but never sent.** `MockEmailProvider` is still the provider.
  You can open any reminder and read the exact message; nothing leaves the server. Implementing a
  real provider means writing one class behind the existing `EmailProvider` protocol.
- **The reminder job does not run on a schedule.** The seeded history covers the demo; the
  "Run reminder job" button in Settings triggers it on demand.
- **The data resets hourly**, so anything you change is temporary. That is deliberate.
- **`DEMO_MODE=true`** keeps the admin demo utilities available on a deployment that is otherwise
  fully production. Everything else about it — logging, error handling, security headers — behaves
  as production.

## Troubleshooting

| Symptom | Cause |
|---|---|
| First load takes ~60s | Render cold start; the keepalive workflow is not running or is not configured |
| CORS errors in the console | `WEB_ORIGIN` does not exactly match the Vercel origin, or has a trailing slash |
| Sign-in fails | `NEXT_PUBLIC_SUPABASE_URL` / anon key mismatch between Vercel and Render |
| Screens load but are empty | The API is unreachable or was never seeded |
| Reset workflow returns 404 | `DEMO_MODE` is not `true` on the Render service |
| Reset workflow returns 401 | `DEMO_JOB_TOKEN` does not match Render's `JOB_TOKEN` |
| Keepalive stopped running | 60 days without a commit; GitHub disabled the schedule — push anything to re-arm |
