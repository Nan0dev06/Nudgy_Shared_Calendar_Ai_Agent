# Nudgy v1 — Decisions, Scope & Backlog

> Companion to `saas-roadmap.md`. This is the authoritative record of choices made
> during the 2026-07-25 verification pass. Branch: `post-hackathon-submission-edits`.
> Legend: **[LOCKED]** decided · **[DISCUSS]** open, decide before/at build ·
> **[LATER]** deliberately deferred.

## Vision & success
- **Promise (v1):** *coordinate any group's time and place* — take the pain out of arranging things with other people. [LOCKED]
- **Feel:** the agent surfaces when the group can go out, the user picks, the agent arranges with everyone and finalizes it clearly and visually. [LOCKED]
- **Primary use cases:** casual friend outings + small work teams first; then recurring friend groups, family, clubs, events. [LOCKED]
- **v1 success metric:** the product actually used regularly by **3 groups × 3 users**. [LOCKED]
- **Interface stance:** **app-first**, but the AI agent is a major surface; users can also do everything manually. Later: let users talk to the agent from *outside* the app (messaging channels). [LOCKED]
- **Name:** keep **Nudgy**; clean up leftover "orbi/Orbi". [LOCKED]

## Core principle discovered this pass
**One user = one unified availability.** All of a user's calendars *and* all their
group commitments roll up into a single free/busy truth, so Nudgy never double-books
them across groups. A **group is a planning context, not a separate time universe.**
Group plans/polls/events are scoped to the group; *time* is unified per person. [LOCKED — confirmed]

