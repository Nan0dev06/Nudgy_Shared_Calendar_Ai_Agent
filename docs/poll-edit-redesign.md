# Poll, voting & editing — redesign (decided 2026-08-01)

> Supersedes the poll mechanics in `v1-decisions.md` §"Redesigned poll/plan
> mechanism" and **amends** §"Editing events & tasks". Authoritative for both.
> Nothing here is built yet. Read `plan_rules.py`, `plan_service.py`,
> `plan_deadlines.py`, `event_rules.py` for what exists today.

## Why this exists

A verification pass on 2026-08-01 found three things marked `[LOCKED]` in
`v1-decisions.md` that were never built (poll modes, member-proposed times,
shared-event editing), and one structural gap nobody had written down: the
availability engine reads **only** external calendar freebusy, so in-app events
and RSVPs do not affect who is considered busy — while "usable with no external
calendar" is itself `[LOCKED]`. Rather than patch around the drift, the poll and
edit logic were redesigned from first principles.

**The principle that survives untouched:** nothing reaches a person's real
calendar without that person's own consent. Every rule below is checked against
it. What changed is the recognition that **a voter's own yes IS that consent** —
the host's lock-in was never protecting anyone's calendar, only choosing which
time. That is what makes automatic convergence safe.

---

## 1. The poll engine

### 1.1 Modes (composer-level, not engine-level)

Three modes, auto-selected from what the composer was given. They differ only in
**how many questions get asked** — one engine underneath.

| Mode | Composer state | Questions asked |
|---|---|---|
| **Quick plan** | place + exactly one time | one yes/no |
| **Pick-a-time** | place + 2..8 times | time votes only |
| **Float-an-idea** | no times yet | interest, then times when added |

**Interest exists only in Float-an-idea.** In the other two, a yes on any time
*is* the interest signal — asking separately is a redundant tap. This is the
single biggest cost the current engine imposes: "pizza Friday 8pm, in?" costs
two questions and two round-trips today.

### 1.2 Times are voted on in parallel, with a spotlight

Every candidate time is votable from the moment it exists. No queue, no walking.

The host may **spotlight** one time — "we're leaning toward this". The spotlight:

- emphasizes one card in the UI,
- sharpens reminder copy ("the group's leaning toward Fri 7pm, you haven't said"),
- **breaks ties at the deadline.**

**Moving the spotlight resets nothing.** This is the whole difference from
today's `advance_to_next_time`, which made prior votes irrelevant by design. The
new move is idempotent and reversible: spotlight 7pm, change your mind, go back
to 5pm — every vote already cast still counts. That property is why the spotlight
is cheap enough to keep.

### 1.3 Vote states: yes / no / if needed

Three states per candidate time. `if needed` is Doodle's "if-need-be": *I can
make this work, I'd rather not.* With every time visible at once it materially
improves outcomes, because it distinguishes "impossible" from "inconvenient".

`Can't-ever` (never works for me, e.g. never Sundays) is **waitlisted until after
launch** — useful signal, another concept to teach.

### 1.4 Every poll carries a minimum

> **Amended as built (2026-08-01, commit `aebfd96`).** The original text below
> said the composer prefills a *majority of the group*. It doesn't, and shouldn't:
> a prefilled number is still the app guessing, and any number can be reached by
> the wrong people. What shipped is stricter.

The bar is **a rule by default, a number only when a human types one**:

- `Plan.expected_count` **NULL** → the default rule: *every account-holding
  member must be able to make the time.* Not a count, so **guests can never
  satisfy it** — people who joined through a link are not the group, and letting
  them substitute would mean sharing a link makes a plan book *easier*.
- `Plan.expected_count` **= an int** → the creator said how many people is
  enough, so anyone who said yes counts toward it, **guests included**.

Both regimes live in exactly one place, `plan_rules.TimeResult.qualifies`, so
they cannot drift apart. `plan_service.minimum_for` renders the bar as a number
for *display only*; `requires_all_members` says which regime is in force.

