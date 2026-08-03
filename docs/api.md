# Nudgy REST API

Base URL (dev): `http://localhost:8000` · Interactive docs: `http://localhost:8000/docs`

**Auth model:** Google OAuth *is* the login. After the OAuth callback the server
sets an `nudgy_session` cookie (signed, httponly). Every endpoint below except
the two `/auth/google/*` ones requires that cookie — the browser sends it
automatically; with `fetch()` use `credentials: "include"`.

Errors are always `{"detail": "<human-readable message>"}` with an appropriate
status (401 not logged in, 403 not your group, 404 not found, 400 bad input).

---

## Auth

### `GET /auth/google/login`
Redirects (302) to Google's consent screen. Frontend: just
`window.location = "/auth/google/login"`.

### `GET /auth/google/callback`
Google redirects here. Exchanges the code, creates/updates the user, sets the
session cookie, then redirects to `/`. The frontend never calls this directly.

### `GET /auth/me`
Who is logged in.

Response `200`:
```json
{
  "email": "nan0.al.shami2006@gmail.com",
  "timezone": "Asia/Beirut",
  "calendar_connected": true
}
```
Response `401`: `{"detail": "Not logged in. Connect Google first."}`

### `PATCH /auth/me`
Update profile fields. Both optional; timezone must be an IANA name.

Request:
```json
{"display_name": "Hussein", "timezone": "Asia/Beirut"}
```
Response `200`: same shape as `GET /auth/me` (which also includes
`display_name`).

### `POST /auth/logout`
Clears the cookie. Response `200`: `{"ok": true}`

---

## Groups

### `POST /groups`
Create a group; the creator is automatically the first member.

Request:
```json
{"name": "Beirut Crew"}
```
Response `200`:
```json
{"id": 1, "name": "Beirut Crew", "invite_code": "4PYJU8"}
```

### `POST /groups/join`
Join by invite code (idempotent — joining twice is fine).

Request:
```json
{"invite_code": "4PYJU8"}
```
Response `200`:
```json
{"id": 1, "name": "Beirut Crew", "invite_code": "4PYJU8"}
```
Response `404`: `{"detail": "No group with that invite code."}`

### `GET /groups`
Groups the current user belongs to.

Response `200`:
```json
[{"id": 1, "name": "Beirut Crew", "invite_code": "4PYJU8"}]
```

### `GET /groups/{group_id}/members`
Response `200`:
```json
[
  {"email": "nan0.al.shami2006@gmail.com", "calendar_connected": true},
  {"email": "nano.06dev@gmail.com", "calendar_connected": true}
]
```
Response `403`: `{"detail": "You are not in this group."}`

### `GET /groups/{group_id}/availability?days_ahead=7&duration_minutes=60`
Live free/busy for the calendar UI. Hits Google's freebusy endpoint for every
connected member at call time (expect a couple of seconds). Privacy unchanged:
busy **ranges only** — never titles or details.

Response `200` (abridged):
```json
{
  "timezone": "Asia/Beirut",
  "members_connected": 2,
  "members_busy": [
    {"email": "a@x.com", "connected": true,
     "busy": [{"start_iso": "2026-07-18T12:00:00+03:00", "end_iso": "2026-07-18T14:00:00+03:00"}]}
  ],
  "common_slots": [
    {"start": "Sat 18 Jul 18:00", "end": "Sat 18 Jul 22:00",
     "start_iso": "2026-07-18T18:00:00+03:00", "end_iso": "2026-07-18T22:00:00+03:00",
     "duration_minutes": 240}
  ]
}
```
With zero connected calendars, `common_slots` is always `[]` (an empty
intersection is not "everyone is free"). Google errors return
`{"members_busy": [], "common_slots": [], "error": "..."}` instead of a 500.

---

## Events & tasks

In-app events/tasks a member creates directly (poll bookings stay on polls).
Google sync mirrors booking.py: one event on the creator's primary calendar,
members as attendees, `sendUpdates="all"` — Google updates everyone's calendar
and emails invites. The inbound half of "two-way" is the availability endpoint
above: whatever people do in Google Calendar shows up as busy blocks.

### `GET /groups/{group_id}/events`
Chronological; tasks without a due date last.

