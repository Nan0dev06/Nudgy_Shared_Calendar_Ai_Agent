# Deploying Nudgy (free tier, private beta)

This puts the backend on a public URL with a **managed Postgres that survives
deploys**, real email delivery, and error tracking — everything a handful of
real testers need.

Three free accounts, no card: **Neon** (database), **Render** (host), **Sentry**
(errors). Sentry is optional; the other two are not.

Free-tier facts worth knowing up front:
- The Render web service **sleeps after 15 minutes** idle and takes ~1 minute to
  wake. **Do not fight this with an uptime pinger** — step 6 explains why that
  trades a slow first request for a dead database halfway through every month.
- Render's *own* free Postgres is **deleted 30 days** after creation — that's why
  the blueprint no longer creates one and we use Neon instead.
- Neon's free compute is **100 CU-hours per project per month** and auto-suspends
  after 5 minutes idle. The suspend is not a nuisance to be worked around; it is
  what keeps you inside the quota. Again: step 6.

**Regions: pick one and use it for all three accounts.** Every request makes
several app↔database round trips and only one user↔app hop, so the database
belongs next to the *host*, not next to your users. The current beta is
Frankfurt throughout: Render Frankfurt, Neon `eu-central-1`, Sentry EU.

---

## 1. Generate the two key secrets

Do this **first** — step 2 will not run without `SECRET_KEY`.

```bash
python -c "import secrets; print('SECRET_KEY=' + secrets.token_urlsafe(48))"
python -c "from cryptography.fernet import Fernet; print('TOKEN_ENCRYPTION_KEY=' + Fernet.generate_key().decode())"
```

`SECRET_KEY` signs session cookies and email links (changing it logs everyone
out). `TOKEN_ENCRYPTION_KEY` encrypts stored OAuth tokens — **changing it later
orphans every connected calendar**, so save it somewhere permanent before you
close the terminal. Render generates its own `SECRET_KEY` for the deploy;
`TOKEN_ENCRYPTION_KEY` you paste.

Put the `SECRET_KEY` line in your local `.env` now. The app refuses to boot when
`SECRET_KEY` is still the published dev default while `DATABASE_URL` is set —
that combination would mean session cookies anyone could forge. The check lives
in `core/config.py` at import time, so with a `DATABASE_URL` and no `SECRET_KEY`
in `.env` it is not just the server that refuses to start: **`check_db.py` and
the whole test suite fail to collect** with that same `RuntimeError`.

## 2. Create the database (Neon)

