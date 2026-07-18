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
`{"done": true}` — check a task off. Response: the updated event.

### `DELETE /events/{event_id}`
Removes the event; if it was synced, also deletes the Google copy (best
effort, via the creator's token). Response `200`: `{"ok": true, "gcal": {...}}`

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

## Plans & voting

A **plan** is one place, one day, and an ordered queue of candidate times. Plans
are created by Nudgy (via chat) **or directly via REST** (`POST
/groups/{group_id}/plans`, same shape as the agent's create_plan tool); members
answer through the two endpoints below.

**The cascade — two different questions.** Every member is first asked about the
plan *itself* (place + day, no time). A **no** there puts them out of the whole
plan and they are never asked a time. A **yes** immediately opens the time
question *for that member* — they do not wait for anyone else to answer. Only
**one** candidate time is ever on the table at a time. A **no** to a time means
"not at 5pm", not "not coming": they stay in the plan and get asked again if the
host moves to the next time.

**Nothing here decides anything.** No majority, no unanimity, no threshold, no
auto-booking, and a single no does not kill a time. Voting only advances that
one member through their own cascade. The **host** (the member who suggested it)
reads the tally and tells Nudgy to either lock the time in — which books *only*
the people who said that time works — or move to the next time. Both of those
happen through chat, not REST.

### `GET /groups/{group_id}/plans`
Newest first, max 10. Every plan carries **the current user's own ballot**;
`host_box` is present **only if the current user is the host**.

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
    "times": [
      {"round_id": 5, "ordinal": 0, "label": "Mon 20 Jul 17:00-18:00",
       "status": "active", "booked": false, "event_link": null},
      {"round_id": 6, "ordinal": 1, "label": "Mon 20 Jul 19:00-20:00",
       "status": "queued", "booked": false, "event_link": null}
    ],
    "ballot": {
      "stage": "time",
      "note": "Does this time work for you?",
      "round_id": 5,
      "time_label": "Mon 20 Jul 17:00-18:00"
    }
  }
]
```

`status`: `open` | `scheduled` | `dead` (every candidate time was used up).
`times[].status`: `queued` (held back) | `active` (being asked now) | `skipped`
(host moved on) | `confirmed` (host locked it in). `day` and every `label` are
already in the current user's timezone.

`ballot.stage` tells the UI exactly what to render for this member:

| stage | render |
|---|---|
| `interest` | the plan question + in/out buttons |
| `time` | the `time_label` question + yes/no buttons; POST with `round_id` |
| `waiting` | just `note` — they've answered everything on the table |
| `out` | just `note` — they said no to the plan |
| `closed` | just `note` — the plan is scheduled or dead |

`round_id`/`time_label` are non-null only when `stage` is `time`.

For the host only:
```json
"host_box": {
  "interested": ["bea@x.com", "dia@x.com"],
  "not_interested": ["cal@x.com"],
  "no_answer": ["eve@x.com"],
  "time_yes": ["bea@x.com"],
  "time_no": ["dia@x.com"],
  "time_waiting": [],
  "note": "For Mon 20 Jul 17:00-18:00: 1 of 2 interested can make it. IN: bea@x.com. OUT for this time (still in for the plan): dia@x.com. ... Your call: lock in ... or move to the next time ..."
}
```
The time columns only ever cover the interested cohort — someone who said no to
the plan is never counted as silent on a time they were never asked. Render
`note` as-is and let the host tell Nudgy what to do.

### `POST /plans/{plan_id}/interest`
Stage 1. Answering again replaces your previous answer.

Request:
```json
{"yes": true}
```
Response `200`: the updated plan object. **A `yes` comes straight back with
`ballot.stage == "time"`** — that is the cascade; render the time question
immediately without re-fetching.
Response `400`: `{"detail": "This plan is scheduled; voting is closed."}`

### `POST /plans/{plan_id}/time-vote`
Stage 2. Only the interested cohort may answer, and only the active time.
`round_id` is **required** so a vote cast while the host was switching times
can't silently land on the wrong one.

Request:
```json
{"yes": true, "round_id": 5}
```
Response `200`: the updated plan object.
Response `403`: `{"detail": "Say you're in for the plan first — times are only asked of people who are."}`
Response `409`: `{"detail": "The host moved on — the question is now Mon 20 Jul 19:00-20:00."}` — re-fetch and show the new question.