Response `200`:
```json
[
  {"id": 1, "kind": "event", "title": "Dinner", "category": "Event",
   "location": "Kalei", "start_iso": "2026-07-18T19:00:00+03:00",
   "end_iso": "2026-07-18T21:00:00+03:00", "done": false,
   "synced": true, "gcal_link": "https://...", "created_by": 1}
]
```

### `POST /groups/{group_id}/events`
```json
{"kind": "event", "title": "Dinner", "category": "Event",
 "location": "Kalei", "start_iso": "2026-07-18T19:00:00+03:00",
 "end_iso": "2026-07-18T21:00:00+03:00",
 "invite_emails": ["b@x.com"], "sync_google": true}
```
`kind: "task"` needs no times (`start_iso` doubles as the due date).
`invite_emails` is filtered to group members; empty = everyone. A Google sync
failure never loses the in-app event — the response carries
`"sync": {"ok": false, "reason": "..."}`.

### `PATCH /events/{event_id}`
Check a task off and/or edit the event. Every field is optional; a field is
changed only if the key is present, so `{"location": null}` clears the location
while omitting it leaves it alone.

```json
{"done": true, "title": "Dentist", "category": "Event", "location": "Hamra",
 "start_iso": "2026-07-20T09:00:00+03:00", "end_iso": "2026-07-20T10:00:00+03:00",
 "anonymous": false}
```

`kind` and `personal` are not editable — each decides which rules govern the row.
An empty body is `400`. Response `200`: the updated event.

**Permissions** (`tools/event_rules.py`):

| | edit fields | tick `done` | delete |
|---|---|---|---|
| **your personal** event/task | ✅ | ✅ | ✅ |
| **someone else's personal** | ❌ | ❌ | ❌ |
| **shared group** event/task | ❌ *goes to a group vote — not built yet* | ✅ anyone | ✅ creator only |

A refusal is `403` and the `detail` explains which rule applied — it's meant to
be shown to the user verbatim.

