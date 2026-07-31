# Nudgy — Architecture Map (the "where")

> Companion to `saas-roadmap.md` (the *what/why*) and `v1-decisions.md` (the
> *decisions*). This doc is the ***where***: what each module owns, and which
> files to open for a given task — so a session can start work without reading
> the whole codebase.
>
> **How to use this (important):** the "read these first" lists below are a
> *starting point, not a fence*. They're almost always what you need. But if
> they turn out to be insufficient for the task, follow the imports and read
> what you actually need — don't stop short. The only thing to avoid is reading
> the **whole** codebase by default when a handful of files would do. Scope
> first; widen only when the task demands it.
>
> Keep this file updated when modules move or are added (it's cheap insurance
> against expensive re-exploration).

---

## Stack & shape
- **Backend:** Python 3.12 + FastAPI + SQLAlchemy 2.0. SQLite locally
  (`nudgy.db`), Postgres in prod (via `DATABASE_URL`). Agent talks to an
  OpenAI-compatible LLM (Groq free tier by default).
- **Frontend:** React + Vite SPA in `frontend/`, **built into**
  `backend/app/static/` which FastAPI serves at `/`. No router yet.
- **One choke point per concern.** All DB queries live in `db/repo.py`; all
  config/env in `core/config.py`; all calendar API work behind
  `calendars/`; all availability math flows through `agent/availability.py`.
  When in doubt, start at the choke point.

## Migrations (no Alembic)
Schema changes happen in **two places**: add the column/relationship to
`db/models.py`, then register it in `db/session.py` — new columns in
`_LATE_COLUMNS`, new indexes in `_LATE_INDEXES`, data moves in a backfill (see
`_backfill_calendar_accounts`). `init_db()` runs create_all + these on every
boot; they're idempotent and work on SQLite + Postgres. Brand-new *tables* need
nothing extra (create_all builds them).

---

## Backend module map (`backend/app/`)

### `main.py`
FastAPI entrypoint. Registers routers, calls `init_db()`, serves the static
frontend. Start here to see the whole route surface.

### `core/` — cross-cutting primitives
| File | Owns |
|---|---|
| `config.py` | **Single source of all settings/env.** Nothing else reads `os.environ`. |
| `crypto.py` | Fernet encrypt/decrypt for secrets at rest (used by `db/types.py`). |
| `passwords.py` | scrypt password hashing (stdlib, no dep). |
| `entitlements.py` | Plan/tier definitions (Free/Pro/…) + feature gates. |
| `quota.py` | Per-user daily AI-turn quota meter. |
| `observability.py` | Sentry init (off without `SENTRY_DSN`) + `scrub_event`, which strips share tokens / OAuth codes / cookies / bodies from every error report. |

### `db/` — persistence
| File | Owns |
|---|---|
| `models.py` | All ORM models: `User`, `CalendarAccount`, `Group`, `Membership`, `Plan`, `TimeRound`, `InterestVote`, `TimeVote`, `GroupEvent`, `EventRsvp`, `PlaceReview`, `AgentUsage`. |
| `repo.py` | **Every DB query in the app.** Add queries here, not inline. |
| `session.py` | Engine, `SessionLocal`, `get_session`, `init_db` + the migration/backfill machinery. |
| `types.py` | `EncryptedString` column type (transparent encrypt-at-rest). |

### `auth/` — identity & OAuth
| File | Owns |
|---|---|
| `google.py` | Google OAuth web flow + credential (`credentials_from_json`) helpers. |
| `tokens.py` | Signed, timed tokens for email verify / magic-link / password reset. |

### `calendars/` — the calendar-provider seam
| File | Owns |
|---|---|
| `base.py` | `CalendarProvider` ABC (`get_busy`, `get_event_locations`, `create_event`, `update_event`, `delete_event`) + `CreatedEvent`, `Interval`. |
| `google.py` | `GoogleCalendarProvider` (reads delegate to `tools/freebusy` + `tools/locations`; writes centralized here). |
| `cache.py` | `FreebusyCache` — short-TTL busy-range cache (display only; booking bypasses it). |
| `__init__.py` | `provider_for_account(session, account)` factory. Microsoft → `NotImplementedError` until built. |

> **Adding a provider (e.g. Microsoft):** new `calendars/<name>.py` implementing
> `CalendarProvider`, one branch in `calendars/__init__.py`, and an
> `auth/<name>.py` for its OAuth. Nothing else changes — every caller already
> uses the interface.

### `mailer/` — transactional email seam
| File | Owns |
|---|---|
| `base.py` | `EmailSender` ABC + `Email`. |
| `console.py` | Dev backend — logs the email (links appear in the server console). Default. |
| `memory.py` | Test backend — captures to `.outbox`. |
| `__init__.py` | `send_email(...)` + `set_email_sender(...)` (swappable singleton). |

> A real free-tier provider (Resend/SMTP) is a later drop-in: new backend class,
> point the singleton at it. No caller changes.

