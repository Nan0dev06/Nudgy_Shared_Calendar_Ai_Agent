# Orbi — Agentic Group Scheduling Assistant

**Hackathon project · July 2026 · Team of 2**

Orbi is a conversational AI agent that finds when a group can *actually* meet — and where. Users connect their Google Calendars, join a group, and ask Orbi things like:

> "Find a time this week when everyone's free and suggest somewhere to meet."

Orbi cross-references everyone's live availability, proposes a time and venue with its reasoning, puts the plan to the group as a two-stage cascade (are you in? → does this time work?), and books it on a shared calendar once the host decides. All through chat with a floating orb in the app.

---

> ## Status — July 2026
>
> **All four phases are complete and verified end-to-end**: live `freebusy` across
> real Google Calendars → common-slot intersection → real venue suggestions →
> in-app plan cascade → the host's decision → a **real booking** written to the
> calendar of everyone who's coming.
>
> - **Live deployment:** <https://orbi-1jgh.onrender.com> (Render free tier — the
>   first request after it's been idle can take ~50s to wake up).
> - **Model:** Llama 4 Scout 17B on Groq's free tier. Groq's limits are
>   per-model, and Scout's (500K tokens/day, 30K/min) are the ones that
>   comfortably fit an agent loop — see [Staying inside the free tier](#staying-inside-the-free-tier).
> - **No paid APIs anywhere**, including venues: OpenStreetMap needs no key.

---

## The Problem

Groups with shared calendars still can't schedule. Manually cross-referencing five calendars is tedious, and raw free/busy data doesn't answer the real questions:

- **"When is everyone free?"** — requires intersecting N calendars, in N timezones, at query time.
- **"When does everyone want to meet?"** — requires asking people and honoring their answers.
- **"Where should we go?"** — requires knowing roughly where the group will be.

Existing tools (Calendly, Doodle) solve slices of this with forms and links. Orbi solves it as a conversation with an agent that does the legwork.

## What Orbi Does

1. You talk to Orbi in natural language — including vaguely. "I wanna go out today with my friends" tells it the day and nothing else.
2. Orbi fetches **live** free/busy data for every group member at the moment you ask.
3. It intersects busy blocks to find common free windows, filtered to sensible hours and durations.
4. It **asks for what it doesn't know instead of guessing** — the times it works out itself, but whether you have a place in mind, whether to search near where you'll all already be or somewhere you name, and what kind of outing, it asks. All in one message, then it waits. A plan built on a guess costs the whole group another round of questions.
5. It anchors a location — on the locations members **explicitly typed** into their own calendar events near the slot, or on an area you named — and searches for a real venue nearby.
6. It proposes a slot + venue **with its reasoning spelled out**.
7. On your confirmation, it puts the plan to the group as a two-stage cascade (see below): who's in at all, then — for those people only — does this time work.
8. You (the host) read the tally and decide: lock the time in, or try the next one. Locking in writes the event to a shared Google Calendar for the people who said that time works.

## Agent Architecture

This is the core of the project: a **real multi-step agentic loop**, not a single LLM call. Orbi is an LLM (Llama 4 Scout 17B via Groq's free tier by default; Ollama/OpenAI supported via the same OpenAI-compatible code path) driving a set of backend tools via native tool/function calling.

### Context injection

Every turn, the backend injects into the model's context:

- **The current datetime (UTC + user's timezone)** — the model doesn't know what time it is; all "this week" / "tomorrow" reasoning is relative to this injected "now".
- The user's identity and group membership.

### Tools

All seven are built and wired into the agent:

| Tool | What it does |
|---|---|
| `get_group_members` | Resolve the user's group → member list + calendar connection status |
| `find_meeting_slots` | Live `freebusy.query` for all members, then intersect busy blocks → common free windows, filtered to reasonable local hours and the requested duration. Returns **only busy time ranges** — never titles or details. The intersection math is a pure, unit-tested function |
| `suggest_venues` | Searches for **real** named places, anchored the way the user chose: on the group itself (read the locations members typed into their *own* events near the slot → geocode → centroid), or on an area they named (`near`, which reads no calendar at all). Venues come only from this call; if it returns nothing, Orbi says so, and if the map service itself fails Orbi says *that* instead — an unreachable API is not an empty neighbourhood. **It never invents a venue** |
| `create_plan` | Put a plan — place, day, and an ordered queue of candidate times — to the group, starting the cascade |
| `get_plan_status` | The host's decision box: who's in, who's out, who's silent, and for the time being asked, who can and can't make it |
| `use_next_time` | **Host move.** Drop the current time and ask the next candidate — to everyone who's in, including those who liked the old one |
| `lock_in_time` | **Host move.** Commit the current time and write it to the calendars of **only** the members who said that time works |

### The decision loop (propose → cascade → host decides → commit or try the next time)

```
user request
   │
   ▼
┌─ AGENTIC LOOP (LLM + tools) ────────────────────────────┐
│  resolve group → fetch live free/busy → intersect →     │
│  filter hours → anchor location → search venues         │
│         │                                               │
│         ▼                                               │
│  PROPOSE place + day + candidate times, with reasoning  │
│         │ host confirms                                 │
│         ▼                                               │
│  CREATE PLAN                                            │
│         │                                               │
│         ▼                                               │
│  STAGE 1 → every member: "in for the plan?"             │
│    ├─ no  → out of the plan entirely                    │
│    └─ yes → opens THEIR stage 2 immediately             │
│              │                                          │
│              ▼                                          │
│  STAGE 2 → the interested only: "does 5pm work?"        │
│         │                                               │
│         ▼                                               │
│  REPORT the tally to the HOST — no rule fires           │
│    ├─ host locks in → COMMIT: event for the yes-voters  │
│    └─ host moves on → next time, asked to the whole ────┼──▶ cohort again
└──────────────────────────────────────────────────────────┘
```

Every tool call is logged (tool name, inputs, outputs) so the loop is visible and debuggable live.

### Plan cascade rules (exact)

**Two questions, not one.** The plan (place + day) and the time are asked
separately, because they're different decisions:

- **Stage 1 — interest**, asked of every member: *"Sam suggested the usual coffee shop Monday — are you in?"* A **no** here is out of the whole plan; they're never asked about a time.
- **A yes opens that member's stage 2 on the spot.** Per person — nobody waits for the rest of the group to answer stage 1 first.
- **Stage 2 — time**, asked only of the interested cohort, and only ever **one** candidate time at a time (their favourite first).
- **A no to a time means "not at 5pm", not "not coming."** They stay in the cohort, because if the host moves to 7pm they get asked again — someone free at 5 may be busy at 7.

**The host decides — the system doesn't.** There is no majority rule, no
unanimity, no threshold, no auto-booking, and one no does **not** kill a time.
Orbi reports the tally; the host chooses:

- **Lock it in** → only the members who said **that time** works get the event and the invite. People who said it doesn't are deliberately left off.
- **Move on** → the next candidate time goes to the **whole** interested cohort. Out of times → the plan closes and Orbi searches for fresh ones.
- **Never** silently book onto the calendar of someone who declined, and silence is never consent.

The cascade lives in `tools/plan_rules.py` as pure, unit-tested functions that
only *report*; `tools/plan_service.py` is the single place state transitions.

### The host's report

The host doesn't have to go looking for the tally — Orbi pushes it. When a vote
lands, a report pops up on the host's screen showing **every member on both
questions** (in / out / silent for the plan; can / can't / silent for the time
being asked) and the two moves available, each spelling out its consequence
first — who gets left off the invite, and who gets re-asked:

```
Coffee at the usual · Monday 20 July @ Cafe Younes

Who's in for the plan          Mon 20 Jul 17:00-18:00
  In:      sam, bea, dia         Can make it:     bea
  Not in:  cal                   Can't make it:   dia
  Silent:  —                     Silent:          sam

[Go ahead with 17:00 (1)]  [Try 19:00 instead]  [Not yet]

Going ahead invites only the 1 who said 17:00 works. dia won't be invited.
Trying 19:00 asks everyone who's in again, including bea.
```

Only the host ever sees it — the API omits the tally entirely for everyone else,
so a member cannot see how anyone voted. The buttons **say it to Orbi in words**
rather than calling a booking endpoint, so the agent stays the single path to a
calendar and the host guard lives in one place. Dismissing it keeps it shut until
somebody actually votes again.

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
| Agent | Llama 4 Scout 17B via Groq (free tier) with native tool calling; model overridable via `ORBI_MODEL`, provider via `LLM_PROVIDER` (Ollama/OpenAI use the same code path) |
| Calendar | Google Calendar API — OAuth 2.0, `calendar.readonly` + `calendar.events` scopes, `freebusy.query` |
| Venues | OpenStreetMap — Nominatim (geocoding) + Overpass (venue search). Free, **no API key**, no account |
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
    tools/             # freebusy, slot intersection, venue search, cascade rules, booking
    db/                # SQLAlchemy models + repo + session (users, groups, plans, rounds, votes)
    api/               # REST endpoints (auth, groups, plans, chat)
    static/            # minimal scaffold UI (Orbi orb + chat + the host's report)
  scripts/
    check_freebusy.py  # Phase 1 CLI proof: OAuth + freebusy + intersection
    check_plan_cascade.py  # Cascade proof: interest → time → host decides → real booking
    chat_cli.py        # Talk to the agent from the terminal (no UI needed)
    seed_demo.py       # Populate test calendars with realistic Beirut events
    connect_account.py # One-time OAuth connect per test account (CLI)
    import_tokens.py   # Load existing OAuth tokens into the DB
  tests/               # slot math + plan cascade rule unit tests
docs/
  api.md               # Endpoint reference with example bodies
  deploy.md            # Render deployment walkthrough
render.yaml            # One-click blueprint: web service + free Postgres
```

## Setup

> Detailed steps land with the code. Outline:

1. **Google Cloud project**: enable the Calendar API, create OAuth 2.0 credentials (web application), add test-account emails as test users. Venue search needs nothing — OpenStreetMap is keyless.
2. **Groq API key** (free) from console.groq.com/keys.
3. Copy `.env.example` → `.env`, fill in `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REDIRECT_URI`, `GROQ_API_KEY`. Secrets are never committed.
4. `pip install -r requirements.txt`
5. Phase 1 check: `python scripts/check_freebusy.py` — proves OAuth + freebusy + intersection across the test accounts before any UI exists.
6. `uvicorn app.main:app --reload` and open the app.

### Staying inside the free tier

Nothing here costs money, but the agent loop is token-hungry and the free
limits are easy to hit by accident. **Groq's rate limits are per model and per
organization** — so a second API key changes nothing, and picking the right
model is the whole game:

| Model | Tokens/day | Tokens/min |
|---|---|---|
| `llama-4-scout-17b-16e-instruct` (default here) | 500K | 30K |
| `llama-3.1-8b-instant` | 500K | 6K |
| `llama-3.3-70b-versatile` | 100K | 12K |

One turn costs roughly **4K tokens per LLM call** — a ~2.4K system prompt plus
~1.6K of tool schemas, re-sent on every step of the loop — and a turn takes 3-4
calls. So a two-message conversation runs ~18K tokens. On the 70B that is about
five conversations before the day's quota is gone, and its 12K/min ceiling
throttles a *single* conversation in progress. Scout's budget fits ~27.

Because the budgets are per-model, developing on Scout leaves the 70B's quota
untouched — set `ORBI_MODEL=llama-3.3-70b-versatile` for a demo if you want its
quality, and it will be full. `LLM_PROVIDER=ollama` runs a local model for
unlimited free iteration (weaker at tool calling; good for plumbing, not for
verifying final behaviour).

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
| 3 | Plan cascade → interest + time votes → host decides → commit to shared calendar or try the next time | ✅ (verified end-to-end incl. a real booking) |
| 4 | Venue suggestion (only if time permits) | ✅ (built on OpenStreetMap instead of Places — free and keyless) |
| + | **Deployed** to Render with Postgres | ✅ (bonus, beyond the original plan) |

Each phase must work before the next begins.

## Handled Correctly (by design)

- **Timezones**: UTC everywhere internally; per-user local display; members may be in different zones; unit tests on the intersection + conversion logic.
- **OAuth token refresh**: expiry handled explicitly — no silent failures.
- **No common slot**: Orbi says so clearly and offers the closest partial options ("4 of 5 are free Thursday 5pm").
- **Members without a connected calendar**: surfaced explicitly, never silently ignored.
- **Tool-call logging**: every agent step logged for live debugging and demoing the loop.
- **A flaky LLM doesn't kill the turn**: Groq rate limits are retried with backoff, and a malformed tool call is handed back to the model as an error it can correct rather than crashing the request.
- **No raw 500s in chat**: agent, LLM, and DB errors are caught and answered in words.
- **A failed booking doesn't strand a plan**: if Google refuses or throws, the time reverts to active so the host can retry or move on, instead of sticking in a state that is neither bookable nor skippable.
- **A failed venue search is not an empty neighbourhood**: Overpass is a free public API and 504s under load. Transient failures are retried, and a search that still fails is reported as *"the venue lookup is temporarily down"* — never as "there are no cafes there", which would be Orbi stating something false about a real place.

## Roadmap / Not Yet Built

All four phases are built, verified, and deployed. Beyond that:

- **True push** (WebSockets). The host's report currently arrives via short polling while a plan is open, which is enough for in-app use; there are no notifications outside the app, and Google's own invite emails cover the notification feel once something is booked.
- Recurring events, multi-group membership UX, roles/permissions
- Preference learning ("Ali never wants Monday mornings")
- Calendar providers beyond Google
- Production hardening: rate limiting, encrypted-at-rest token storage, multi-instance deployment