1. [neon.tech](https://neon.tech) → sign up → **Create project** → region
   `eu-central-1` (Frankfurt), newest Postgres version offered. Nothing in the
   codebase is version-sensitive.
2. On the project dashboard, **Connection string** → copy the URI. It looks like:
   `postgresql://user:password@ep-xxx.region.aws.neon.tech/neondb?sslmode=require`
3. Keep it somewhere safe for step 3. It contains a password — treat it like one.

Say **no to Neon Auth** if it is offered. Nudgy has its own identity layer
(email+password, magic links, verification, reset, sessions, plus Google and
Microsoft OAuth with Fernet-encrypted tokens); adopting Neon's would mean
rewriting that and moving the user table into a vendor's schema.

**Verify it locally before deploying anything.** Put it in your local `.env` as
`DATABASE_URL=…` alongside the `SECRET_KEY` from step 1, then:

```bash
python backend/scripts/check_db.py
```

It connects, creates the schema, and prints a row count per table. If that
prints `OK`, the deploy will come up. Then **remove `DATABASE_URL` from your
local `.env` again** — otherwise local dev writes to the beta database instead
of `nudgy.db`. Leave `SECRET_KEY` in place; it is harmless and stops your local
sessions being invalidated on every restart. Put `DATABASE_URL` back only when
you want to re-run `check_db.py`.

> The app upgrades a legacy `postgres://` scheme automatically and forces
> `sslmode=require` when the URL has no explicit mode, so a plain paste is safe.

## 3. Deploy the blueprint (Render)

1. Push this branch to GitHub (Render reads `render.yaml` from the repo).
2. Render dashboard → **New** → **Blueprint** → connect the repo → it shows one
   web service, `nudgy`. Set its region to **Frankfurt**, matching Neon.
3. It prompts for every `sync: false` variable. Paste:

   | Variable | Value |
   |---|---|
   | `DATABASE_URL` | the Neon string from step 1 |
   | `TOKEN_ENCRYPTION_KEY` | the Fernet key from step 2 |
   | `GROQ_API_KEY` | from `console.groq.com/keys` |
   | `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | your local `.env` values |
   | `GOOGLE_REDIRECT_URI` | placeholder for now — fixed in step 4 |
   | `APP_BASE_URL` | placeholder for now — fixed in step 4 |
   | `SMTP_HOST` / `SMTP_USER` / `SMTP_PASSWORD` | `smtp.gmail.com`, your Gmail, your 16-char App Password |
   | `SENTRY_DSN` | from step 5, or leave blank |
   | `MS_CLIENT_ID` / `MS_CLIENT_SECRET` / `MS_REDIRECT_URI` | your Azure app registration, or blank for Google-only |

4. **Apply.** The first build takes a few minutes.

> **Model:** the blueprint sets `NUDGY_MODEL=llama-3.1-8b-instant` (500K
> tokens/day). `llama-3.3-70b-versatile` is noticeably better at tool calling
> but only 100K/day — roughly five conversations before it rate-limits. Switch
> in the service's **Environment** tab if beta chats feel dumb.

## 4. Point the OAuth redirects at the deployed URL

When the service is live Render shows its URL, e.g. `https://nudgy.onrender.com`
— call it `BACKEND`.

1. **Google Cloud Console** → APIs & Services → Credentials → your Web
   application client → **Authorized redirect URIs** → add
   `BACKEND/auth/google/callback` → Save.
2. **Azure Portal** (only if using Microsoft) → App registrations → your app →
   Authentication → add the Web redirect URI `BACKEND/auth/microsoft/callback`.
3. Render → the `nudgy` service → **Environment** → set:
   - `GOOGLE_REDIRECT_URI` = `BACKEND/auth/google/callback`
   - `MS_REDIRECT_URI` = `BACKEND/auth/microsoft/callback`
   - `APP_BASE_URL` = `BACKEND`  ← **email links point here; get it right**
   Save (this redeploys).
4. Google Cloud Console → **OAuth consent screen** → publishing status stays
   **Testing**, and every beta tester's Google address goes in **Test users**
   (100 max). Without that they get "app not verified" and are blocked.

## 5. Error tracking (Sentry, optional but recommended)

[sentry.io](https://sentry.io) → sign up → **choose the EU data region** → new
project → platform **FastAPI** → copy the **DSN** → paste it as `SENTRY_DSN` in
Render. `SENTRY_ENVIRONMENT=production` is already set by the blueprint, so beta
errors never mix with local ones.

**The region is chosen once, when the organisation is created, and cannot be
changed afterwards** — moving means starting a new org and losing the history.
EU because the testers are in Lebanon/MENA/Europe: if any of them is in the EEA
or UK you are a GDPR controller for their data, and keeping it in Frankfurt
removes the international-transfer question rather than leaving it to be argued.
Latency is irrelevant either way — the SDK ships events in the background.

Credentials are stripped from every event before it is sent — share tokens,
OAuth codes, cookies, and request bodies (see
`backend/app/core/observability.py`). Leave `SENTRY_TRACES_SAMPLE_RATE` at `0`
unless you are specifically chasing latency; traces eat the free quota fast.

## 6. Do NOT keep it awake

An earlier version of this document told you to point an uptime pinger at
`/healthz` every 10 minutes to dodge the cold start. **Don't.** Doing that
exhausts the Neon free quota around the 17th of every month, and the reasoning it
gave — "`/healthz` never touches the database" — was only half the picture.

The arithmetic:

- Neon Free is **100 CU-hours per project per month**, the smallest compute is
  **0.25 CU**, and compute auto-suspends after **5 minutes idle** (not
  disableable on Free). 100 ÷ 0.25 = **400 wall-clock hours** of DB-active time
  per month. A month is ~730 hours.
- The pinger keeps the *process* alive, and the process runs `jobs/plan_ticker.py`
  — which opens a session every **60 seconds**, awake or not. Neon therefore
  never reaches 5 minutes idle and never suspends.
- 730 > 400. When the quota is gone Neon suspends the compute: live connections
  drop, new ones are refused, until the next billing cycle. The data survives;
  the app does not.

So the free tiers only compose if the web service is *allowed* to sleep. That is
the trade being made deliberately: a ~1 minute cold start for a tester who
arrives first, in exchange for a database that stays up all month.

**What sleeping actually costs**, read from the code rather than assumed:

- **Deadlines resolve late, not never.** `resolve_deadline` only tests
  `now >= deadline`, so one that passed during sleep is resolved on the first
  tick after any request wakes the service.
- **Reminders can be missed outright.** `reminder_due` returns False once the
  deadline is past, so a nudge whose window elapsed during sleep never sends.
  This is the one real loss, and the reason to eventually leave the free tier.
- **The mirror is briefly stale on wake, but no longer for a whole interval.**
  Both job loops now run once immediately and sleep afterwards
  (`jobs/plan_ticker.py`, `jobs/calendar_sync.py`), so a cold start costs the
  boot time, not boot time plus 60s / 5 min.

**When you outgrow this**, in increasing order of effort:

1. **Render Starter, $7/month** — always on, no workaround, no grey area. Then
   also set `PLAN_TICK_SECONDS=900` and `CALENDAR_SYNC_SECONDS=900` in the
   environment, or the always-on process walks straight into the Neon quota
   above. At a 15-minute cadence Neon suspends between ticks, giving roughly
   64 CU-hours/month; at 10 minutes it is ~93 and too close to the line.
   Deadlines are set in hours, so 15-minute resolution costs nothing.
2. **A host whose free tier is genuinely always-on** (Northflank's sandbox is
   the sanctioned one: 2 services, 1 persistent database, 2 cron jobs, no card).
   Same `900` caveat — or use its bundled Postgres and drop Neon entirely, which
   removes the CU-hour ceiling but takes on an undocumented sandbox limit.
3. **Neon Launch** raises the compute quota if you would rather pay for the
   database than the host.

## 7. Smoke-test the live app

In a browser at `BACKEND`:

1. `BACKEND/healthz` → `{"status":"ok"}`; `BACKEND/readyz` → `{"database":"ok"}`.
   A 503 from `/readyz` means the app is up but `DATABASE_URL` is wrong.
2. Register with a real address → the verification email arrives (check spam;
   Gmail SMTP has mediocre deliverability) → the link logs you in.
3. **Connect Google Calendar** → consent → Settings → Calendars shows it.
4. Create a group, start a plan, vote. A second browser (or phone) on the same
   group should see the vote land within a second (SSE).

## Redeploys

Push to the branch Render is watching and it deploys automatically. Schema
changes apply themselves on startup (`create_all` + the in-place column/index
migrations in `backend/app/db/session.py`); your Postgres data persists.

**The frontend bundle is committed, not built on the server** — Render only runs
`pip install`. After any frontend edit: `cd frontend && npm run build`, then
commit `backend/app/static/`.