### `notify/` — what a plan says when it emails somebody
`plans.py` owns the wording of the vote reminder, the "voting closed" note to
the host, and the "it booked itself" note to attendees. One recipient per call
with pre-rendered time labels, because times must read in the reader's zone.
Sends are best-effort — a dead mailbox must never abort a tick. **This is where
per-user Settings > Notifications preferences hook in when they land.**

### `jobs/` — work that runs on a clock, not on a request
`plan_ticker.py`: once a minute, resolve passed deadlines and nudge non-voters.
`run_tick(session, now)` is plain and synchronous (tests drive months of plan
life with a frozen clock); the asyncio loop around it is started/stopped by
`main.py`'s lifespan. In-process on purpose — a broker would be another thing to
host. Knobs: `PLAN_TICK_SECONDS`, `PLAN_TICKER_ENABLED`,
`PLAN_REMINDER_INTERVAL_SECONDS`.

### `realtime/` — the live feed's pub/sub
`bus.py`: an in-process `EventBus` of per-group subscribers; `__init__.py`
exposes `plans_changed(group_id)` / `events_changed(group_id)`, which is what
routes, the agent's mutating tools, and the ticker call. What travels is a KIND
("plans"/"events"), never plan contents — the client refetches through the
normal authenticated endpoint, so authorization stays in one place and bursts
coalesce into one refetch. One process only: multi-worker needs Redis behind
the same two functions. Consumed by `api/stream_routes.py`; knobs
`SSE_HEARTBEAT_SECONDS`, `SSE_MAX_CONNECTIONS`.

### `tools/` — calendar reads, venue search, slot math, booking, vote rules
| File | Owns |
|---|---|
| `freebusy.py` | Google freebusy read (**privacy boundary**: busy ranges only). |
| `locations.py` | Venue-suggestion pipeline (OpenStreetMap geocode/Overpass) + event-location read. |
| `slots.py` | **Pure** interval math: `find_common_slots`, `merge_intervals`, `complement`, `intersect`, `reasonable_hours`. No I/O — easy to unit-test. |
| `booking.py` | Writes a host-confirmed plan time to the calendar (via the provider). |
| `plan_rules.py` | **Pure** two-stage vote-cascade logic (interest → time). Works on *participants* — members and share-link guests alike. |
| `plan_deadlines.py` | **Pure** async-convergence rules: when to nudge, when a deadline closes/books a plan, what counts as unanimity. |
| `plan_service.py` | Host actions (confirm / advance) **and** the two async transitions (`maybe_auto_book`, `resolve_deadline`). Also where `PlanState` merges members + guests. |

