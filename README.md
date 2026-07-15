# Orbi — Agentic Group Scheduling Assistant

**Hackathon project · July 2026 · Team of 2**

Orbi is a conversational AI agent that finds when a group can *actually* meet — and where. Users connect their Google Calendars, join a group, and ask Orbi things like:

> "Find a time this week when everyone's free and suggest somewhere to meet."

Orbi cross-references everyone's live availability, proposes a time and venue with its reasoning, runs a poll, and — once the group approves — books the event on a shared calendar. All through chat with a floating orb in the app.

---

> ## Status — July 2026
>
> **Phases 1–3 are complete and verified end-to-end**: live `freebusy` across real
> Google Calendars → common-slot intersection → in-app poll → threshold rule →
> a **real booking** written to every member's calendar.
>
> - **Live deployment:** <https://orbi-1jgh.onrender.com> (Render free tier — the
>   first request after it's been idle can take ~50s to wake up).
> - **Model:** Llama 3.3 70B on Groq's free tier (no paid APIs anywhere).
> - **Remaining:** Phase 4 (venue suggestions) is the only optional piece left.

---

## The Problem

Groups with shared calendars still can't schedule. Manually cross-referencing five calendars is tedious, and raw free/busy data doesn't answer the real questions:

- **"When is everyone free?"** — requires intersecting N calendars, in N timezones, at query time.
- **"When does everyone want to meet?"** — requires asking people and honoring their answers.
- **"Where should we go?"** — requires knowing roughly where the group will be.

Existing tools (Calendly, Doodle) solve slices of this with forms and links. Orbi solves it as a conversation with an agent that does the legwork.

## What Orbi Does

1. You talk to Orbi in natural language.
2. Orbi fetches **live** free/busy data for every group member at the moment you ask.
3. It intersects busy blocks to find common free windows, filtered to sensible hours and durations.
4. It anchors a location using only the locations members **explicitly typed** into their own calendar events near the candidate slot, and searches for a real venue nearby.
5. It proposes a slot + venue **with its reasoning spelled out**.
6. On your confirmation, it polls the group. Votes are evaluated against a strict rule (see below).
7. Approved → Orbi writes the event to a shared Google Calendar. Rejected → Orbi re-plans and proposes an alternative.

## Agent Architecture

