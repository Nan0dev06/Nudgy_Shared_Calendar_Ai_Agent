# Deploying Nudgy (free tier, private beta)

This puts the backend on a public URL with a **managed Postgres that survives
deploys**, real email delivery, and error tracking — everything a handful of
real testers need.

Three free accounts, no card: **Neon** (database), **Render** (host), **Sentry**
(errors). Sentry is optional; the other two are not.

Free-tier facts worth knowing up front:
- The Render web service **sleeps after 15 minutes** idle and takes ~1 minute to
  wake. An uptime pinger on `/healthz` keeps it warm (step 6).
- Render's *own* free Postgres is **deleted 30 days** after creation — that's why
  the blueprint no longer creates one and we use Neon instead.
- Neon's free compute **auto-suspends when idle**, which is fine and is why
  `/healthz` never touches the database.

---

## 1. Create the database (Neon)

1. [neon.tech](https://neon.tech) → sign up → **Create project** (any name; pick
   the region closest to your testers).
2. On the project dashboard, **Connection string** → copy the URI. It looks like:
   `postgresql://user:password@ep-xxx.region.aws.neon.tech/neondb?sslmode=require`
3. Keep it somewhere safe for step 3. It contains a password — treat it like one.

**Verify it locally before deploying anything.** Put it in your local `.env` as
`DATABASE_URL=…`, then:

```bash
python backend/scripts/check_db.py
```

It connects, creates the schema, and prints a row count per table. If that
prints `OK`, the deploy will come up. Then **remove `DATABASE_URL` from your
local `.env` again** — otherwise local dev writes to the beta database instead
of `nudgy.db`.

> The app upgrades a legacy `postgres://` scheme automatically and forces
> `sslmode=require` when the URL has no explicit mode, so a plain paste is safe.

## 2. Generate the two key secrets

```bash
python -c "import secrets; print('SECRET_KEY=' + secrets.token_urlsafe(48))"
python -c "from cryptography.fernet import Fernet; print('TOKEN_ENCRYPTION_KEY=' + Fernet.generate_key().decode())"
```

`SECRET_KEY` signs session cookies and email links (changing it logs everyone
out). `TOKEN_ENCRYPTION_KEY` encrypts stored OAuth tokens — **changing it later
orphans every connected calendar**, so save it. Render generates `SECRET_KEY`
for you; `TOKEN_ENCRYPTION_KEY` you paste.

The app refuses to boot if `SECRET_KEY` is still the published dev default while
`DATABASE_URL` is set — that combination would mean forgeable sessions.

## 3. Deploy the blueprint (Render)

1. Push this branch to GitHub (Render reads `render.yaml` from the repo).
2. Render dashboard → **New** → **Blueprint** → connect the repo → it shows one
   web service, `nudgy`.
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

[sentry.io](https://sentry.io) → new project → platform **FastAPI** → copy the
**DSN** → paste it as `SENTRY_DSN` in Render. `SENTRY_ENVIRONMENT=production` is
already set by the blueprint, so beta errors never mix with local ones.

Credentials are stripped from every event before it is sent — share tokens,
OAuth codes, cookies, and request bodies (see
`backend/app/core/observability.py`). Leave `SENTRY_TRACES_SAMPLE_RATE` at `0`
unless you are specifically chasing latency; traces eat the free quota fast.

## 6. Keep it awake (optional)

Free Render services sleep after 15 minutes, and a tester's first request then
waits ~1 minute. Point any free uptime pinger
([UptimeRobot](https://uptimerobot.com), [cron-job.org](https://cron-job.org)) at
`BACKEND/healthz` every 10 minutes.

Use `/healthz`, **not** `/readyz`: `/healthz` answers from the process alone,
while `/readyz` opens a database connection and would keep Neon's compute
running around the clock.

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
