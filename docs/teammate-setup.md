# Onboarding a second developer

Everything a teammate needs to run and develop Nudgy **without touching the
owner's Render account**. Render only matters for the public deployment; all
development happens locally.

## What's shared vs. what each dev makes themselves

| Thing | Who provides it | Notes |
|---|---|---|
| The code | **Shared** — GitHub collaborator invite | This is the only access that has to be granted |
| `.gitignore`, `.env.example`, `render.yaml` | **In the repo** | Come with the clone; nobody creates their own |
| `.env` | **Each dev makes their own** | Gitignored, so it was never in the repo to begin with |
| Groq API key | **Each dev makes their own** (free) | Groq's free limits are per *organization*, so a shared key means a shared 100K/day budget |
| Google OAuth client (`GOOGLE_CLIENT_ID` / `_SECRET`) | **Each dev makes their own** | Keeps secrets from changing hands at all; see step 4 |
| Google test accounts | **Each dev's own** | Each account must be listed as a test user on that dev's own Google Cloud project |
| Render account | **Not needed** | Only the person who owns the live deploy needs one |

## Steps for the teammate

### 1. Fork the repo

The repo is public, so no access has to be granted — the teammate forks it
themselves: GitHub → the repo → **Fork**. Then:

```bash
git clone https://github.com/<their-username>/Nudgy_Shared_Calendar_Ai_Agent.git
cd Nudgy_Shared_Calendar_Ai_Agent
git remote add upstream https://github.com/<owner>/Nudgy_Shared_Calendar_Ai_Agent.git
```

Work on a branch, never on `main`:

```bash
git checkout -b <name>/<what-youre-doing>
```

Push to the fork (`origin`) and open a pull request against the owner's `main`.
Ticking **Allow edits by maintainers** on the PR lets the owner push fixes onto
the branch, which saves a lot of round-tripping.

Sync with upstream before starting anything new, so the fork doesn't drift:

```bash
git fetch upstream
git checkout main && git merge upstream/main
git push origin main
```

The owner's `main` is what Render auto-deploys, so a merged PR goes live.

### 2. Python environment

Python 3.12.

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 3. Groq API key (free, no card)

Sign up at <https://console.groq.com/keys>, create a key, keep it for step 5.

A separate key under the teammate's own Groq account means a separate daily
quota — worth it, because two people sharing one key will rate-limit each other
mid-demo.

### 4. Google OAuth credentials

Each dev creates their own OAuth client, so no secret ever changes hands.
It's free and takes about ten minutes.

In the [Google Cloud Console](https://console.cloud.google.com):

1. Create a new project.
2. **APIs & Services** → **Library** → enable the **Google Calendar API**.
3. **APIs & Services** → **Credentials** → **Create credentials** → **OAuth
   client ID** → *Web application*.
4. Under **Authorized redirect URIs**, add both:

   ```
   http://localhost:8765/
   http://localhost:8000/auth/google/callback
   ```

5. **OAuth consent screen / Audience** → **Test users** → add every Google
   account that will be used for testing. While the app is in Testing mode,
   only listed accounts can log in.
6. Copy the client ID and client secret for step 5.

Test calendar accounts are per-project, so the teammate needs a couple of
Google accounts of their own to test a group with — Nudgy needs at least two
connected calendars to intersect anything.

(Sharing one client between two devs also works — the localhost redirect URI is
already registered, so it needs no changes — but it means sending a client
secret over chat and keeping one consent screen's test-user list in sync. Not
worth it.)

### 5. `.env`

```bash
cp .env.example .env
```

Fill in:

```
GOOGLE_CLIENT_ID=...
GOOGLE_CLIENT_SECRET=...
GOOGLE_REDIRECT_URI=http://localhost:8000/auth/google/callback
GROQ_API_KEY=...
LLM_PROVIDER=groq
NUDGY_MODEL=llama-3.3-70b-versatile
```

`.env` is gitignored — it must never be committed. The local database
(`*.db`) and `backend/.tokens/` are gitignored too, so the teammate starts with
an empty database and connects accounts themselves.

### 6. Run it

```bash
uvicorn app.main:app --reload --app-dir backend
```

Open <http://localhost:8000>, click **Connect Google Calendar**, and log in
with each test account so they land in the local database.

Optional checks:

```bash
python -m pytest backend/tests -q          # unit tests, no network
python backend/scripts/check_freebusy.py   # proves OAuth + freebusy end to end
python backend/scripts/seed_app_data.py    # demo group/users
```

### 7. Frontend work

```bash
cd frontend
npm install
npm run dev      # Vite on :5173, proxies API calls to :8000
```

**Important team gotcha:** Render only runs `pip install` — it serves the
*committed* bundle in `backend/app/static/`. After any frontend change:

```bash
npm run build    # writes into backend/app/static/
```

and commit the build output along with the source.

Two people editing the frontend will conflict in `backend/app/static/`
regularly, and working from a fork makes it worse — branches live longer, so
the bundle drifts further before it merges. The fix is always the same: resolve
the conflicts in the **source** files, then rerun `npm run build` and commit
the regenerated bundle. Never hand-merge the built JS. Syncing from `upstream`
often keeps these small.

## Deployment

The teammate does **not** need Render to develop. When their pull request is
merged into the owner's `main`, the owner's Render service auto-deploys it.

If they want their own live URL, they create their own free Render account and
run the blueprint from the same repo — **New** → **Blueprint** → connect the
repo → Apply. It provisions a separate web service and Postgres, and asks for
their own `GROQ_API_KEY`, `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, and
`GOOGLE_REDIRECT_URI`. Their deployed URL's callback
(`https://<their-service>.onrender.com/auth/google/callback`) has to be added
to the **Authorized redirect URIs** of whichever OAuth client they're using.
Full walkthrough: [docs/deploy.md](deploy.md).

Adding the teammate to the *owner's* Render workspace is the other route, but
free/hobby workspaces are single-user — extra members require a paid team plan.
Check Render's current pricing before going that way; for a two-person project
it's usually not worth it.
