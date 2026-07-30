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
- **Next up in Phase 2:** conversation persistence, model router + fallback,
  injection defense.

## Before beta testers (as of 2026-07-31)

Code-side, one item left: **the mobile-responsive pass** (own session — there
are zero `@media` queries and at 375px the sidebar squeezes the content column
to ~119px). Then **one full security-review session**, last, after the code
stops moving.

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
