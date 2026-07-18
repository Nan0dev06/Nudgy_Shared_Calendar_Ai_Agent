# Deploying Nudgy to Render (free)

This puts the backend on a public, always-reachable URL with a persistent
Postgres database, so connected accounts survive restarts.

Free-tier note: the web service sleeps after 15 minutes of no traffic and
takes ~1 minute to wake on the next request. Before a demo, open the URL once
to wake it. The free Postgres expires 30 days after creation (fine for the
hackathon).

## 1. Deploy the blueprint

1. Make sure `render.yaml` is pushed to GitHub (it is, in the repo root).
2. Render dashboard → **New** → **Blueprint**.
3. Connect the **nudgy** repo. Render reads `render.yaml` and shows a plan:
   one web service (`nudgy`) + one Postgres (`nudgy-db`).
4. It will prompt for the secret env vars (the `sync: false` ones). Paste in
   the values from your local `.env`:
   - `GROQ_API_KEY`
   - `GOOGLE_CLIENT_ID`
   - `GOOGLE_CLIENT_SECRET`
   - `GOOGLE_REDIRECT_URI` — you don't know the final URL yet, so put a
     placeholder for now (e.g. `https://nudgy.onrender.com/auth/google/callback`)
     and fix it in step 3.
   `SECRET_KEY` and `DATABASE_URL` are handled automatically — leave them.
5. **Apply**. First build takes a few minutes.

> **Model:** the blueprint sets `NUDGY_MODEL=llama-3.3-70b-versatile` — the
> strongest remaining Groq free model at tool calling. Its free limit is 100K
> tokens/day and 12K/minute, roughly five conversations before it rate-limits,
> so pace a demo. If you already deployed and the var is missing, check the
> `nudgy` service → **Environment** and add it by hand. `llama-3.1-8b-instant`
> has a bigger daily budget (500K) but is weaker at tools. (The old
> `llama-4-scout-17b-16e-instruct` was removed from Groq's catalog and 404s.)
> See [Staying inside the free tier](../README.md#staying-inside-the-free-tier).

## 2. Grab your URL

When the web service goes live, Render shows its URL near the top, like
`https://nudgy.onrender.com` (or `https://nudgy-xxxx.onrender.com` if the name
was taken). Copy it — call it `BACKEND` below.

## 3. Wire up Google OAuth for the deployed URL

The web-app login uses Google OAuth, which only allows pre-registered redirect
URLs. Add the deployed one:

1. [Google Cloud Console](https://console.cloud.google.com) → **APIs & Services**
   → **Credentials** → your OAuth client (the Web application one).
2. Under **Authorized redirect URIs**, add: `BACKEND/auth/google/callback`
   (e.g. `https://nudgy.onrender.com/auth/google/callback`). Save.
3. Back in Render → the `nudgy` service → **Environment** → set
   `GOOGLE_REDIRECT_URI` to that same `BACKEND/auth/google/callback`. Save
   (this triggers a redeploy).

## 4. Connect accounts on the live app

The deployed database starts empty. Open `BACKEND` in a browser, click
**Connect Google Calendar**, and log in with each test account so they're
stored in Postgres. (Their calendars already hold the seeded Beirut events;
re-run `python backend/scripts/seed_demo.py` locally anytime to refresh them —
it writes to the real Google calendars, independent of which database.)

## Redeploys

Push to `main` and Render auto-deploys. Tables are created automatically on
startup; your data in Postgres persists across deploys.
