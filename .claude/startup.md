# Nudgy session startup

Read these before doing anything else this session:

1. **`docs/saas-roadmap.md`** — the master plan: phases, what's done, what's next.
2. **`docs/architecture-map.md`** — module map + "working on X? read these files" index.
3. **`docs/v1-decisions.md`** — locked/discuss/later product decisions. Don't relitigate a LOCKED item.
4. **Memory** (if available) — prior-session state, known issues, process notes.
5. `git log -1 --oneline && git branch --show-current` — confirm branch and last commit match what memory/roadmap say.

## Current state (update this section as phases complete)

- **Phase 0** (quota, session TTL, token encryption, perf, host endpoints) — DONE, merged.
- **Phase 1** (identity/calendar split, provider abstraction for Google+Microsoft,
  freebusy cache, email+password+magic-link+reset auth, Outlook) — DONE, merged
  (PRs #7, #8, #9, #10, #12; #11 = architecture-map doc).
- **Frontend auth UI** (PR #13) + **sync_setting enforcement** (#14) + **beta
  hardening / group lifecycle** (#15) — DONE, merged.
- **Async voting** (deadlines, non-voter reminders, opt-in auto-book) — PR #16,
  merged.
- **Guest voting** (shareable vote links, no account needed) — PR #17, merged.
- **Direct host actions** (the card calls `/lock-in` + `/next-time` instead of
  the LLM) — PR #18, merged.
- **SSE live feed** replacing the 5s poll — branch `feat/sse-live-updates`.
  `app/realtime/` (in-process bus, pokes carry only a group id + kind) +
  `GET /groups/{id}/stream` + `frontend/src/live.js`. The poll survives as a
  backstop: 5s when the stream is down, 60s when it's up.
- **SMTP email** (real delivery, enumeration-safe sends) — PR #20, merged.
- **Postgres + observability** — PR #21, merged. Neon-shaped engine (TLS forced
  via `normalize_db_url`, pooling for idle disconnects), `scripts/check_db.py`,
  schema portability tested against the PG dialect offline, Sentry behind
  `SENTRY_DSN` with a credential scrubber, `/healthz` + `/readyz`, `render.yaml`
  + `docs/deploy.md` rewritten for the beta path.
- **Auto timezone** — PR #22, merged. `User.timezone_auto`; the browser reports
  its zone on every boot; typing one in Settings pins it forever.
- **Injection defense** — PR #23, merged. `agent/fencing.py`: untrusted text
  (calendar locations, OSM venue/area names, group names, reviews, memory notes)
  is sanitized and wrapped in `<untrusted>` blocks, plus one standing prompt
  rule. Fenced at ONE seam — `loop._tool_message` — so a tool added later is
  covered by default. `TRUSTED_RESULT_KEYS` (`note`/`error`/`search_failed`)
  stay outside the fence, which is why `locations.py` notes point at a field
  name instead of inlining an untrusted value. Costs ~124 tokens/step.
- **Event ownership** — PR #24, merged. `tools/event_rules.py` (pure, beside
  `plan_rules.py`): personal events are owner-only — closing a real hole where
  any member could delete another's masked private event — shared-event delete
  is creator-only, shared-event EDIT is refused for everyone including the
  creator (it needs the vote flow below), and ticking a shared task done stays
  open to all. PATCH also edits personal events now; `model_fields_set`
  distinguishes "clear it" from "don't touch it".
- **Next up in Phase 2:** conversation persistence, model router + fallback.

## Decided 2026-08-01, not yet built — READ `docs/poll-edit-redesign.md` FIRST

The poll/voting/editing logic was redesigned from first principles on 2026-08-01
and that doc is authoritative; the poll + editing sections of `v1-decisions.md`
are marked superseded/amended and point at it. Headline changes:

- **Poll modes finally built** (Quick / Pick-a-time / Float-an-idea) — they were
  `[LOCKED]` since 2026-07-25 and never existed in code. Interest survives only
  in Float-an-idea.
- **Parallel time voting + a host spotlight** replaces the serial round queue.
  Moving the spotlight resets nothing. Deletes `advance_to_next_time`, the
  `queued/active/skipped` machine, and the `dead` status.
- **Votes gain a third state** (yes / no / if needed).
- **Every poll carries a required minimum** (`expected_count`, prefilled with a
  group majority) and **converges by itself at the deadline** — best time that
  meets the minimum books for its yes voters. Deletes `auto_book`.
- **Members can add candidate times**; the host still decides.
- **A booked poll becomes a `GroupEvent`** with the yes-voters as attendees —
  today they're separate models, which is why a booked poll has no edit path.
- **Editing = RSVP-reset, not a vote.** Creator-only edits; material ones
  (title/start/end/location) reset attendees to `needs_reconfirm`, tentative
  until the event. This is where `update_event` (implemented in both providers,
  called by nothing, guarded by a deliberate 409) finally gets wired.
- **Availability must learn about in-app events.** `fetch_busy_for_group` reads
  ONLY external freebusy — in-app events and RSVPs affect nothing today, while
  calendar-optional is `[LOCKED]`. `needs_reconfirm` counts as busy.
- **Inbound sync** — the user considers this beta-critical: external events
  should be visible IN Nudgy, not merely block scheduling as opaque busy ranges.
  Titles are **opt-in per connected calendar**, visible to their **owner only**
  (groupmates still see busy blocks). Polling first, webhooks later — same
  syncToken/delta machinery, so it's an upgrade, not a rewrite.

## Before beta testers (as of 2026-08-01)

Code-side: **inbound sync** and **edit proposals + voting** (both above), plus
the **mobile-responsive pass** (own session — there are zero `@media` queries
and at 375px the sidebar squeezes the content column to ~119px). Then **one
full security-review session**, last, after the code stops moving.

**Hosting is a live blocker.** Render's free tier spins a web service down after
15 minutes idle and offers no cron jobs. `plan_ticker` is an in-process asyncio
task started in the FastAPI lifespan (`main.py`), so while the service sleeps
**vote deadlines, non-voter reminders and auto-book stop firing** — a real
pre-beta bug today, independent of sync. It also rules out reliable calendar
webhooks, since nothing would renew the subscriptions. Options raised, none
decided: a no-sleep free tier (Koyeb Nano, Northflank), a paid always-on Render
instance, or staying on Render and driving ticks from an external free scheduler
(GitHub Actions cron / cron-job.org hitting an endpoint).

Everything else is the user's to do, outside the repo: live-test SMTP delivery,
verify Google + Microsoft calendars actually sync, create the Neon project and
paste `DATABASE_URL` (verify with `python backend/scripts/check_db.py`),
optionally a Sentry DSN, then deploy per `docs/deploy.md` (redirect URIs +
`APP_BASE_URL` + Google consent screen in Testing with each tester's address in
Test users).

## Working branch

All work happens on `post-hackathon-submission-edits`. Never touch/merge `main`
until the user explicitly says to. Each feature gets its own `feat/*` branch off
`post-hackathon-submission-edits`, opened + merged as a PR into that integration
branch.

## Process notes

- Ultracode is always on for this project.
- Default to working inline. Only reach for the Workflow tool (multi-agent
  fan-out) for: (a) a security/auth review, (b) a large multi-file audit, (c)
  integrating an unfamiliar external API. Keep those runs small (2-3 agents),
  not a 15-agent sweep — cost/time tracks agent count.
- `gh` CLI is at `/c/Program Files/GitHub CLI/gh.exe` (not on bash PATH — call by
  full path). Auth per-session via:
  `export GH_TOKEN=$(printf 'protocol=https\nhost=github.com\n\n' | git credential fill | sed -n 's/^password=//p')`