### `DELETE /events/{event_id}`
Removes the event; if it was synced, also deletes the Google copy (best
effort, via the creator's token). Creator-only (see the table above); `403`
otherwise. Response `200`: `{"ok": true, "gcal": {...}}`

---

## Chat (the Nudgy orb)

### `POST /chat`
One turn of the agent. **Stateless**: the frontend keeps the conversation and
sends it back as `history` each time (max 40 items). `group_id` may be null if
the user has no group yet — Nudgy will tell them to create/join one.

Request:
```json
{
  "group_id": 1,
  "message": "Find a time this week when everyone's free",
  "history": [
    {"role": "user", "content": "hey nudgy"},
    {"role": "assistant", "content": "Hi! I can help your group find a time to meet."}
  ]
}
```

Response `200`:
```json
{
  "reply": "Everyone is free Thursday 17 July between 18:00 and 22:00 — it's the only evening this week with no conflicts for both of you. Want me to look at other days?",
  "trace": [
    {"kind": "tool_call",   "name": "get_group_members",  "detail": {}},
    {"kind": "tool_result", "name": "get_group_members",  "detail": {"group_name": "Beirut Crew", "member_count": 2, "members": [...]}},
    {"kind": "tool_call",   "name": "find_meeting_slots", "detail": {"days_ahead": 7, "duration_minutes": 60}},
    {"kind": "tool_result", "name": "find_meeting_slots", "detail": {"common_slots": [...], "...": "..."}},
    {"kind": "text",        "name": "",                   "detail": "Everyone is free Thursday..."}
  ]
}
```

`trace` is the agent's visible reasoning loop — render it as collapsible
"Nudgy is checking calendars…" steps if you want the agentic feel in the UI.
All times inside `reply` are already in the user's local timezone.

Notes for the UI:
- The call can take several seconds (live Google Calendar queries + the model).
  Show a thinking state on the orb.
- Each call uses Groq free-tier quota — don't auto-fire it; only send on user action.

---

## Plans & voting (polls)

> Rewritten 2026-08-03 against the code. The engine was redesigned on 2026-08-01
> (`docs/poll-edit-redesign.md` §1) and this section described the *previous*
> one — a queue of times with a single "active" round, an interest→time cascade,
> and host moves that only worked through chat. None of that exists any more.

A **plan** (the UI calls it a poll) is one place, one day, and a **set of
candidate times that are all votable at once**. There is no active round and no
queue: a member answers whichever times they like, in any order, whenever.

**Three modes**, derived rather than stored (`plan_rules.mode_of`) — a stored
mode could contradict the plan it describes:

| mode | when | what members answer |
|---|---|---|
| `quick` | 0–1 candidate times, `asks_interest=false` | one yes/no |
| `pick_a_time` | 2+ candidate times | every time, independently |
| `float` | `asks_interest=true` | interest first; times as they arrive |

Interest exists **only** in Float. Everywhere else a yes on any time *is* the
interest signal, so asking separately would be a redundant tap.

**Three vote states**, not two: `yes`, `no`, `if_needed`. "If needed" is
Doodle's if-need-be — *I can make this work, I would rather not* — and it counts
toward the minimum only when `yes` alone cannot reach it.

**The minimum** is how many members must be able to make a time before it books
**without a human**. Omitted at creation it is the *rule* "every account-holding
member", and `requires_all_members` is `true`; a guest can never substitute for
a member under that rule. Type a number and it becomes a count that guests do
count toward. Either way the minimum never constrains the host — a lock-in books
whatever time the host names, for whoever said yes or if-needed.

**Two host moves**, both REST, neither routed through the model: `spotlight`
(lean toward a time — resets nothing, reversible) and `lock-in` (commit and
book). The old `POST /plans/{id}/next-time` is **gone**: it made every prior
vote irrelevant, so hosts avoided using it.

### `GET /groups/{group_id}/plans`
Newest first, max 10. Every plan carries the caller's own ballot and their own
answer per time; `host_box` and `share_url` appear **only for the host**.

Response `200`:
```json
[
  {
    "id": 3,
    "title": "Coffee catch-up",
    "location": "Kalei Coffee, Mar Mikhael",
    "day": "Monday 20 July",
    "status": "open",
    "host": "nan0.al.shami2006@gmail.com",
    "is_host": false,
    "mode": "pick_a_time",
    "asks_interest": false,
    "minimum": 4,
    "requires_all_members": true,
    "guest_count": 1,
    "spotlight_round_id": 6,
    "deadline_iso": "2026-07-19T18:00:00+00:00",
    "voting_open": true,
    "times": [
      {
        "round_id": 5, "ordinal": 0, "label": "Mon 20 Jul 17:00-18:00",
        "start_iso": "2026-07-20T14:00:00+00:00",
        "end_iso": "2026-07-20T15:00:00+00:00",
        "booked": false, "event_link": null, "spotlit": false,
        "yes": 2, "if_needed": 1, "no": 1, "waiting": 0, "guest_yes": 1,
        "qualifies": false,
        "suggested_by": "bea@x.com", "can_remove": false,
        "my_answer": "yes"
      }
    ],
    "ballot": {"stage": "time", "note": "2 times still need your answer.", "unanswered": 2}
  }
]
```

`status` is `open` | `booked` | `expired`. (`scheduled` and `dead` are gone.)
`day` and every `label` are already in the caller's timezone; `start_iso` /
`end_iso` are the raw UTC instants, for placing a booked time on a calendar and
for spotting duplicate proposals.

Per time: `yes` counts members only, `guest_yes` folds guest yes + if-needed
together, and `qualifies` is whether that time currently meets the minimum.
`my_answer` is `"yes"` | `"no"` | `"if_needed"` | `null` — what *this* caller
said, so their own choice stays visible. `can_remove` is true only when the
caller suggested that time, it is not booked, and the poll is still open.

`ballot.stage` tells the UI what to render:

| stage | render |
|---|---|
| `interest` | Float only — the plan question + in/out |
| `time` | the time grid; `unanswered` is how many they still owe |
| `waiting` | just `note` — they have answered everything on the table |
| `out` | just `note` — they said no to the plan (Float only) |
| `closed` | just `note` — booked or expired |

For the host only:
```json
"share_url": "https://nudgy.example/?share=abc123",
"host_box": {
  "interested": ["bea@x.com"],
  "not_interested": ["cal@x.com"],
  "no_answer": ["eve@x.com"],
  "note": "Fri 7pm works for 4 of 5. Your call: lock it in, or lean toward it."
}
```
Those three lists are about **interest**, so they are only populated in Float.
Per-time standing lives on `times[]` — the whole grid renders from one GET.

### `POST /groups/{group_id}/plans`
Create a poll. Same shape the agent's `create_plan` tool uses.

Request:
```json
{
  "title": "Coffee catch-up",
  "location": "Kalei Coffee",
  "slots": [{"start_iso": "2026-07-20T14:00:00Z", "end_iso": "2026-07-20T15:00:00Z"}],
  "expected_count": null,
  "deadline_iso": "2026-07-19T18:00:00Z"
}
```
`slots` is capped at 6 and may be empty — that is how a Float poll starts.
`expected_count` omitted or `null` means the default rule (every member).
Response `200`: the plan object above.

### `PATCH /plans/{plan_id}`
Host-only. Move or clear the vote deadline, change the minimum.

```json
{"deadline_iso": "2026-07-21T18:00:00Z", "minimum": 3}
```
Omit a field to leave it alone; send `"deadline_iso": null` to clear it.
**Works on an `expired` poll, deliberately** — a fresh deadline reopens voting,
which is how a host says "a couple of you never answered, take another day"
instead of rebuilding the poll. Lowering the minimum can retroactively make a
complete poll bookable, so convergence is re-checked on every call.
Response `400`: the plan is already booked.

### `POST /plans/{plan_id}/rounds`
**Any member** adds candidate times to an open poll — members propose, the host
decides. New times are votable immediately and disturb no existing vote.

```json
{"slots": [{"start_iso": "...", "end_iso": "..."}]}
```
1–6 slots. Response `200`: the plan object.

### `DELETE /plans/{plan_id}/rounds/{round_id}`
Take back a time **you** suggested, before it is booked.
Response `403` if you did not suggest it — removing someone else's would let one
member quietly delete the option the group was converging on, and its votes.
Response `400` if it is booked. Response `200`: the plan object.

### `POST /plans/{plan_id}/spotlight`
Host-only. Lean toward one time, or clear the spotlight with `null`.

```json
{"round_id": 6}
```
Nothing is skipped, nothing is reset, and it can be moved back.
Response `200`: `{"action": ..., "note": ..., "plan": {...}}`

### `POST /plans/{plan_id}/lock-in`
Host-only. Book the time the host **names** — not the spotlit one, because
"what we are leaning toward" and "what we are committing to" are different
statements, and coupling them would make locking in a different time a two-step
dance. Books for everyone who said `yes` or `if_needed`.

```json
{"round_id": 6}
```
Response `200`:
```json
{"action": "booked", "round_id": 6, "time": "Fri 25 Jul 19:00-20:00",
 "attendees": ["bea@x.com"], "event_link": "https://calendar.google.com/...",
 "plan": {...}}
```
Response `502`: the calendar write failed — retryable, and the round is put back.
Response `400`: the plan is already booked, or the round is not valid.

**Not gated by the minimum.** That bar governs what books *without* a human;
this is a deliberate, deterministic path to the calendar.

### `POST /plans/{plan_id}/interest`
**Float polls only.** Response `400` on any other mode: *"This poll asks about
times directly — just answer the times."*

```json
{"yes": true}
```
Response `200`: the plan object.

### `POST /plans/{plan_id}/time-vote`
Answer one candidate time.

```json
{"round_id": 5, "answer": "if_needed"}
```
`answer` is `yes` | `no` | `if_needed`; anything else is a `400`.
Response `403`: Float poll and you have not said you are in yet.
Response `404`: that round is not one of this poll's candidates.
Response `200`: the plan object.

**There is no `409` any more.** Every time is answerable independently, so there
is no active round to race against — the old "the host moved on" conflict was an
artifact of the queue.

**The vote that completes a poll is the one that books it.** When a vote is the
last outstanding answer and some time meets the minimum, `plan_service.converge`
books it there and then; nothing is left for anyone to decide.

### `POST /plans/{plan_id}/share` · `DELETE /plans/{plan_id}/share`
Host-only. `POST` mints the public vote link (`?regenerate=true` mints a fresh
token, killing every copy of the old one at once); `DELETE` turns it off. Votes
already cast survive both — the people who cast them were invited in good faith.
Response `200`: `{"share_url": ..., "share_token": ...}` / `{"share_url": null}`

### `DELETE /plans/{plan_id}`
Host-only, any status. Deletes candidate times and votes with it; a calendar
event a booked round created is left in place.
Response `200`: `{"deleted": true, "plan_id": 3}`
