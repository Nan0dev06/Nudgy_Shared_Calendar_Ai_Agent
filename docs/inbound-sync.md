# Inbound calendar sync

> Built 2026-08-01 on `feat/inbound-calendar-sync`. This is the doc the code
> points at (`jobs/calendar_sync.py`, `db/models.ExternalEvent`,
> `agent/availability.py`).

## The problem it solves

Outbound sync always worked: book a plan and it lands on everyone's Google
Calendar. The way back was `freebusy`, which is opaque by construction. That was
enough to stop Nudgy double-booking anybody and **never enough to tell them
why their Tuesday was gone**. You'd see a grey block and have to leave the app
to find out what was in it.

Inbound sync mirrors external events into `external_events` so a busy block can
say what it is — to the person whose calendar it came from.

## The shape

Both providers answer the same question the same way: *hand back an opaque
token, get only what changed since*. That symmetry is the whole design, and it
sits behind two methods on `CalendarProvider`:

```
list_sync_calendars() -> [SyncCalendar]
sync_events(calendar_id, token, window_start, window_end, want_titles) -> SyncResult
```

`jobs/calendar_sync.py` drives them on a 5-minute tick, exactly as
`jobs/plan_ticker.py` drives deadlines: a plain synchronous `run_sync(session,
now)` that tests can drive with a frozen clock, wrapped in an asyncio loop owned
by `main.py`'s lifespan.

### Why webhooks will be an upgrade, not a rewrite

Neither provider's push notification carries a useful payload. Google's is
**empty** (everything is in headers); Graph's gives an id and an etag that may
already be stale. Both mean the same thing: *something on this calendar moved*.
The handler's entire job would be to call the stored token loop — which is
`_sync_calendar`, unchanged. **Webhooks replace the timer, not the sync.**

A polling safety net stays either way: Graph explicitly drops notifications for
slow endpoints and says dropped ones can't be recovered, and Google channels
lapse and need manual renewal.

## Per-provider mechanics

| | Google | Microsoft Graph |
|---|---|---|
| Call | `events.list` + `syncToken` | `/me/calendarView/delta` |
| Scope | **every calendar you own/edit** (one token each) | the mailbox's calendarView (one token) |
| Token | short opaque string | the whole `@odata.deltaLink` URL |
| Deletions | items with `status: "cancelled"`, id only | `@removed` stubs |
| Recurrence | expanded by `singleEvents=True` | expanded by calendarView |
| Invalidation | `410` + `reason: fullSyncRequired` | `410`, or 4xx + `syncStateNotFound` |
| Field control | `fields` mask — **can ask for less** | `$select` unsupported — **cannot** |

**Google's query is frozen for a token's lifetime.** `singleEvents`,
`showDeleted`, `maxResults` and the `fields` mask must be byte-identical on
every call; changing one mid-stream is *undefined behaviour*, not an error.
`timeMin`/`timeMax` are outright forbidden alongside a token (400), so the
window is re-applied on our side and anything that drifted outside it is dropped
as a deletion.

**Graph freezes the window INTO the token.** A token minted for a +120d horizon
is still a +120d horizon three months later — it does not roll. Hence
`plan_round`: keep the token while the stored horizon is comfortably ahead, and
start a fresh full round once `now` closes on it. That re-base is routine
maintenance, not an error path.

**The token only exists on the last page.** Both providers hand it over only
once paging completes, so a round that dies halfway has nothing to save and
replays from the previous token. That is safe *only* because every write is an
idempotent upsert on `(user_id, calendar_id, external_id)` — which is also why
rows and token commit in one transaction. Saving the token first would lose
those changes permanently; saving rows first merely repeats work.

## Privacy

Titles are **opt-in per connected calendar** (`CalendarAccount.read_titles`,
default off) and visible to **their owner only** — `v1-decisions.md` #5.

- **Google**: enforced in the *request*. With the opt-in off the field mask never
  asks for `summary`, so there is nothing to discard and nothing to leak into a
  log or a crash report.
- **Graph**: cannot be enforced in the request — `$select` is unsupported on a
  delta call, so the subject, body, attendees and location arrive whatever the
  user chose. The opt-in is enforced at ingestion in `_parse_delta_item`. The
  honest statement there is *"we don't keep it"*, not *"we never received it"*.

