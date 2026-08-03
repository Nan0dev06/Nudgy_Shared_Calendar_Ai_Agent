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
| Google OAuth client (`GOOGLE_CLIENT_ID` / `_SECRET`) | **Either** — reuse the owner's, or make a second Google Cloud project | Reusing is simpler; see step 4 |
| Google test accounts | **Shared** — same accounts, or the teammate adds their own | Each account must be listed as a test user on whichever Google Cloud project is in use |
| Render account | **Not needed** | Only the person who owns the live deploy needs one |

## Steps for the teammate

### 1. Get the repo

The owner: GitHub → repo → **Settings** → **Collaborators** → **Add people** →
the teammate's GitHub username. The teammate accepts the emailed invite, then:

```bash
git clone https://github.com/<owner>/Nudgy_Shared_Calendar_Ai_Agent.git
cd Nudgy_Shared_Calendar_Ai_Agent
```

Work on a branch, not `main`:

```bash
git checkout -b <name>/<what-youre-doing>
```

`main` is what Render auto-deploys, so pushing straight to it redeploys the
live app.

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

Two options.

**Option A — reuse the owner's OAuth client (recommended).** The client ID and
secret work from any machine; the redirect URI
`http://localhost:8000/auth/google/callback` is already registered on it. The
owner sends the teammate `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` over
something private (not a commit, not a public channel), and adds the teammate's
Google account as a test user: [Google Cloud Console](https://console.cloud.google.com)
→ **APIs & Services** → **OAuth consent screen / Audience** → **Test users**
→ **Add users**.

**Option B — the teammate makes their own Google Cloud project.** Needed only
if they want their own calendar test accounts and their own consent screen:
new project → enable the **Google Calendar API** → **Credentials** → **Create
credentials** → **OAuth client ID** → *Web application* → add both redirect
URIs:

```
http://localhost:8765/
http://localhost:8000/auth/google/callback
```

Then add every test Google account under **Test users**.

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

and commit the build output along with the source. Two people editing the
frontend will conflict in `backend/app/static/` regularly; the fix is to rerun
`npm run build` after resolving the source-file conflicts and commit the
regenerated bundle rather than hand-merging it.

## Deployment

The teammate does **not** need Render to develop. When their branch is merged
into `main`, the owner's Render service auto-deploys it.

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
