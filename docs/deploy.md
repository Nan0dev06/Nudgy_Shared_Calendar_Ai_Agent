# Deploying Orbi to Render (free)

This puts the backend on a public, always-reachable URL with a persistent
Postgres database — so the Calendar add-on works without a tunnel, and
connected accounts survive restarts.

Free-tier note: the web service sleeps after 15 minutes of no traffic and
takes ~1 minute to wake on the next request. Before a demo, open the URL once
to wake it. The free Postgres expires 30 days after creation (fine for the
hackathon).

## 1. Deploy the blueprint

1. Make sure `render.yaml` is pushed to GitHub (it is, in the repo root).
2. Render dashboard → **New** → **Blueprint**.
3. Connect the **orbi** repo. Render reads `render.yaml` and shows a plan:
   one web service (`orbi`) + one Postgres (`orbi-db`).
4. It will prompt for the secret env vars (the `sync: false` ones). Paste in
   the values from your local `.env`:
   - `GROQ_API_KEY`
   - `GOOGLE_CLIENT_ID`
   - `GOOGLE_CLIENT_SECRET`
   - `ADDON_SHARED_SECRET`
   - `GOOGLE_REDIRECT_URI` — you don't know the final URL yet, so put a
     placeholder for now (e.g. `https://orbi.onrender.com/auth/google/callback`)
     and fix it in step 3.
   `SECRET_KEY` and `DATABASE_URL` are handled automatically — leave them.
5. **Apply**. First build takes a few minutes.

## 2. Grab your URL

When the web service goes live, Render shows its URL near the top, like
`https://orbi.onrender.com` (or `https://orbi-xxxx.onrender.com` if the name
was taken). Copy it — call it `BACKEND` below.

## 3. Wire up Google OAuth for the deployed URL

The web-app login uses Google OAuth, which only allows pre-registered redirect
URLs. Add the deployed one:

1. [Google Cloud Console](https://console.cloud.google.com) → **APIs & Services**
   → **Credentials** → your OAuth client (the Web application one).
2. Under **Authorized redirect URIs**, add: `BACKEND/auth/google/callback`
   (e.g. `https://orbi.onrender.com/auth/google/callback`). Save.
3. Back in Render → the `orbi` service → **Environment** → set
   `GOOGLE_REDIRECT_URI` to that same `BACKEND/auth/google/callback`. Save
   (this triggers a redeploy).

## 4. Connect accounts on the live app

The deployed database starts empty. Open `BACKEND` in a browser, click
**Connect Google Calendar**, and log in with each test account so they're
stored in Postgres. (Their calendars already hold the seeded Beirut events;
re-run `python backend/scripts/seed_demo.py` locally anytime to refresh them —
it writes to the real Google calendars, independent of which database.)

## 5. Point the Calendar add-on at Render (no more tunnel)

Now that the backend has a stable public URL, the add-on doesn't need the
localhost tunnel at all. In `addon/`:

- `Code.gs`: `var BACKEND_URL = 'BACKEND';` (no trailing slash)
- `appsscript.json`: `"urlFetchWhitelist": ["BACKEND"]`
- Make sure the add-on's Script Property `ADDON_SHARED_SECRET` equals the
  value you set on Render.

Then follow `addon/README.md` from step 6 (test deployment) onward.

## Redeploys

Push to `main` and Render auto-deploys. Tables are created automatically on
startup; your data in Postgres persists across deploys.
