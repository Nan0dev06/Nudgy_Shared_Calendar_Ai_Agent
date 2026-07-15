# Orbi — Google Calendar Add-on

A thin client: this Apps Script project has **no logic of its own**. Every
message goes to `POST /addon/chat` on your existing FastAPI backend, which
runs the exact same agent loop (`app/agent/loop.py`) the web app uses. The
only thing unique to the add-on is the CardService sidebar UI.

## Why you need a public URL

Apps Script code runs on **Google's servers**, not your laptop — it cannot
reach `http://localhost:8000`. You need *some* public HTTPS URL pointing at
your backend. Two options, easiest first:

### Option A — a free tunnel (for testing/demo, ~5 min)

No account needed. Run this in your **own terminal** (PowerShell or Git
Bash) — not through Claude, since it needs to run for as long as you're
testing:

```
ssh -R 80:127.0.0.1:8000 nokey@localhost.run
```

Make sure the backend is running first (`uvicorn app.main:app --app-dir backend --port 8000`).
After a few seconds you'll see a line like:

```
xxxxxxxxxxxxxx.lhr.life tunneled with tls termination, https://xxxxxxxxxxxxxx.lhr.life
```

That `https://...lhr.life` URL is your public backend address. It changes
every time you restart the tunnel (free tier), so you'll re-paste it into
the two places below whenever you restart it.

### Option B — deploy the backend somewhere free and stable

Render, Railway, or Fly.io all have free web-service tiers. More setup, but
you get a URL that never changes. Worth doing once you're past testing and
want the add-on reliably reachable — ask me and I'll walk through whichever
host you pick.

## One-time setup

1. **Get your public URL** (Option A or B above).

2. **Create the Apps Script project**: go to
   [script.google.com](https://script.google.com) → New project.

3. **Paste in the two files** from this `addon/` folder:
   - Replace the default `Code.gs` content with this repo's `Code.gs`.
   - In the project, click the gear icon → **Show "appsscript.json" manifest
     file in editor** → paste in this repo's `appsscript.json`.

4. **Set the shared secret** (Project Settings → Script Properties → Add
   script property):
   - Key: `ADDON_SHARED_SECRET`
   - Value: the same string as `ADDON_SHARED_SECRET` in your `.env`

5. **Point the code at your public URL** — edit two places:
   - `Code.gs`: `var BACKEND_URL = 'https://your-url-here';` (no trailing slash)
   - `appsscript.json`: `"urlFetchWhitelist": ["https://your-url-here"]`

6. **Test deploy**: Deploy → Test deployments → install it for your own
   Google account. Google may show an unverified-app warning since this
   isn't published — that's expected for a hackathon demo; click through
   ("Advanced" → "Go to Orbi (unsafe)") since it's your own script.

7. **Open Google Calendar** in a browser, look for the Orbi icon in the
   right-hand side panel. First run: it asks for the email you connected in
   the Orbi web app (your test account), then you can chat with Orbi right
   there.

## What the add-on does and doesn't do

- **Does**: chat with Orbi — find times, propose slots, create polls, check
  poll status, book once approved. Exactly the same tools and rules as the
  web app, because it's the same backend code.
- **Doesn't**: manage groups (create/join) — that stays in the web app. If a
  connected user has no group yet, Orbi tells them to set one up there
  first, rather than duplicating that UI in a sidebar.

## Restarting after a tunnel restart

Every time you restart the `ssh -R ...` tunnel you get a new `.lhr.life`
URL. Update `BACKEND_URL` in `Code.gs` and `urlFetchWhitelist` in
`appsscript.json` to match, save, and the same test deployment picks it up
immediately — no need to redeploy.
