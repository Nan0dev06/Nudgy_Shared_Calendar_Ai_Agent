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
  is creator-only, and ticking a shared task done stays open to all. PATCH also
  edits personal events now; `model_fields_set` distinguishes "clear it" from
  "don't touch it". *(Superseded in part by §3: shared-event edit was refused
  for everyone at the time of this PR; it is now creator-only with an
  attendance reset.)*
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

- **Poll UI rewrite** — `feat/poll-ui-rewrite`, merged 2026-08-01. §1 is now
  complete end to end. The card is a GRID of candidate times with three answer
  buttons each; host moves (lock in / lean toward) sit on the row they act on;
  any member can suggest a time; the guest share page shows the same grid.
  **Plus a migration that mattered:** an existing DB could not read or cast a
  vote against the merged backend (`time_votes.yes` and `time_rounds.status`
  were NOT NULL with no default, and `answer` did not exist). `db/session.py`
  now adds + backfills `answer` and drops the three dead columns;
  `tests/test_db_migration.py` builds the old schema by hand to prove it.
  Scripts rebuilt too. 444 tests green.

> **READ `docs/beta-readiness-map.md`** — a full audit of the code against every
> doc, done 2026-08-01, and still the source of truth for what is actually built.
> Its §0/§3 "the frontend is broken" headline is now RESOLVED; everything else in
> it stands. `docs/api.md` is still stale on polls.

- **Poll/event unification + editing** — `feat/poll-event-unification`, merged
  2026-08-01. §2 and §3 both done; see the struck-through items below for what
  each landed. 460 tests green.

- **Inbound sync** — `feat/inbound-calendar-sync`, built 2026-08-02.
  **`docs/inbound-sync.md` is the doc; read it before touching any of this.**
  External events are mirrored into `external_events` on a 5-min tick
  (`jobs/calendar_sync.py`) via Google `syncToken` + Graph `calendarView/delta`
  behind two new `CalendarProvider` methods. Titles are opt-in per calendar
  (`CalendarAccount.read_titles`, default off) and `members_busy` labels **the
  caller's own rows only** — same group, two viewers, two payloads. Labels are
  clipped to live free/busy, so a stale mirror can never invent busy time, and
  availability itself is unchanged (the mirror is not a source of busy time).
  Turning titles off / disconnecting / leaving two-way all PROMPT for what to do
  with what was already pulled in; "keep" survives via a nullable
  `ExternalEvent.account_id`.
  **`sync_setting` is finally three real behaviours** (resolved 2026-08-02):
  `none` = neither direction, `one_way` = writes out only, `two_way` = both.
  `repo.account_syncs_out` (`!= "none"`) and `repo.account_syncs_in`
  (`== "two_way"`). Free/busy is unaffected by all three — it is not sync.
  515 tests green. Verified against the REAL Google + Microsoft accounts on the
  dev machine, not just fakes — see the doc's last section.

- **Next up: the mobile-responsive pass**, then doc reconciliation, then the
  security review. Deferred past beta: conversation persistence, model router +
  fallback, notification preferences, windowed events fetch.

> **Schema rule, learned the hard way:** every model change ships its
> `_LATE_COLUMNS`/`_DROPPED_COLUMNS` entry AND a `tests/test_db_migration.py`
> case in the same commit — and check the case FAILS without the migration.
> The suite builds fresh schemas, so it says nothing about existing data.

## Decided 2026-08-01 — §1-§4 ALL BUILT. `docs/poll-edit-redesign.md` is history now, except §6 (still open)

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
- ~~The poll UI~~ — rewritten and merged; §1 is done end to end.
- ~~**A booked poll becomes a `GroupEvent`**~~ — BUILT (§2, merged 2026-08-01).
  `events.plan_id`; yes/if-needed voters arrive as `going` RSVPs. Guests get no
  RSVP row (no user to hang it on) — the poll stays their record.
- ~~**Editing = RSVP-reset, not a vote.**~~ — BUILT (§3, merged 2026-08-01).
  Creator-only edits; material ones (title/start/end/location) reset attendees
  to `needs_reconfirm`, which counts as BUSY. `provider.update_event` is wired
  and the deliberate 409 is gone. `repo.reset_attendance` skips the editor and
  anyone who already said `cant`.
- ~~**Availability must learn about in-app events.**~~ BUILT (PR #25), and §4 is
  now complete: `needs_reconfirm` joined `repo.BUSY_RSVP_STATUSES` with §3, so a
  pending re-confirm counts as busy and can't be double-booked.
- ~~**Inbound sync**~~ — BUILT 2026-08-02, exactly as specified: opt-in titles
  per calendar, owner-only, polling now with the same syncToken/delta machinery
  a webhook would call. `docs/inbound-sync.md`.
  **Decided while building it (2026-08-02):** external events surface as
  *titled busy blocks*, not as a new card type — colour on the calendar already
  means "whose time this is", and a second colour axis for "which calendar"
  would collide with it. A multi-person cluster shows a `Yours: …` line on the
  block face and the calendar name in the hover. Real event cards were
  deliberately deferred to solo mode, where a calendar-first surface pays off.

## Before beta testers (as of 2026-08-01)

Agreed order (2026-08-01): **finish everything for DESKTOP beta first, then do
the phone pass.** Phone beta is wanted, it is just not first.

1. ~~**Poll UI rewrite**~~ — DONE (merged 2026-08-01), scripts and bundle with it.
2. ~~**§2 booked poll → `GroupEvent`**~~ — DONE (merged 2026-08-01).
3. ~~**§3 edit + RSVP-reset**~~ — DONE (merged 2026-08-01).
4. ~~**Inbound sync**~~ — DONE (`feat/inbound-calendar-sync`, 2026-08-02).
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