### `agent/` — the LLM agent
| File | Owns |
|---|---|
| `loop.py` | The multi-step agent loop (calls the model, dispatches tools). |
| `prompt.py` | System-prompt assembly (incl. the user-memory block). |
| `tools.py` | Agent tool definitions + implementations (find slots, suggest venues, create plan, …). |
| `availability.py` | **Availability service:** `fetch_busy_for_group` (unions each member's calendars via the cache) + `compute_availability` (slots + partial windows). The bridge between DB and `tools/slots`. |
| `fencing.py` | **The trust boundary.** Sanitizes text nobody on this side wrote (calendar locations, OSM venue/area names, group names, reviews, memory notes) and wraps it in `<untrusted>` blocks. Applied to the prompt in `prompt.py` and to *every* tool result in `loop._tool_message`, so a tool added later is covered by default. |

### `api/` — HTTP routes
| File | Route surface |
|---|---|
| `deps.py` | `get_session` + `get_current_user` (session-cookie auth). Shared cookie config. |
| `health_routes.py` | `GET /healthz` (no I/O — what the uptime pinger hits) and `GET /readyz` (round-trips the DB). |
| `auth_routes.py` | `/auth/*` — Google OAuth **and** email/password/magic-link/reset + `/auth/me` (+ `/auth/me/detected-timezone`). |
| `group_routes.py` | `/groups/*` — create/join, members. |
| `event_routes.py` | group events & tasks + Google sync. |
| `plan_routes.py` | `/plans/*` — voting, deterministic host actions, deadline/auto-book settings, share-link on/off. |
| `share_routes.py` | `/share/{token}/*` — **the only unauthenticated writes in the app.** Guest voting: view, join (name + optional email), interest, time-vote. |
| `stream_routes.py` | `GET /groups/{id}/stream` — the SSE live feed (see `realtime/`). |
| `chat_routes.py` | `/chat` — the agent endpoint. |
| `review_routes.py` | `/reviews` — place reviews. |

---

## Frontend map (`frontend/src/`)
> Not yet mapped in depth — this is a structural index. A dedicated frontend
> session should expand it.

- `main.jsx` — entry; `App.jsx` — top-level app + view state (no router yet).
- `screens/` — pre-app gates: **`SignIn.jsx`** (auth UI lives/expands here),
  `GroupGate.jsx` (pick/join a group).
- `pages/` — `HomePage`, `CalendarPage`, `PollsPage`, `PlacesPage`,
  `ActivityPage`, `SettingsPage`.
- `components/` — `Shell`, `Sidebar`, `TopBar`, `ChatPanel`, `ChatbotOrb`,
  `Modals`, `Fields`, `Blobs`, `OrbLogo`, `Icons`.
- `api.js` — backend calls; `live.js` — the SSE subscription (`useGroupLive`)
  + the poll cadences; `ctx.js` — shared state; `availability.js`, `dates.js`,
  `people.js`, `places.js`, `theme.js` — helpers.
- `vite.config.js` — dev proxy to `:8000` (`/auth`, `/groups`, `/plans`,
  `/events`, `/reviews`, `/chat`; the dead `/polls` prefix was removed in
  PR#13). Every backend prefix the frontend calls must be listed here.

---

## "I want to work on X → read these first"
- **Availability / free-busy:** `agent/availability.py`, `tools/slots.py`,
  `calendars/cache.py`, `calendars/google.py`, `tools/freebusy.py`,
  `db/repo.py` (`get_calendar_accounts`).
- **The agent (behavior, tools, prompt):** `agent/loop.py`, `agent/prompt.py`,
  `agent/tools.py`; `core/quota.py`; `core/config.py` (LLM_* settings).
- **Prompt injection / untrusted text:** `agent/fencing.py`, the `UNTRUSTED DATA`
  rule in `agent/prompt.py`, `loop.TRUSTED_RESULT_KEYS`. Adding a tool that
  returns a `note` means promising that note never inlines untrusted text —
  `tools/locations.py` shows the pattern (point at a field, don't splice it in).
- **Auth (login, sessions, email/password):** `api/auth_routes.py`,
  `api/deps.py`, `auth/google.py`, `auth/tokens.py`, `core/passwords.py`,
  `mailer/`, `db/repo.py` (user/account fns), `db/models.py` (`User`,
  `CalendarAccount`).
- **Add a calendar provider:** `calendars/base.py`, `calendars/__init__.py`,
  new `calendars/<name>.py`, `auth/<name>.py`.
- **Plans / voting / host actions:** `tools/plan_rules.py`,
  `tools/plan_service.py`, `api/plan_routes.py`, `db/models.py` (`Plan`,
  `TimeRound`, votes), `db/repo.py`.
- **Deadlines / reminders / auto-book:** `tools/plan_deadlines.py`,
  `jobs/plan_ticker.py`, `notify/plans.py`, `tools/plan_service.py`
  (`maybe_auto_book`, `resolve_deadline`), `core/config.py` (PLAN_* knobs).
- **Guest voting / share links:** `api/share_routes.py`,
  `auth/guest_tokens.py`, `db/models.py` (`PlanGuest`, `Guest*Vote`,
  `Plan.share_token`), `db/repo.py` (share + guest section),
  `frontend/src/screens/SharePage.jsx`.
- **Live updates / SSE:** `realtime/bus.py`, `realtime/__init__.py`,
  `api/stream_routes.py`, `frontend/src/live.js`, `App.jsx` (the poke handlers
  + the backstop poll), `core/config.py` (SSE_* knobs).
- **DB schema change:** `db/models.py` + `db/session.py`
  (`_LATE_COLUMNS`/`_LATE_INDEXES`/backfill).
- **Config / env:** `core/config.py` + `.env.example`.
- **Deploying / Postgres / error tracking:** `docs/deploy.md`, `render.yaml`,
  `db/session.py` (`normalize_db_url`, engine/pool), `core/observability.py`,
  `api/health_routes.py`, `backend/scripts/check_db.py`.
- **Timezones:** `db/models.py` (`User.timezone`, `timezone_auto`),
  `api/auth_routes.py` (`patch_me`, `detected_timezone`), `App.jsx`
  (`reportTimezone`), `pages/SettingsPage.jsx`. Everything is UTC internally
  and rendered per-viewer.
- **Billing / quota / tiers:** `core/entitlements.py`, `core/quota.py`,
  `db/models.py` (`AgentUsage`, `User.tier`).
- **Events & tasks + Google sync:** `api/event_routes.py`, `db/models.py`
  (`GroupEvent`, `EventRsvp`), `db/repo.py`, `calendars/`.
- **Venue suggestion:** `tools/locations.py`, `agent/tools.py` (`_suggest_venues`).

## Tests (`backend/tests/`)
One file per concern; mirror that when adding features. Notable:
`test_calendar_accounts`, `test_calendar_provider`, `test_freebusy_cache`,
`test_email_auth`, `test_passwords`, `test_slots`, `test_plan_rules`,
`test_plan_*`, `test_quota`, `test_auth_security`, `test_crypto`,
`test_db_portability` (renders the schema against the Postgres dialect with no
server), `test_observability`, `test_health`, `test_timezone`. Run:
`python -m pytest backend/tests -q`.
