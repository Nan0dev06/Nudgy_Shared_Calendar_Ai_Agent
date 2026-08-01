# Nudgy — Beta Readiness Map (audit of 2026-08-01)

> A verification pass over the **whole** codebase against `saas-roadmap.md`,
> `v1-decisions.md`, `poll-edit-redesign.md` and `.claude/startup.md`.
> Every "BUILT" below was read in the source, not inferred from a doc or a commit
> message. Branch audited: `feat/poll-engine-rewrite` @ `aebfd96`.
> Test suite at time of audit: **440 passed**.

---

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
