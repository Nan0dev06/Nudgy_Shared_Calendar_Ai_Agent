# Nudgy — Hackathon → SaaS Master Plan

> Branch: `post-hackathon-submission-edits`. Living document. This is the master
> reference that supersedes the earlier audit (`~/.claude/plans/…`) by adding the
> SaaS point-of-view. Nothing from the hackathon build is treated as fixed — every
> item is re-evaluated with a "better way?" lens.

## Locked decisions (from you)
- **Market:** Both, **consumer-first** (friend groups, outings, personal life now; team plans later — architect for it, don't build it yet).
- **Budget:** **Free until first dollar.** Free tiers only; every paid upgrade goes on a **Wishlist** with the abstraction pre-built so swapping in later is config, not a refactor.
- **Auth:** Add **email/magic-link + Microsoft** alongside Google; **decouple identity from calendar**; support **Google + Outlook** calendars.
- **Billing:** Build the **plan/entitlement skeleton now** (everyone on Free), **defer the payment processor**. Recommendation when you monetize: a **merchant-of-record (Lemon Squeezy or Paddle)** so you never touch global VAT/sales-tax. Placeholder `BillingProvider` interface until then.

## The reframe (what "real SaaS" actually needs that a hackathon skips)
A hackathon proves the idea works once, with everyone in the room. A SaaS has to work for **strangers, asynchronously, unattended, at cost, safely, legally, and durably.** The gaps are less about features and more about:
1. **Multi-tenant safety** — one user must not be able to exhaust a shared resource (right now a single user can burn the whole org's daily free-LLM budget for *everyone*).
2. **Durability & security** — SQLite on an ephemeral disk loses data; OAuth refresh tokens are stored as **plaintext** today.
3. **Async reality** — people aren't all in the app at once, so the poll cascade needs **notifications + deadlines**, not 5-second polling of present users.
4. **Provider independence** — Google-only calendar and a single free LLM are single points of failure; both need an **abstraction with fallback**.
5. **The public gate** — going public needs OAuth verification + privacy policy + ToS. Going to *private beta* does **not** (Google Testing mode: 100 users, no verification). So we ship a beta free, and build the gate in parallel.

---

# PART 1 — CORE FUNCTIONALITY (Priority 1)

## 1.1 Identity & Auth
**Now:** Google OAuth is *both* login and calendar; `User` = an email row; session cookie signed with `SECRET_KEY`, **no expiry**; tokens plaintext in DB.
**Better way:** Split **Identity** from **Calendar connections**. Model: one `User` (identity) → many `CalendarAccount` rows (Google, Microsoft, each with its own encrypted token). Login via **email magic-link** (free, provider-independent) plus **"Continue with Google/Microsoft"** (the same OAuth consent that also grants calendar, so a social sign-in connects a calendar in one step). Add session **TTL + refresh + logout-all**.
**Build vs buy:** rolling magic-link is cheap and lock-in-free, but consider **Supabase Auth** or **Clerk** (both have free tiers) to skip building password resets, email verification, and social-login plumbing. Trade-off: speed & security vs a dependency. Decision needed (see Open Decisions).
**Security fixes (do regardless):** encrypt OAuth refresh tokens at rest (Fernet/libsodium, key from env/secret store); switch session signer to a timed one; never log tokens.

## 1.2 Calendar integration (the core data source)
**Now:** Google `freebusy` (privacy-preserving, ranges only) + events + reads all owned calendars. Google only.
**Better way:** a **`CalendarProvider` interface** — `get_busy(window)`, `get_event_locations(window)`, `create_event(...)`, `delete_event(...)`, `update_event(...)` — with **Google** and **Microsoft Graph** implementations (Graph `getSchedule` = freebusy equivalent). This one refactor is what makes "both markets" real: Outlook is table-stakes for teams and common for consumers. Keep the privacy principle (ranges only, locations only) across every provider. Add a short-TTL **freebusy cache** (perf + stays inside free API quotas). Push/webhook sync (Google `watch`, Graph subscriptions) is a later optimization; on-demand fetch is fine for beta.

## 1.3 Groups / tenancy
**Now:** `Group` + 6-char invite code, flat, no roles. You can join but there's **no leave, no remove-member, no rename, no regenerate-code, no delete-group** — basic gaps.
**Better way:** keep it simple for consumers but add an `owner|member` role now (zero cost, unblocks team admin/billing/seats later without a migration). Add the missing lifecycle ops. Consider a **solo/personal space** (plan your own life + tasks + agent with no group) — broadens consumer appeal and is a natural free-tier funnel.

## 1.4 Poll / cascade mechanism
**Now:** two-stage, host-decides, pure + unit-tested (genuinely good). But host moves route through the **LLM**, updates come from **5s polling**, and there are **no reminders/deadlines**.
**Better way:** keep the cascade logic; add (a) **deterministic host-action endpoints** (`/plans/{id}/lock-in`, `/next-time`) so a booking never depends on a stochastic model; (b) **real-time via SSE/WebSocket** instead of polling (instant, scales, cheaper); (c) **vote reminders + auto-expiry** so async groups actually converge; (d) **shareable vote links** so people can vote without an account (growth loop + removes friction).

## 1.5 Tasks & Events
**Now:** `GroupEvent` (event|task), RSVP, optional one-way Google sync, personal/anonymous masking. Decent, but there is **no edit** (only create/delete/toggle-done), no recurrence, no reminders, and sync is one-way (create+delete, no update).
**Better way:** add full CRUD (edit), recurrence, reminders; make Google/Outlook sync two-way and resilient; unify "a thing on a calendar" so events, tasks, and booked plans share one render/model path.

## 1.6 The agentic AI (the crown jewel)
**Now:** 8-step loop, 7 tools, free Groq `8b-instant`, prompt re-sent each step, no persistence, host moves via chat, injection-exposed via location strings, **no per-user quota** (shared free budget).
**Better way — the big one:**
- **Model router with fallback:** keep free now (Groq), but make the provider swappable with a fallback ladder (Groq → Cerebras free → …). Wishlist: a paid frontier model (Claude/GPT) — a large tool-calling-quality jump when revenue allows.
- **Per-user token quota:** free tier = N agent turns/day per user, enforced server-side. **Critical multi-tenant fix** — protects the shared free budget and becomes a natural paid upsell.
- **Conversation persistence:** store transcripts per user/group (history survives refresh, and real transcripts let us improve the agent).
- **Guardrails:** fence injected location/review text in clearly-delimited data blocks; keep the scope guard; validate tool outputs.
- **Determinism split:** LLM for the conversational/creative parts; deterministic endpoints for booking/host moves.
- **UX:** stream tokens/steps via SSE instead of the current fake `setTimeout` choreography.
- **Tool expansion:** `get_drafts`, reschedule/cancel, add-to-personal-calendar, smarter venue (avoid low-rated), set reminders.

---

# PART 2 — LEGAL / PRIVACY / COMPLIANCE (Priority 1, but only *gates going public*)
- **Private beta runs free & ungated:** Google "Testing" publishing status = up to 100 users, no verification, no privacy policy required; Microsoft can stay single-tenant/test. Perfect for "until someone says they'd pay."
- **To go public** you need: **Privacy Policy + Terms of Service** (write from vetted templates — free), **Google OAuth verification** (calendar scopes are *sensitive*, so verification needs a homepage, verified **domain**, branding, and a demo video — but **not** the paid CASA security assessment that *restricted* scopes require), and **Microsoft publisher verification**.
- **Data rights (GDPR/CCPA baseline):** account **export** + **delete**, consent, a Data-Processing note. The session cookie is functional-only, so the cookie-banner burden is minimal.
- **The one near-unavoidable ~$10/yr cost:** a **domain** (needed for OAuth verification, branded email, and a real product). Everything else stays free.
- **Trust surface:** a simple `/security` + `/privacy` + `/terms` set also lifts conversion.

---

# PART 3 — BILLING & PLANS (skeleton now, processor later)
- **Now (free):** design a **Plan + Entitlement** model — `Plan` (Free, Pro, Team-later), feature gates (max groups, agent turns/day, connected calendars, advanced-venue, priority model), an `entitlements`/`subscription` table, and a central `require_entitlement()` / quota checker. Everyone is on Free; features are built **gate-aware from day one**.
- **Defer:** the payment integration behind a thin `BillingProvider` interface + placeholder. When the first "I'd pay" arrives, wire **Lemon Squeezy/Paddle** in ~a day.
- **Why now:** flipping to paid later becomes *config*, not a rewrite; and quotas double as abuse/cost control immediately.
- **Wishlist:** usage add-ons (extra agent turns), team seats, annual plans.

---

# PART 4 — OPTIMIZATION OF WORKFLOWS (carried from the audit, still valid)
- Fix **N+1** vote reads; compute each plan's tally/active-round **once** (not twice) in `GET /plans`.
- **Windowed** events fetch (currently returns every personal event, all-time).
- **FK indexes** (none today beyond unique/email/invite-code).
- **Freebusy cache** (fewer Google/Graph calls, faster group loads).
- ~~Replace **5s polling** with **SSE**~~ (DONE — `realtime/` + `GET /groups/{id}/stream`; the poll stays as a slow backstop). ETag/304 still open as an extra.
- ~~**Managed Postgres**~~ (DONE — engine is Neon-shaped: TLS forced, pooling for
  idle disconnects, `scripts/check_db.py` to verify a connection string, and the
  schema is now checked against the Postgres dialect in CI. Remaining: you create
  the Neon project and paste the URL.)
- ~~**Error tracking + uptime**~~ (DONE — Sentry behind `SENTRY_DSN` with a
  credential scrubber; `/healthz` for the pinger, `/readyz` for the DB check.)
- Delete dead `polls`/`votes` tables + stray `orbi.db`; reset & reseed local DB.

---

# PART 5 — DESIGN, LOOKS, STYLING, PAGES
- **Public site vs app:** today the app *is* the root. A SaaS needs a **public marketing layer** (Landing, Pricing, Privacy, Terms, About/Help) in front of the logged-in app. That means **introducing a router** (react-router, or migrating to a meta-framework) — the current no-router SPA can't host many public pages cleanly. Real architecture fork.
- **New pages:** Landing, Pricing, Onboarding/first-run, Account & Billing settings, Privacy, Terms, Security, Help/Docs, 404/empty/error states.
- **Theming:** **dark mode** (colors are hardcoded inline today) and **mobile responsiveness** (fixed widths, desktop-only) — both are launch-blockers for a consumer app people will open on phones.
- **Accessibility:** clickable `<div>`s → real `<button>`s, focus states, aria, restore scrollbars.
- **Design system:** extract the glass theme into tokens + reusable components so styling scales past one big inline-styled app.

---

# PART 6 — EXTRA FEATURES (wishlist, prioritized)
1. **Notifications** (email now-ish/free tier, push later) — semi-core: the cascade needs it to work async.
2. **Shareable plan/vote links** — vote without an account; viral growth loop.
3. **Recurring plans** ("same crew, next week").
4. **Solo/personal mode** — plan your own life + tasks with the agent, no group.
5. **Smarter venues** — avoid low-rated, cuisine/budget/fairness of travel distance.
6. ~~**Auto timezone detect**~~ (DONE — the browser reports its zone on every
   boot; typing one in Settings pins it and detection stops touching it).
7. **Chat integrations** — Slack/Discord where consumer groups already live.
8. **Host analytics** — "your crew usually says yes to Thursday 7pm."

---

# PART 7 — SEQUENCED ROADMAP
- **Phase 0 — Foundation (free, invisible):** entitlement/quota skeleton; encrypt tokens + session TTL; managed Postgres + backups; error tracking; per-user LLM quota; DB cleanup. Start OAuth-verification paperwork (domain, draft privacy/ToS) *in parallel* — no code blocked on it.
- **Phase 1 — Auth & calendars:** identity/calendar split; email magic-link + Google + Microsoft; `CalendarProvider` + Outlook; freebusy cache.
- **Phase 2 — Agent & mechanism hardening:** model router + fallback; conversation persistence; ~~deterministic host endpoints~~ (done, Phase 0); injection defense; ~~SSE real-time~~ (DONE); ~~vote reminders/deadlines~~ + ~~shareable vote links~~ (both DONE — see "Async convergence" below).
- **Phase 3 — Go-public prep:** privacy/ToS live; submit Google + Microsoft verification; marketing + pricing + account/billing pages; landing site + router.
- **Phase 4 — Polish & growth:** dark mode, responsive, a11y; wishlist features (notifications, recurring, solo mode, smarter venues, integrations).
- **Payments:** slot the deferred `BillingProvider` in the moment real demand appears.

---

# PART 8 — SUMMARY (how it works · review · how to fix), per item

### Core functionality
| Thing | How it works | Review | How to fix / better way |
|---|---|---|---|
| Auth/Identity | Google OAuth is both login + calendar; forever-valid signed cookie | Excludes non-Google users; no session expiry; can't add a 2nd calendar | Split identity from calendars; email magic-link + Google + Microsoft; session TTL; consider Clerk/Supabase |
| Token storage | Google refresh tokens saved as plaintext JSON in DB | Real security risk for a product holding calendar access | Encrypt at rest (Fernet), key from secret store |
| Calendar data | Google `freebusy` (ranges only) + event locations | Excellent privacy design, but Google-only and uncached | `CalendarProvider` abstraction + Microsoft Graph; short-TTL cache |
| Groups | Invite-code join, flat, no roles | Missing leave/kick/rename/delete; no roles for teams-later | Add lifecycle ops + owner/member role now; optional solo mode |
| Poll cascade | Two-stage, host-decides, pure + tested; **+ deadlines, reminders, opt-in auto-book, guest voting by link** | Host moves and live updates both fixed (PR#18 endpoints, SSE feed) | Next: modes (quick / pick-a-time / float-idea), member-proposed times |
| Tasks/Events | Create/delete/toggle; optional one-way Google sync | No edit, no recurrence, no reminders; one-way sync | Full CRUD + recurrence + reminders; two-way resilient sync |
| Agent AI | 8-step loop, 7 tools, free Groq 8b, prompt resent each step | Fragile model; no persistence; no quota (shared budget); injection-exposed | Model router+fallback; per-user quota; persist transcripts; fence injected text; stream via SSE |

### SaaS scaffolding
| Thing | How it works | Review | How to fix / better way |
|---|---|---|---|
| Legal/verification | None today | Blocks *public* launch (not private beta) | Privacy/ToS from templates; Google + MS verification when going public; stay in Testing mode for beta |
| Billing | None | Needed to earn, but premature to integrate | Build entitlement/quota skeleton now; defer processor behind `BillingProvider`; recommend Lemon Squeezy/Paddle |
| Data durability | SQLite for dev; Postgres (Neon) in prod, TLS forced, schema portability tested | Render's own free Postgres self-deletes after 30 days — hence Neon | Create the Neon project + paste `DATABASE_URL` |
| Multi-tenant cost | One shared free LLM key for all users | One user can exhaust everyone's daily budget | Per-user token quota (also a paid upsell) |

### Optimization
| Thing | How it works | Review | How to fix / better way |
|---|---|---|---|
| Plan reads | `GET /plans` computes each tally once; refetched on an SSE poke (slow backstop poll behind it) | Hot path is now event-driven | ETag/304 to make the backstop poll free |
| Events fetch | Returns every personal event, all time | Unbounded payload as data grows | Window by visible range ± buffer |
| Indexes | Only unique/email/invite-code | Slow FK filters on Postgres at scale | Add FK indexes |
| Freebusy | Live Google call per member every group open | Slow, burns quota | Short-TTL per-user cache |

### Design / pages
| Thing | How it works | Review | How to fix / better way |
|---|---|---|---|
| App shell | App is the root; no router | Can't host public marketing/legal pages | Add router; public site in front of the app |
| Theme | Hardcoded light, fixed widths | No dark mode, not mobile-usable — consumer launch blockers | Token-based theming + dark mode + responsive breakpoints |
| A11y | Clickable divs, hidden scrollbars | Keyboard/screen-reader unusable | Real buttons, focus/aria, visible scroll |

### Extra features (wishlist)
| Thing | How it works | Review | How to fix / better way |
|---|---|---|---|
| Notifications | Vote reminders + deadline/booking mail via the mailer seam (console backend in dev) | Wording done, delivery isn't — needs a real provider; no in-app or push channel, no per-user preferences | Wire Resend/SMTP; add Settings > Notifications, gating it in `notify/` |
| Share links | Per-plan bearer token; guests give a name (+optional email for the invite), vote in the same cascade, counted in the tally | Host can regenerate/revoke; capped per plan; no rate limit on join attempts | Add a join rate limit if links ever leak in the wild |
| Timezones | Per-user IANA zone, auto-detected from the browser on each boot (`timezone_auto`); a zone typed in Settings pins it | Mixed-tz groups render correctly for each viewer | Done. Next: show other members' local time on a proposed slot |
| Recurring / solo / smarter venues / chat integrations | N/A | High-value differentiators | Build post-core from the wishlist |

---

# Working defaults (accepted 2026-07-25 — revisit during Q&A)
1. **Auth build-vs-buy →** roll our own **magic-link** (no lock-in, free), keep Google/Microsoft OAuth for social sign-in + calendar. Reconsider Clerk/Supabase only if it slows us down.
2. **Stack for many pages →** add **react-router** to the current Vite SPA (small step); no Next/Remix migration now.
3. **DB host →** move to **Neon** free tier (managed Postgres + branching + backups).
4. **Domain →** get a **~$10/yr domain** only when ready for verification/public; no spend during local dev / private beta.
5. **Solo/personal mode →** **deferred to Phase 4**; v1 stays focused on the group flow.

> These are working defaults so the build isn't blocked; every one is open to change
> during the feature-by-feature verification + Q&A pass (see below / the chat thread).
