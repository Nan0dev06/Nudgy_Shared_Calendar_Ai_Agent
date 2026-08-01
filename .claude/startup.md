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
- **Availability counts in-app events** — PR #25, merged. `poll-edit-redesign.md`
  §4. `repo.get_busy_events_for_users` + `BUSY_RSVP_STATUSES`;
  `MemberBusy.has_source` replaces "externally connected" as the test for
  joining the intersection.
- **Poll engine rewrite** — `feat/poll-engine-rewrite`, merged 2026-08-01.
  `poll-edit-redesign.md` §1 **on the backend only**: modes, parallel voting +
  spotlight, three vote states, the rule-or-number bar, minimum-based
  convergence, member-suggested times, guest reclaim-by-email. 440 tests green.
  Deleted: `advance_to_next_time`, the queued/active/skipped machine, `dead`,
  `auto_book`, `everyone_said_yes`.

> **READ `docs/beta-readiness-map.md`** — a full audit of the code against every
> doc, done 2026-08-01. It is the current source of truth for what is actually
> built. Headline: **`frontend/src/` still speaks the pre-rewrite poll API**, so
> every poll interaction in the UI is broken against the merged backend (422s,
> a 404, a booked poll with no confirmation UI). Also stale and broken:
> `scripts/seed_app_data.py` (crashes), `scripts/check_plan_cascade.py`,
> `docs/api.md`, and the committed `backend/app/static/` bundle.

- **Next up:** the poll UI rewrite (blocking everything else), then
  `poll-edit-redesign.md` §2 + §3, inbound sync, responsive. Deferred past beta:
  conversation persistence, model router + fallback, notification preferences,
  windowed events fetch.

## Decided 2026-08-01, §2 and §3 not yet built — READ `docs/poll-edit-redesign.md`

The poll/voting/editing logic was redesigned from first principles on 2026-08-01
and that doc is authoritative; the poll + editing sections of `v1-decisions.md`
are marked superseded/amended and point at it. Headline changes:

§1 (everything down to "members can add candidate times") is **BUILT on the
backend** — see the merged-work list above. The items still outstanding:

- ~~Poll modes~~ · ~~parallel voting + spotlight~~ · ~~three vote states~~ ·
  ~~the minimum + convergence~~ · ~~member-added times~~ — all shipped. The bar
  is a RULE by default (every account-holding member, guests cannot substitute),
  a NUMBER only when a human types one; the earlier "prefilled with a majority"
  reading is superseded — `poll-edit-redesign.md` §1.4 carries the amendment.
- **The poll UI has not been rewritten** and is the next session. Until then the
  merged backend has no working front end.
- **A booked poll becomes a `GroupEvent`** with the yes-voters as attendees —
  today they're separate models, which is why a booked poll has no edit path.
- **Editing = RSVP-reset, not a vote.** Creator-only edits; material ones
  (title/start/end/location) reset attendees to `needs_reconfirm`, tentative
  until the event. This is where `update_event` (implemented in both providers,
  called by nothing, guarded by a deliberate 409) finally gets wired.
- ~~**Availability must learn about in-app events.**~~ BUILT (PR #25). What
  remains of §4 for later: adding `needs_reconfirm` to `repo.BUSY_RSVP_STATUSES`
  — that one tuple is the entire availability half of §3.
- **Inbound sync** — the user considers this beta-critical: external events
  should be visible IN Nudgy, not merely block scheduling as opaque busy ranges.
  Titles are **opt-in per connected calendar**, visible to their **owner only**
  (groupmates still see busy blocks). Polling first, webhooks later — same
  syncToken/delta machinery, so it's an upgrade, not a rewrite.

## Before beta testers (as of 2026-08-01)

Agreed order (2026-08-01): **finish everything for DESKTOP beta first, then do
the phone pass.** Phone beta is wanted, it is just not first.

1. **Poll UI rewrite** — blocking; the app has no working poll surface today.
   Fold in fixing `seed_app_data.py` + `check_plan_cascade.py` and rebuilding
   the static bundle.
2. **§2 booked poll → `GroupEvent`** (small; unblocks §3).
3. **§3 edit + RSVP-reset** — wires `provider.update_event`, adds
   `needs_reconfirm`.
4. **Inbound sync.**
5. **Mobile-responsive pass** — zero `@media` queries today; at 375px the
   sidebar squeezes the content column to ~119px.
6. **Doc reconciliation** (`api.md` is stale on polls).
7. **One full security-review session, last**, after the code stops moving.

**Hosting — corrected 2026-08-01, read this before repeating the old claim.**
Render's free tier spins a web service down after 15 minutes idle and offers no
cron jobs. `plan_ticker` is an in-process asyncio task started in the FastAPI
lifespan (`main.py`).

What that actually costs, read from the code rather than assumed:

- **Deadlines fire LATE, not never.** `run_tick` scans every open plan and
  `resolve_deadline` only tests `now >= deadline`, so a deadline that passed
  during sleep is resolved on the first tick after any request wakes the service.
- **Reminders can be missed outright.** `reminder_due` returns False once the
  deadline has passed, so a nudge whose window elapsed during sleep is never
  sent — the reminder is the part that genuinely degrades.
- **Cold start hits the first real user**, and `_loop` sleeps
  `PLAN_TICK_SECONDS` *before* its first tick, so the service must stay up ~60s
  past wake for anything to run. Render keeps it up 15 min after a request, so
  it does.
- A calendar write does wake the service, but nothing writes when the group is
  simply idle — which is exactly when a deadline is waiting.

Keep-alive pinging works technically but is **not officially supported by
Render**; their answer to cold starts is a paid instance. It is a workaround
against the spirit of the free tier, not a sanctioned configuration, and it
should not be what a beta depends on. Not decided yet: a no-sleep free tier
(Koyeb Nano, Northflank), a paid always-on Render instance, or an external free
scheduler (GitHub Actions cron / cron-job.org) hitting a tick endpoint — which
is a legitimate "run my job on a schedule" use, unlike pinging `/healthz` purely
to defeat sleep.

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