This closes the gap that makes automatic convergence dangerous: without it, "the
most-voted time wins" books a 10-person outing for the 3 people who answered.
With it, booking below everyone is impossible **unless a human deliberately
typed a smaller number**. The minimum is the group's stated answer to "how many
of us make this worth doing?" — the app never guesses it.

The bar constrains **automatic** convergence only. A host lock-in books whatever
time they pick for whoever said yes, minimum or not (`plan_service.confirm_time`).

### 1.5 Convergence

At the deadline, in order:

1. Rank candidate times by **yes** count (for *ranking*, members and guests count
   identically — they differ only in whether they can satisfy the bar, §1.4).
2. If a time meets the minimum on yes alone → it wins.
3. Otherwise, allow `if needed` to count toward the minimum. Among times that now
   reach it, most **yes** wins.
4. Tie → the spotlit time. Still tied → earliest start.
5. Nothing reaches the minimum → the poll **expires**. Votes are kept. The host
   can extend the deadline, add times, lower the minimum, or lock in manually.

The winner books **for its yes and if-needed voters only** — the existing
`_confirm` subset rule, unchanged.

`Plan.auto_book` is **deleted**. Best-time-wins-at-deadline *is* auto-booking,
with a better rule than unanimity; keeping both would restore two convergence
paths. `everyone_said_yes()` goes with it.

### 1.6 Who controls what

| Action | Who |
|---|---|
| Create a poll | any member |
| Add candidate times | **any member** (new — `[LOCKED]` and never built) |
| Vote | any member; guests via share link |
| Move the spotlight | host |
| Set/extend/clear the deadline | host |
| Change the minimum | host |
| Share / revoke the vote link | any member |
| Lock in early | host |
| Delete the poll | host |

**Members propose, the host decides.** A member who spots a time that works for
everyone can put it up; nobody but the host commits the group to anything.

### 1.7 Status machine

`open → booked | expired`

`dead` is **deleted** — it only ever meant "the host walked off the end of the
queue", which cannot happen when all times are live at once. `expired` means the
deadline passed without reaching the minimum; the host still has moves.

---

## 2. A booked poll becomes a real event

Locking in creates a `GroupEvent` whose attendees are the yes/if-needed voters,
carrying their `EventRsvp` rows (`going`). The poll stays visible as that event's
history — how the group arrived at this time.

Today `Plan` bookings and `GroupEvent` are strangers, and `EventRsvp` explicitly
excludes poll bookings, which is why **a booked poll has no edit path at all**.
This unification is what gives it one.

---

## 3. Editing: RSVP-reset, not a second vote

**Amends the `[LOCKED]` decision of 2026-07-31** (every field to a group vote,
majority wins, creator applies). That design was rejected for two reasons:

- It is a **second vote engine** beside the poll engine — duplicate UI, duplicate
  notifications, duplicate history.
- **Majority is the wrong rule for calendars.** If 4 of 6 vote to move 5pm→7pm,
  the 2 who can't make 7pm are handed an event they never agreed to. Majority
  rule over other people's time contradicts the principle the app is built on.

### The rule

- **Only the creator edits** a shared event. Anyone else's change arrives as a
  *suggestion* the creator applies or drops — which preserves "any member can
  propose a change" without creating a second authority.
- **Material edits** (title, start, end, location) reset every attendee to
  `needs_reconfirm`. **Non-material edits** (category, notes) apply directly with
  a notification.
  - Title is material on purpose: a meeting renamed "quarter goals" →
    "week analysis" is exactly as material as a moved time. That reasoning from
    the original decision survives intact.
- **Non-reconfirmed attendees stay tentative until the event**, and are dropped
  only if they never answer. A 10-minute shift must not cost a silent yes-person
  their spot.
- Applied edits notify attendees (`sendUpdates="all"`) and push to the real
  calendar — this is where `provider.update_event` (implemented in both
  providers, called by nothing, guarded by a deliberate 409 in
  `event_routes.py`) finally gets wired.

