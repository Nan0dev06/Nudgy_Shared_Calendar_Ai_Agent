# Nudgy — agentic group scheduling

Conversational AI agent that finds when a group can meet and where. Users
connect Google Calendar, join a group, and ask an LLM agent (via chat) to
intersect everyone's live free/busy, propose a time + real venue, run a
two-stage vote cascade (interest → time), and book to Google once the host
decides. Privacy by design: only `freebusy` ranges — never event details.

## Stack
- **Backend:** Python 3.12 + FastAPI, SQLAlchemy. SQLite locally, Postgres on
  Render (via `DATABASE_URL`). Agent uses Groq (free tier, OpenAI-compatible).
- **Frontend:** React + Vite in `frontend/`; builds into `backend/app/static/`,
  which FastAPI serves at `/`.

## Layout
`backend/app/{api,agent,tools,db,auth}` · `frontend/src/` · `render.yaml`
(one-click deploy) · `docs/`.

## Run & test
- Backend: `uvicorn app.main:app --reload --app-dir backend`
- Frontend dev: `cd frontend && npm run dev` (proxies to :8000)
- Rebuild bundle after frontend edits: `cd frontend && npm run build` (commit it)
- Tests: `python -m pytest backend/tests -q`
- Seed demo data: `python backend/scripts/seed_app_data.py`

## Gotchas
- Chat needs a working `NUDGY_MODEL`; `llama-3.3-70b-versatile` is current.
- Render only runs `pip install` — it serves the **committed** static bundle.
