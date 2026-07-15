# Orbi — Agentic Group Scheduling Assistant

**Hackathon project · July 2026 · Team of 2**

Orbi is a conversational AI agent that finds when a group can *actually* meet — and where. Users connect their Google Calendars, join a group, and ask Orbi things like:

> "Find a time this week when everyone's free and suggest somewhere to meet."

Orbi cross-references everyone's live availability, proposes a time and venue with its reasoning, runs a poll, and — once the group approves — books the event on a shared calendar. All through chat with a floating orb in the app.

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

| Tool | What it does |
|---|---|
| `get_group_members` | Resolve the user's group → member list + calendar connection status |
| `get_free_busy` | Live `freebusy.query` against Google Calendar for all members over a time range. Returns **only busy time ranges** — never titles or details |
| `find_common_slots` | Intersect busy blocks → common free windows, filtered to reasonable hours and requested duration (pure function, unit-tested) |
| `get_event_locations` | Locations users explicitly typed into their own events adjacent to a candidate slot → geographic anchor |
| `search_venues` | Real Places API call near the anchor. No results → Orbi says so. **It never invents a venue** |
| `create_poll` | Propose slot (+ venue) to the group as an in-app poll |
| `get_poll_status` | Read votes collected so far |
| `create_calendar_event` | Write the approved event to the shared group calendar |

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

Orbi does group scheduling and meetup planning. Nothing else. Asked to write an essay or discuss the weather, it politely declines and restates what it can do. Enforced in the system prompt and covered by tests.

## Tech Stack

| Layer | Choice |
|---|---|
| Backend | Python 3.11+, FastAPI |
| Agent | Llama 3.3 70B via Groq (free tier) with native tool calling; Ollama/OpenAI swappable via `LLM_PROVIDER` |
| Calendar | Google Calendar API — OAuth 2.0, `calendar.readonly` + `calendar.events` scopes, `freebusy.query` |
| Venues | Google Places API (Phase 4) |
| Frontend | React (teammate's branch). Backend exposes a documented REST API with example request/response bodies for every endpoint |
| Storage | SQLite (hackathon-appropriate; swappable) |
| Timezones | Everything stored in UTC; converted to each user's local timezone at display time. Unit-tested |

### Repository layout (planned)

```
backend/
  app/
    main.py            # FastAPI app
    auth/              # Google OAuth flow + token refresh
    agent/             # agent loop, system prompt, tool definitions
    tools/             # Tool implementations (freebusy, intersection, places, …)
    models/            # DB models: users, groups, polls, votes
    api/               # REST endpoints for the frontend
  scripts/
    check_freebusy.py  # Phase 1 CLI proof: OAuth + freebusy + intersection
    seed_demo.py       # Populate 3–4 test calendars with realistic Beirut events
  tests/
docs/
  api.md               # Endpoint reference with example bodies
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

The backend runs anywhere; for a public, always-on URL (used by the Google
Calendar add-on) there's a one-click Render blueprint (`render.yaml`) that
provisions a free Postgres database alongside the web service. Locally it uses
SQLite; set `DATABASE_URL` (Render does this automatically) to use Postgres.
Full walkthrough in [docs/deploy.md](docs/deploy.md).

## Build Order

| Phase | Deliverable | Status |
|---|---|---|
| 1 | Google OAuth + freebusy across test accounts + intersection logic, proven via CLI script | ✅ |
| 2 | Conversational agent + Orbi orb UI: open-ended prompt → candidate slots → reasoning in natural language | ✅ (agent chat needs the free `GROQ_API_KEY` in `.env`) |
| 3 | Poll → vote collection → threshold rule → commit to shared calendar or re-plan | ✅ (verified end-to-end incl. a real booking) |
| 4 | Venue suggestion via Places API (only if time permits) | ⬜ |

Each phase must work before the next begins.

## Handled Correctly (by design)

- **Timezones**: UTC everywhere internally; per-user local display; members may be in different zones; unit tests on the intersection + conversion logic.
- **OAuth token refresh**: expiry handled explicitly — no silent failures.
- **No common slot**: Orbi says so clearly and offers the closest partial options ("4 of 5 are free Thursday 5pm").
- **Members without a connected calendar**: surfaced explicitly, never silently ignored.
- **Tool-call logging**: every agent step logged for live debugging and demoing the loop.

## Roadmap / Not Yet Built

Everything. This README is the spec; code follows phase by phase. Beyond Phase 4:

- Push/real-time notifications (out of scope — polls are in-app; Google's own invite emails cover notification feel)
- Recurring events, multi-group membership UX, roles/permissions
- Preference learning ("Ali never wants Monday mornings")
- Calendar providers beyond Google
- Production hardening: rate limiting, encrypted-at-rest token storage, multi-instance deployment
