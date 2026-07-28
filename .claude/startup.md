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
  branch `feat/async-voting`. **Open, awaiting merge.**
- **Guest voting** (shareable vote links, no account needed) — branch
  `feat/guest-voting`, stacked **on top of** `feat/async-voting`. Merge #16
  first, then this one.
- **Next up in Phase 2:** SSE to replace the 5s poll (the poll cadence is
  visible in the server log — it's the last piece of the async story), then
  conversation persistence / model router / injection defense.
- **Known gap worth a small branch:** the poll card's "Lock it in" and "Try the
  next time" still go through the LLM (`doSend`) even though the deterministic
  endpoints exist (`POST /plans/{id}/lock-in`, `/next-time`) and `api.js` could
  call them directly.

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
