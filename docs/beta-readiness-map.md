# Nudgy — Beta Readiness Map (audit of 2026-08-01)

> A verification pass over the **whole** codebase against `saas-roadmap.md`,
> `v1-decisions.md`, `poll-edit-redesign.md` and `.claude/startup.md`.
> Every "BUILT" below was read in the source, not inferred from a doc or a commit
> message. Branch audited: `feat/poll-engine-rewrite` @ `aebfd96`.
> Test suite at time of audit: **440 passed**.

---

> **UPDATE 2026-08-01 (later the same day).** §0 and §3's headline — "the
> frontend is broken" — is **RESOLVED**: the poll UI was rewritten and merged,
> along with both stale scripts, a rebuilt bundle, and a migration for existing
> databases that turned out to be the more serious bug (see §3a). Everything
> else in this document still stands. Original text kept below as the record of
> what was found.

> **UPDATE 2026-08-03 — status of every open item, re-verified in the source
> rather than read off this document.** The audit text below is unchanged and
> remains the record of what 2026-08-01 looked like; this block is the current
> truth. Suite now at **531 passed**.
>
> **§2 — resolved:**
> - *"Two-way sync"* — inbound sync shipped (`feat/inbound-calendar-sync`,
>   merged 2026-08-03). `provider.update_event` is wired by §3's edit path and
>   the deliberate 409 is gone. `sync_setting` is now three real behaviours:
>   `none` / `one_way` (READ only) / `two_way`. See `docs/inbound-sync.md`.
> - *"Poll modes / parallel voting"* and *"members can propose alternative
>   times"* — the UI shipped with the poll UI rewrite; both are real end to end.
> - *"delete dead `polls`/`votes` tables + stray `orbi.db`"* — **verified done
>   2026-08-03.** No such tables in `models.py`, no `orbi.db` on disk.
>
> **§2 — still true, and deferred past beta by decision, not drift:**
> notification preferences, chat persistence, the model router + fallback, and
> the windowed events fetch. All four are `[LOCKED]` in `v1-decisions.md` and
> none of them breaks a tester's session.
>
> **§3 — all six resolved.** 1–4 went with the poll UI rewrite; **5** (`api.md`
> stale on polls) was rewritten 2026-08-03 against the routes — the section now
> documents the three modes, three vote states, the minimum, spotlight/lock-in,
> and the endpoints that replaced the queue; **6** (`.claude/startup.md` stale)
> has been current since 2026-08-01.
>
> **§4 — hosting is DECIDED (2026-08-03):** Render free + Neon free, Frankfurt
> for both, Sentry EU, and **no keep-alive pinger** — the pinger would exhaust
> Neon's 100 CU-hour monthly quota around day 17, because it keeps the process
> alive and the process ticks the DB every 60s. `docs/deploy.md` §6 carries the
> arithmetic. The "deadlines and reminders stop firing while asleep" cost is
> reduced but not eliminated: both job loops now tick once immediately on
> startup instead of sleeping first, so a wake costs boot time rather than boot
> time plus a full interval. Reminders whose window elapsed during sleep are
> still missed — that is the trigger for moving to an always-on host.
>
> **What is actually left before beta:** the mobile-responsive pass (§6.6), then
> the security review (§6.9), last, after the code stops moving.

## 0. The headline

The **backend is in good shape and further along than the docs say.** The poll
engine rewrite (`poll-edit-redesign.md` §1) is fully built and tested, and §4
(availability counting in-app events) shipped as claimed.

The **frontend is not.** `frontend/src/` still speaks the *pre-rewrite* poll API
end to end. Every poll interaction in the app is broken against today's backend:
votes 422, host lock-in 422, "try the next time" 404s, a booked poll renders as
an unknown status. This is the single largest gap between "what we said we did"
and "what a beta tester would experience".

Two support scripts (`seed_app_data.py`, `check_plan_cascade.py`) are also
broken against the new schema — `seed_app_data.py` was confirmed crashing.

---

## 1. What exists and is verified working

### Foundation (Phase 0)
| Item | Where | Verified |
|---|---|---|
| Entitlement/tier skeleton | `core/entitlements.py` | Free/Pro/Team/Max, placeholder numbers |
| Per-user daily AI-turn quota | `core/quota.py`, `AgentUsage` | counted in the user's local day |
| OAuth tokens encrypted at rest | `core/crypto.py`, `db/types.py` | `EncryptedString` on `CalendarAccount.token_json` |
| Session TTL | `api/deps.py` | 30 days, timed signer |
| FK indexes | `db/session.py` `_LATE_INDEXES` | 5 indexes + unique share_token |
| Health probes | `api/health_routes.py` | `/healthz`, `/readyz` |
| Error tracking | `core/observability.py` | Sentry behind `SENTRY_DSN` + credential scrubber |
| Postgres readiness | `db/session.py`, `scripts/check_db.py` | TLS forced, pooling, schema tested vs PG dialect |