Each person still individually consents to what sits on their calendar — the same
guarantee as a vote, a fraction of the machinery. It is also what Google Calendar
does natively (a time change resets responses), so it composes with two-way sync
instead of fighting it. And "who re-confirmed after which change" gives change
history nearly for free.

**Accepted cost:** a creator can effectively push people off an event by editing
it. Judged acceptable — that is "the plan changed and you couldn't make it",
which is a real outcome, not a bug.

---

## 4. Availability must learn about in-app events — **BUILT 2026-08-01**

`fetch_busy_for_group()` reads only connected Google/Microsoft freebusy. There is
no reference to `GroupEvent` or `EventRsvp` anywhere in `availability.py`. A
member with no connected calendar is marked `connected=False` and dropped from
the intersection entirely.

Consequences today: in-app events block nothing, RSVPs affect nothing, and a
group using Nudgy as its calendar has an availability engine that sees an empty
world — while calendar-optional is `[LOCKED]`.

**In-app events join the busy union**, weighted by attendance state:

| State | Counts as |
|---|---|
| `going` | busy |
| `needs_reconfirm` | **busy** |
| `maybe` | busy |
| `cant` | free |

`needs_reconfirm` counts as busy because it *was* busy a moment ago and probably
still is — treating a pending re-confirm as free invites double-booking someone
who is still coming. A soft-busy tier (avoid when possible, not disqualifying)
is the better long-run answer but `slots.py` is binary intervals; deferred.

### What shipped

`repo.get_busy_events_for_users()` — one batched, windowed query turning in-app
events into busy intervals per user. Rules: personal events block their owner
only; shared events block their creator plus `going`/`maybe` RSVPs and **nobody
else** (a shared event doesn't own your time until you say you're coming — the
same consent rule the plan cascade runs on); tasks never block (a due date is an
instant, not an appointment); events count **across groups**, since a group is a
planning context, not a separate time universe.

`fetch_busy_for_group()` now merges that with external freebusy. `MemberBusy`
gains `in_app_blocks` and `has_source`, and **every member with a source joins
the intersection** — previously only externally-connected ones did, which
silently ignored anyone using Nudgy as their calendar.

`compute_availability()` returns `members_with_source` and `members_unknown`.
The API's "blank the slots" guard moved from `members_connected` to
`members_with_source`, so a group that connected nothing but keeps its events in
Nudgy now gets real slots. The agent's prompt rule was rewritten to judge unknown
availability by `members_unknown` — being un-connected is no longer the same as
being unknown.

`RSVP → busy` lives in exactly one place: `repo.BUSY_RSVP_STATUSES`. Adding
`needs_reconfirm` to that tuple is the entire availability half of §3.

Covered by `backend/tests/test_availability_in_app.py` (11 tests).

---

## 5. What this deletes / what survives

**Deleted:** `advance_to_next_time`, the `queued/active/skipped` round state
machine, the `dead` status, `auto_book`, `everyone_said_yes`, the mandatory
interest gate, most of `_host_note`'s branching, and the "5PM is a different
question from 7PM" re-ask concept.

**Survives intact:** `_confirm`'s booking path and its member-vs-guest invite
split (careful work — a display label must never be handed to Google as a
recipient), `plan_deadlines.next_reminder_at` / `reminder_due`, the guest voting
model, `event_rules.py`'s shape, the SSE poke layer, and the deterministic
host-action endpoints.

## 6. Still open

- **FLAG BEFORE LAUNCH — guest de-duplication.** A guest's yes counts toward a
  creator-typed minimum, so one person voting twice can carry a poll over its
  own bar. Today the defence is: a signed cookie, unique names per plan, and
  reclaim-by-email on join. Email is **optional** and asking for it is not a
  login — but that means the reclaim only protects guests who happen to give
  one. In beta we collect emails anyway, so the gap is masked. It stops being
  masked at launch. Decide then whether a typed minimum should count guests at
  all, or whether counting them should require an email.

- Whether cancelling a shared event needs anything beyond creator-only.
- `can't-ever` vote state — post-launch.
- Soft-busy tier in `slots.py`.
- Host handover when the host leaves the group (existing `[LATER]`).