## V1 scope — IN
- **Auth:** email **+ password** (verified) **+ optional magic-link**, Google, **Microsoft (in v1)**. Identity decoupled from calendars. [LOCKED]
- **Calendar-optional:** usable with **no external calendar** — Nudgy works as your own calendar app (create/view/edit events, tasks, month/week/day). [LOCKED]
- **Multiple calendars per user**, viewable together, told apart by **color/hover**. [LOCKED]
- **Two-way sync** (create/delete/**edit** both directions) with a per-user **setting: none / one-way / two-way** (default two-way, after consent). [LOCKED]
- **Groups:** invite codes + invite links + email invites + **in-app friend invites**; you can be in a group without being friends. No size cap (warn when large). Minimal **owner/member** roles. Group lifecycle (leave/rename/delete/regenerate code) + **host handover / hostless** plans (default: keep + auto-book-if-all-yes). [LOCKED]
- **Agent:** best **free** model with **fallbacks**; **per-user daily turn limit** (free); confirm-before-action on writes; can create/edit/delete events/tasks/plans/polls with confirmation; warm/casual tone; learns how the user likes to be addressed → preferences; **chat persistence** (recent/incomplete/starred, ~10 cap, notify near limit, more for paid). [LOCKED]
- **Availability engine:** unified free/busy, reasonable-hours + duration filters, partial windows. Defaults **06:00–22:00 / 60-min / 7-day**, all user-changeable; per-user working-hours preferences. Short display **cache (~60–120s)** for speed, but **always fetch live at the moment of booking** so no conflict slips through. (60-min = default hangout *duration* — the engine looks for free gaps at least that long; set per plan.) [LOCKED]
- **Venues:** OpenStreetMap for v1 (Google Places on wishlist if strictly better). **Travel-fairness** prioritized over midpoint. All activity/venue types **except alcohol, sexual, illegal** (content policy below). Saved/favorite venues per group in preferences. [LOCKED]
- **Poll/plan mechanism (redesigned into modes — see below).** Vote deadlines + auto-expire (user-set); non-voter reminders; members can **propose alternative times**; opt-in **auto-book if everyone says yes**; booking invites **yes-voters** to their calendars. [LOCKED]
- **Events & tasks** kept in v1; personal/anonymous busy sharing kept; full CRUD incl. **edit**. **No unilateral group events:** a thing reaches another member's calendar *only* via their consent (a poll yes-vote, or an invite they accept) — a directly-created event is the creator's own and shows to the group only as busy time. RSVP kept but lightweight (overlaps poll attendance). [LOCKED]
- **Notifications:** in-app + email + push first; channels/types configurable in a Settings > Notifications section (pending vote, locked-in, reminders, review nudges). [LOCKED]
- **Billing skeleton:** entitlement/quota system, everyone Free; processor deferred. Tiers: Free (most/all *manual* features, strict AI limits ~5–10 turns/day) → Pro (more AI, better features, extra themes) → Team (Pro + more AI + team features) → Max (generous everything + enterprise "contact us"). **Per-user** pricing incl. a Team plan. Only AI limits (and maybe in-app chat) gated by paid for now. [LOCKED structure; numbers DISCUSS]
- **Legal:** template-based Privacy + ToS (individual operator); export/delete manual-on-request for v1. [LOCKED]
- **Design:** keep glassmorphism (with fixes below); dark mode nice-to-have; responsive/native later; app-only first, marketing page before launch. [LOCKED]
- **Infra:** managed Postgres (Neon or equiv), error tracking (Sentry or equiv), encrypted tokens, backups; open to moving off Render; **avoid boycotted vendors — flag any.** [LOCKED intent]
- **Process:** write tests once an idea is confirmed; PRs on feature branches into the integration branch; keep `main` untouched until told. [LOCKED]

## Redesigned poll/plan mechanism (answers "is it overkill?")
Keep the underlying engine (interest + time votes), but expose it as **modes** so the
two-stage cascade is a *choice*, never mandatory:
- **Quick plan** — place + time already set → one **yes/no**; yes-voters get it (auto-book if enabled, else host locks in).
- **Pick-a-time** — place set, choose among candidate times → time vote only.
- **Float-an-idea** — gauge interest first, then times → the full two-stage cascade.
The composer auto-selects the simplest mode from what the user filled in. Plus:
deadlines/auto-expire, member-proposed times, auto-book-if-all-yes, reminders. [LOCKED]

## Host model (answers "host shouldn't cancel for everyone")
- A host leaving/cancelling does **not** kill the plan. The group can **keep it**, **elect a new host**, or go **hostless**. [LOCKED intent]
- Roles exist to gate: manage membership (invite/remove), rename/delete group, transfer ownership, (later) billing. Default group creator = owner = default host. [LOCKED]
- **Default when a host cancels: keep the plan and auto-book-if-all-yes**, with a notification letting the group pick another rule (majority-by-deadline / designate a decider / elect a new host). The richer host-handover options are a specific edge case → **[LATER]**; v1 ships only the sensible default. [LOCKED]

## Editing events & tasks (decided 2026-07-31)
- **Personal events belong to their owner.** Nobody else can edit, change or delete
  another member's personal event — not the group, not the group's creator. [LOCKED]
- **Shared group events/tasks are not unilaterally editable — not even by their
  creator.** Any member can *propose* a change; it goes back to the group as a vote.
  **Every field** goes through this, titles included: a meeting renamed from "quarter
  goals" to "week analysis" is exactly as material as a moved time. [LOCKED]
- **Majority wins, but the creator applies it.** The vote produces a verdict; a human
  presses go. This preserves the app's standing rule that nothing reaches a real
  calendar without a person deciding (see `tools/plan_rules.py` and the Plan
  docstring) — the creator can apply what the group approved or drop it, never
  override it. [LOCKED]
- **Attendees are notified on every applied edit** (`sendUpdates="all"`), including
  title-only changes. [LOCKED]
- Deleting a shared event is, for now, creator-only — tightened from "any member",
  which was a live hole. Whether a delete should itself need a vote is open. [DISCUSS]
- **Leader-decides variant** — in a workplace group the leader changes the time
  directly, taking the group's stated preferences into account rather than voting.
  Belongs with *different group types*, already [LATER] below. [LATER]
- **Change history** — an audit trail of who changed what and when on an event or
  task, so an edit is explicable after the fact. [LATER]

## Content & safety policy
- The agent and venue search will **not** suggest or plan around **alcohol, sexual, or illegal** activities/venues (drop bar/pub/alcohol venue types; scope-guard refuses these). [LOCKED]

## Accepted fixes (from the audit + this pass)
Security/reliability: encrypt OAuth tokens; session TTL; per-user LLM quota; model
fallback ladder; deterministic host-action endpoints; injection hardening. Perf: fix
N+1 + double tally queries; window the events fetch; FK indexes; freebusy cache;
replace 5s poll with SSE. Data: managed Postgres + backups; delete dead `polls`/`votes`
tables + stray `orbi.db`, reset & reseed local DB. Agent: prompt-cache the static
prefix so only dynamic bits (now/memory/slots) vary each step; raise/relax MAX_STEPS
per tier with smarter loop detection; persist transcripts; stream steps (drop fake
setTimeout pacing). Sync: two-way + settings toggle. All [LOCKED] unless in DISCUSS.

## Resolved (were open discussion items) — all decided 2026-07-25
1. Unified availability — **confirmed** (one user = one merged free/busy).
2. Microsoft/Outlook — **in v1** (build `CalendarProvider` abstraction; Google + Microsoft both shipped; caveat: some uni/work MS tenants require IT-admin consent, outside our control).
3. Auth — email **+ password** with reset/verification **+ optional magic-link** + Google + Microsoft.
4. Availability freshness — **short cache (~60–120s) + always live at booking**.
5. Title reading — **opt-in per connected calendar** (a switch per `CalendarAccount`, beside colour/sync), clear toggle + consent. Titles pulled in are **visible to their owner only**: groupmates still see opaque busy blocks, so the freebusy-only promise made to everyone else is unchanged. Refined 2026-07-31 — the point is that a person stops leaving Nudgy to check what they have (and it carries into solo mode). [LOCKED]
6. Hostless plan — **default keep + auto-book-if-all-yes**; richer options [LATER].
7. Free-tier AI turns — **placeholder ~8/day** (a "turn" = one user→agent message; internal steps are capped separately). Revisit with a cost model.
8. Recurrence — **decide during Phase 2** (include only if low-friction, else [LATER]).
9. Reviews/taste — **out of v1 core**; build after v1, before launch.

## Definitions
- **AI turn** = one user message to the agent → one complete answer (which may use several internal LLM/tool steps, capped so one turn can't run away). Manual actions (polls/events/tasks) cost **no** turn. Quota is counted in turns per user per local day.

## Extra-features backlog
Yours: **friends graph** (add by unique id/handle; in-app invites) — friends partly in
v1 (invites), full graph [LATER]; **phone-number login** [LATER + FLAGGED: SIM-swap /
number-recycling security risk, not recommended as primary auth]; **linked plans /
projects** (meetings + tasks + recurring) [LATER]; **in-app group + private chat**
[LATER, maybe Free]; global place/activity pages + opt-in global reviews [LATER];
Apple/iCloud + Notion + more integrations [LATER]; chat-with-agent from outside the app
(WhatsApp/Slack/etc.) [LATER]; different group types with different features [LATER];
event **importance/priority** (team/class meetings outrank casual) [LATER].
Mine: recurring plans, smarter venue ranking, host analytics, shareable read-only plan
links [LATER].

## UI ideas noted
- Possibly move **friends** to a top-right list (keep **groups** in the left sidebar); watch for conflict with the chat-orb UI. [DISCUSS/design phase]
- Multi-calendar differentiation via color/hover. [design phase]
- Glassmorphism kept, but fix: contrast/readability, `backdrop-filter` performance on
  mobile, and offer a "reduced transparency" accessibility mode. [design phase]