Nothing else about an event (description, attendees, organiser, body) is ever
mirrored, on either provider.

Flipping the opt-in clears every sync token on the account, because Google's
field mask is part of the frozen query and a token minted while titles were off
can never start returning them.

### Who sees a title

`members_busy` labels **the caller's own rows and nobody else's**. The same
group loaded by two people returns two different payloads: each sees their own
detail and everyone else's opaque busy time. Verified in
`tests/test_external_labels.py`, which is the file to read before touching any
of this.

Labels are **clipped to live free/busy** rather than asserted on their own. The
mirror is minutes stale by design, so an event deleted a minute ago is still in
it; without clipping the calendar would draw "Dentist, 15:00" over an hour the
slot math was simultaneously offering as free. Worst case now is an *unlabelled*
block — the old behaviour.

Availability itself is untouched: the mirror is **not** a third source of busy
time. Free/busy stays live at call time, and labelling cannot move a slot.

### Turning it off

Both destructive choices leave data behind that is the user's to keep or bin, so
the app asks instead of picking:

- **Titles off** → keep or remove the titles already pulled in. Times stay either
  way; they are what makes a block a block.
- **Disconnect** → keep or delete what was synced. "Keep" has to outlive the
  connection, so `ExternalEvent` hangs off `user_id` with a **nullable**
  `account_id` (NULL = frozen copy, no longer syncing). Reconnecting the same
  calendar re-adopts those rows rather than duplicating them.

Both default to *remove* on the server, so a client that forgets to ask errs
towards deleting text somebody withdrew consent for.

## Schema

- `calendar_accounts.read_titles` — the opt-in (`_LATE_COLUMNS`, FALSE for every
  existing connection).
- `calendar_sync_states` — one row per calendar: token, the window it was minted
  for, last success, last error.
- `external_events` — the mirror.

> `_backfill_calendar_accounts` is raw SQL and gets no Python-side defaults, so
> every NOT NULL column added to `CalendarAccount` must be named in it. Missing
> that made a fresh database fail to boot.

## Open question: `sync_setting` vs inbound

Inbound sync is gated on `read_titles`, **not** on `sync_setting`. A calendar set
to `one_way` — or even `none` — still has its events mirrored (untitled, unless
titles are on).

That follows the position already written into `repo.account_syncs_out`: *"the
inbound half is the freebusy availability read, which is independent of this and
always happens."* Under that reading `sync_setting` governs only what Nudgy
WRITES, and mirroring is just a better-informed read of what it was already
allowed to see.

But the names invite the other reading — `one_way` sounding like "Nudgy →
calendar only". Worth settling before beta. Note `one_way` and `two_way` are
still behaviourally identical today (`!= "none"`), so whatever is decided should
probably resolve both at once.

## Known limits

- **Microsoft mirrors one calendar.** Graph v1.0 documents delta only for the
  mailbox's own `calendarView`; the per-calendar form
  (`/me/calendars/{id}/calendarView/delta`) appears on the *beta* reference only.
  This matches what `MicrosoftCalendarProvider.get_busy` already reads, so the
  two stay in step — but a second Outlook calendar is invisible to both.
- **All-day events are pinned to UTC midnight.** Google sends a bare date with no
  zone; there is no correct instant. Flagged `all_day` for the UI.
- **Render's free tier sleeps after 15 min idle**, so the tick stops with it.
  Same caveat as `plan_ticker` — see `.claude/startup.md`. For a display mirror
  this degrades gracefully: it catches up on the next tick after wake.
- **No `iCalUID` dedup yet.** The same meeting on two connected calendars is two
  mirror rows. They merge into one busy block anyway, so the only visible effect
  is a repeated title in the hover.

## Verified against the live APIs (2026-08-01)

Not just fakes — the job ran against the real accounts connected on the dev
machine:

- Google discovered 3 calendars (`sadek06smith@gmail.com`, "Academic", "Academic
  Extracurriculars"), took a real `syncToken` for each, and mirrored 127 events
  with recurring series correctly expanded into instances.
- Graph returned a real `deltaLink`, stored whole and replayed.
- A second round on the stored tokens reported `added: 0, updated: 0` — proof it
  was genuinely incremental, since a rejected token would have re-read all 127.
- All 127 rows came in with `title = NULL`, because `read_titles` was off. The
  opt-in held against live data.