### Identity & calendars (Phase 1)
| Item | Where | Verified |
|---|---|---|
| Identity/calendar split | `db/models.py` `CalendarAccount` | multi-account per user, per-account color/sync/primary |
| Email + password + verify + reset + magic-link | `api/auth_routes.py`, `auth/tokens.py`, `core/passwords.py` | enumeration-safe responses |
| Google OAuth (login + connect) | `auth/google.py` | |
| Microsoft/Outlook | `calendars/microsoft.py`, `auth/microsoft.py` | provider implemented, 357-line test file |
| `CalendarProvider` abstraction | `calendars/base.py`, `__init__.py` | |
| Freebusy cache | `calendars/cache.py` | 90s TTL, booking bypasses it |
| Auto timezone | `User.timezone_auto` | manual choice pins it |
| SMTP mail delivery | `mailer/smtp.py` | console backend when `SMTP_*` unset |

### Groups, plans, events
| Item | Where | Verified |
|---|---|---|
| Group lifecycle | `api/group_routes.py` | rename, leave, kick, regenerate code, delete, **owner-departure handover** |
| Deterministic host endpoints | `api/plan_routes.py` | `/lock-in`, `/spotlight` — no LLM in the booking path |
| SSE live feed | `realtime/`, `api/stream_routes.py` | poll survives as a 5s/60s backstop |
| Guest voting via share link | `api/share_routes.py`, `auth/guest_tokens.py` | + reclaim-by-email, per-plan cap, regenerate/revoke |
| Vote deadlines, reminders, convergence | `tools/plan_deadlines.py`, `jobs/plan_ticker.py`, `notify/plans.py` | |
| Injection fencing | `agent/fencing.py` | one seam at `loop._tool_message` |
| Event ownership rules | `tools/event_rules.py` | personal = owner-only; shared delete = creator-only |
| Personal-event edit | `api/event_routes.py` PATCH | `model_fields_set` distinguishes clear-vs-untouched |

### The poll rewrite — `poll-edit-redesign.md` §1 — **BUILT (backend)**
Confirmed in source, all of it:
- **Modes** derived, never stored (`plan_rules.mode_of`); `Plan.asks_interest` fixed at creation.
- **Parallel voting + spotlight**; `advance_to_next_time`, the queued/active/skipped machine and the `dead` status are gone.
- **Three vote states** (`yes` / `no` / `if_needed`) for members *and* guests.
- **Minimum + convergence** (`choose_winner`, `deadline_outcome`, `ready_to_book_early`); `auto_book` and `everyone_said_yes` deleted.
- **Any member can add candidate times** (`POST /plans/{id}/rounds`), remover-is-suggester rule.
- Agent tools updated to match (`spotlight_time`, `lock_in_time`, `get_plan_status` reporting `if_needed`).

> **Doc drift:** `poll-edit-redesign.md` §1.4 says the minimum "prefills with a
> majority of the group". The shipped decision (commit `aebfd96`) is different and
> better: `expected_count = NULL` means *the default rule — every account-holding
> member must be able to make it*, and guests can never substitute. The doc needs
> updating to match the code, not the other way around.

### Availability §4 — **BUILT**
`repo.get_busy_events_for_users` + `BUSY_RSVP_STATUSES` + `MemberBusy.has_source`
+ `members_with_source`. The API's blank-the-slots guard moved off
`members_connected`. Covered by `test_availability_in_app.py` (11 tests).

---

## 2. What we said was done but ISN'T

| Claim | Reality |
|---|---|
| "Poll modes / parallel voting are built" | Backend only. **The UI is the old cascade.** |
| "Two-way sync" (`v1-decisions.md` LOCKED) | Outbound create + delete only. `provider.update_event` is implemented in both providers and **called by nothing**; `event_routes.py` still carries the deliberate 409. No inbound sync at all. |
| "Members can propose alternative times" | Backend yes, **no UI** to do it. |
| "Notifications configurable in Settings" (LOCKED) | No preference model, no Settings section. Mail sends unconditionally. |
| "Chat persistence, ~10 cap" (LOCKED) | No model, no code. `max_persistent_chats` exists in `entitlements.py` and is read by nothing. |
| "Best free model with fallbacks" (LOCKED) | One `LLM_MODEL`, no ladder, no retry-on-other-provider. |
| "Windowed events fetch" (roadmap Part 4) | `repo.get_group_events` is still unbounded, all-time. |
| Roadmap: "delete dead `polls`/`votes` tables + stray `orbi.db`" | Not verified as done — check before beta. |

---

## 3. Broken right now (regressions, not gaps)