This is the core of the project: a **real multi-step agentic loop**, not a single LLM call. Orbi is an LLM (Llama 3.3 70B via Groq's free tier by default; Ollama/OpenAI supported via the same OpenAI-compatible code path) driving a set of backend tools via native tool/function calling.

### Context injection

Every turn, the backend injects into the model's context:

- **The current datetime (UTC + user's timezone)** — the model doesn't know what time it is; all "this week" / "tomorrow" reasoning is relative to this injected "now".
- The user's identity and group membership.

### Tools

Built and wired into the agent (Phases 1–3):

| Tool | What it does |
|---|---|
| `get_group_members` | Resolve the user's group → member list + calendar connection status |
| `find_meeting_slots` | Live `freebusy.query` for all members, then intersect busy blocks → common free windows, filtered to reasonable local hours and the requested duration. Returns **only busy time ranges** — never titles or details. The intersection math is a pure, unit-tested function |
| `create_poll` | Propose a confirmed slot to the group as an in-app poll |
| `get_poll_status` | Read the current tally and re-evaluate the decision rule (approved / rejected / pending) |
| `book_meeting` | Write the **approved** poll's event to every member's calendar via Google invites. Refuses anything not approved |

Planned for Phase 4 (venue suggestions, not yet built):

| Tool | What it will do |
|---|---|
| `get_event_locations` | Locations users explicitly typed into their own events adjacent to a candidate slot → geographic anchor |
| `search_venues` | Real Places API call near the anchor. No results → Orbi says so. **It never invents a venue** |

### The decision loop (propose → observe → decide → commit or re-plan)

```
user request
   │
   ▼
┌─ AGENTIC LOOP (LLM + tools) ────────────────────────────┐
│  resolve group → fetch live free/busy → intersect →     │
│  filter hours → anchor location → search venues         │
│         │                                               │
│         ▼                                               │
│  PROPOSE slot + venue, with reasoning, to user          │
│         │ user confirms                                 │
│         ▼                                               │
│  CREATE POLL → group members vote yes/no                │
│         │                                               │
│         ▼                                               │
│  EVALUATE votes against threshold rule                  │
│    ├─ approved  → COMMIT: write event to shared cal     │
│    └─ rejected  → RE-PLAN: propose alternative slot ────┼──▶ back to top
└──────────────────────────────────────────────────────────┘
```

Every tool call is logged (tool name, inputs, outputs) so the loop is visible and debuggable live.

### Poll rules (exact)

- Book **only if** zero NO votes **and** at least N YES votes (N configurable per poll).
- Any NO vote → do not book; Orbi offers to find an alternative.
- Timeout with missing votes → Orbi asks the requester: proceed, nudge, or re-plan.
- **Never** silently book onto the calendar of someone who declined.

## Privacy by Architecture

This is a design principle, not a feature:

- **Orbi cannot see event details.** Availability comes exclusively from Google Calendar's `freebusy` endpoint, which returns only busy time ranges — no titles, no descriptions, no attendees. Event contents never enter the system or the model's context.
- **Orbi cannot reveal what it does not have** — and it never fabricates. If you ask why someone is busy, the honest and only answer is: *"Nour is busy then."* There is no deception logic anywhere in the codebase.
- **Location comes only from what users typed.** The single exception to "no event details" is the location field of a user's *own* events, used only to anchor venue search. No tracking, no movement inference, no location history.

## Scope Guard

Orbi does group scheduling and meetup planning. Nothing else. Asked to write an essay or discuss the weather, it politely declines and restates what it can do. Enforced in the system prompt and verified against injection-style prompts.

## Tech Stack

| Layer | Choice |
|---|---|
| Backend | Python 3.11+, FastAPI |
| Agent | Llama 3.3 70B via Groq (free tier) with native tool calling; Ollama/OpenAI swappable via `LLM_PROVIDER` |
| Calendar | Google Calendar API — OAuth 2.0, `calendar.readonly` + `calendar.events` scopes, `freebusy.query` |
| Venues | Google Places API (Phase 4) |
| Frontend | React (teammate's branch). Backend exposes a documented REST API with example request/response bodies for every endpoint. A minimal scaffold UI ships in `backend/app/static/` for testing |
| Storage | **SQLite** locally, **Postgres** in production — auto-selected by the `DATABASE_URL` env var (Render injects it). Same code, no migrations |
| Hosting | Render (free tier) via a one-click `render.yaml` blueprint that also provisions the Postgres database |
| Timezones | Everything stored in UTC; converted to each user's local timezone at display time. Unit-tested |

### Repository layout

```
backend/
  app/
    main.py            # FastAPI app
    core/config.py     # env-driven settings (one place for all config)
    auth/              # Google OAuth flow + token refresh
    agent/             # agent loop, system prompt, tool definitions
    tools/             # freebusy, slot intersection, poll rules, booking
    db/                # SQLAlchemy models + repo + session (users, groups, polls, votes)
    api/               # REST endpoints (auth, groups, polls, chat)
    static/            # minimal scaffold UI (Orbi orb + chat)
  scripts/
    check_freebusy.py  # Phase 1 CLI proof: OAuth + freebusy + intersection
    check_phase3.py    # Phase 3 proof: poll → votes → rule → real booking
    seed_demo.py       # Populate test calendars with realistic Beirut events
    connect_account.py # One-time OAuth connect per test account (CLI)
  tests/               # slot math + poll rule unit tests
docs/
  api.md               # Endpoint reference with example bodies
  deploy.md            # Render deployment walkthrough
render.yaml            # One-click blueprint: web service + free Postgres
```

## Setup

> Detailed steps land with the code. Outline:

1. **Google Cloud project**: enable Calendar API (+ Places API later), create OAuth 2.0 credentials (web application), add test-account emails as test users.
2. **Groq API key** (free) from console.groq.com/keys.
3. Copy `.env.example` → `.env`, fill in `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REDIRECT_URI`, `GROQ_API_KEY`. Secrets are never committed.
4. `pip install -r requirements.txt`
5. Phase 1 check: `python scripts/check_freebusy.py` — proves OAuth + freebusy + intersection across the test accounts before any UI exists.
6. `uvicorn app.main:app --reload` and open the app.

### Demo safety

`scripts/seed_demo.py` populates the test calendars with realistic events (with locations, in Beirut) so the full flow demos reliably without depending on live third-party state.

### Deploy (optional)

The backend runs anywhere; for a public, always-on URL there's a one-click
Render blueprint (`render.yaml`) that provisions a free Postgres database
alongside the web service. Locally it uses SQLite; set `DATABASE_URL` (Render
does this automatically) to use Postgres. Full walkthrough in
[docs/deploy.md](docs/deploy.md).

## Build Order

| Phase | Deliverable | Status |
|---|---|---|
| 1 | Google OAuth + freebusy across test accounts + intersection logic, proven via CLI script | ✅ |
| 2 | Conversational agent + Orbi orb UI: open-ended prompt → candidate slots → reasoning in natural language | ✅ (agent chat needs the free `GROQ_API_KEY`) |
| 3 | Poll → vote collection → threshold rule → commit to shared calendar or re-plan | ✅ (verified end-to-end incl. a real booking) |
| 4 | Venue suggestion via Places API (only if time permits) | ⬜ |
| + | **Deployed** to Render with Postgres | ✅ (bonus, beyond the original plan) |

Each phase must work before the next begins.

## Handled Correctly (by design)

- **Timezones**: UTC everywhere internally; per-user local display; members may be in different zones; unit tests on the intersection + conversion logic.
- **OAuth token refresh**: expiry handled explicitly — no silent failures.
- **No common slot**: Orbi says so clearly and offers the closest partial options ("4 of 5 are free Thursday 5pm").
- **Members without a connected calendar**: surfaced explicitly, never silently ignored.
- **Tool-call logging**: every agent step logged for live debugging and demoing the loop.

## Roadmap / Not Yet Built

Phases 1–3 are built, verified, and deployed; Phase 4 (venues) is the next
optional step. Beyond that:

- Push/real-time notifications (out of scope — polls are in-app; Google's own invite emails cover notification feel)
- Recurring events, multi-group membership UX, roles/permissions
- Preference learning ("Ali never wants Monday mornings")
- Calendar providers beyond Google
- Production hardening: rate limiting, encrypted-at-rest token storage, multi-instance deployment