1. **`frontend/src/` poll layer — everything.** Confirmed mismatches:
   - `api.voteTime(planId, yes, round_id)` sends `{yes, round_id}`; backend wants `{round_id, answer}` → **422**.
   - `api.lockInPlan(planId)` sends no body; backend requires `{round_id}` → **422**.
   - `api.nextPlanTime` → `POST /plans/{id}/next-time` **no longer exists** → 404.
   - `api.guestTimeVote(token, yes, round_id)` — same shape problem on the share page.
   - `patchPlan({auto_book})` — field silently ignored; the toggle does nothing.
   - `PollsPage` renders `p.status === "scheduled"` / `"dead"`; backend emits `open | booked | expired`. **A booked poll shows no confirmation UI at all.**
   - `PollsPage` reads `t.status === "active"/"skipped"`, `hb.time_yes/time_no/time_waiting`, `b.round_id`, `b.time_label` — none of these exist in the response anymore.
   - No UI exists for: spotlight, `if_needed`, member-added times, removing your own time, the minimum, `expired`.
2. **`backend/scripts/seed_app_data.py` crashes** — sets `TimeRound.status="active"/"queued"`, a column that no longer exists. Confirmed by running it.
3. **`backend/scripts/check_plan_cascade.py`** imports `advance_to_next_time` / `confirm_active_time` — both deleted.
4. **`backend/app/static/` bundle is from 2026-07-31** — two feature merges stale even before the poll rewrite. Render serves this committed bundle.
5. **`docs/api.md` poll section is stale** — no spotlight, no rounds endpoint, old vote bodies.
6. **`.claude/startup.md` is stale** — still lists the poll redesign as "not yet built" and doesn't mention `feat/poll-engine-rewrite`.

---

## 3a. Found while fixing §3 — the one that would have reached production

The audit above missed this because it read code rather than upgrading a
database, and the test suite misses this whole class by construction: **every
test builds a fresh schema with `create_all`, so nothing exercises the path an
existing database takes.**

Against a DB created before the poll redesign, the merged backend could not read
or cast a single vote:

| Column | State | Effect |
|---|---|---|
| `time_votes.yes`, `guest_time_votes.yes` | `NOT NULL`, no default; replaced by three-state `answer` | reads raised *no such column: answer*; writes raised NOT NULL |
| `time_rounds.status` | `NOT NULL`, no default; replaced by nothing | inserting any new candidate time failed |
| `plans.auto_book` | `NOT NULL` but `DEFAULT FALSE` | harmless — accepts inserts, sits unread. Left in place. |

`db/session.py` now adds `answer`, backfills it from the old boolean, then drops
the three dead columns (`ALTER TABLE … DROP COLUMN`, supported on SQLite ≥ 3.35
and every Postgres we target; a failure is logged, not raised). Nothing becomes
`if_needed` in the backfill — nobody was ever able to say it, and inventing it
would put words in a real person's mouth.

`backend/tests/test_db_migration.py` builds the pre-redesign schema by hand,
fills it, upgrades it, and asserts both preservation and writability. All four
tests fail without the fix.

**Carry this forward:** §2 and §3 both change the schema (`GroupEvent` gains
poll parentage; `EventRsvp` gains `needs_reconfirm`). Add the migration and a
`test_db_migration.py` case *with* the feature, not after it.

## 4. Still open by decision (not drift)

- **Hosting.** Render free spins down after 15 min idle; `plan_ticker` is an
  in-process asyncio task, so **deadlines, reminders and convergence stop firing
  while the service sleeps.** This is a live pre-beta bug and it also rules out
  calendar webhooks. Options raised, none chosen: no-sleep free tier (Koyeb /
  Northflank), paid always-on Render, or an external free scheduler hitting a
  tick endpoint.
- **Guest de-duplication at launch** (`poll-edit-redesign.md` §6) — masked in beta
  because we collect emails anyway.
- Whether cancelling a shared event needs more than creator-only.
- `can't-ever` vote state; soft-busy tier in `slots.py`; host handover — all post-launch.

---

## 5. Not started, and correctly deferred

Router + marketing/landing/pricing pages · Privacy/ToS · Google & Microsoft
verification · account export/delete · dark mode · a11y pass · design tokens ·
recurrence · event reminders · solo mode · friends graph · chat integrations ·
smarter venue ranking · host analytics · payment processor.

---

## 6. Recommended order to beta

**The rule: nothing else ships until the frontend matches the backend.** Every
day the two are apart, every other change is being built on a UI nobody can use.

1. **Poll UI rewrite** — blocking. Restores the app to working.
2. **Fix the broken scripts + rebuild the bundle** — cheap, do it inside #1.
3. **Booked poll → GroupEvent (§2)** — small, and it unblocks §3.
4. **Edit + RSVP-reset (§3)** — wires `update_event`, adds `needs_reconfirm`.
5. **Inbound sync** — user considers this beta-critical.
6. **Mobile-responsive pass** — zero `@media` queries today; at 375px the sidebar
   squeezes the content column to ~119px.
7. **Hosting decision + ticker reliability** — must be settled before testers.
8. **Doc reconciliation.**
9. **Security review, last**, after the code stops moving.

Deliberately *after* beta: model router, conversation persistence, notification
preferences, windowed events fetch. None of them break a tester's session.
