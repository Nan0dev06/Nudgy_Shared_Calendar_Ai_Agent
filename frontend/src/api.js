// Thin wrapper over the Nudgy REST API (docs/api.md). Session cookie based —
// always send credentials.

async function req(path, opts = {}) {
  const res = await fetch(path, {
    credentials: "include",
    headers: opts.body ? { "Content-Type": "application/json" } : undefined,
    ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = (await res.json()).detail || detail;
    } catch {
      /* non-JSON error body */
    }
    const err = new Error(detail);
    err.status = res.status;
    throw err;
  }
  return res.json();
}

export const api = {
  me: () => req("/auth/me"),
  patchMe: (body) => req("/auth/me", { method: "PATCH", body }),
  // "my browser is in this zone" — sent on every boot; the server ignores it
  // for anyone who picked a timezone by hand (see auth_routes.detected_timezone)
  detectedTimezone: (timezone) =>
    req("/auth/me/detected-timezone", { method: "POST", body: { timezone } }),
  logout: () => req("/auth/logout", { method: "POST" }),
  // email / password identity (decoupled from calendars). register / magicLink /
  // requestReset intentionally return a generic message whether or not the email
  // exists — the UI shows it verbatim and never reveals which case it hit.
  login: (email, password) =>
    req("/auth/login", { method: "POST", body: { email, password } }),
  register: (email, password) =>
    req("/auth/register", { method: "POST", body: { email, password } }),
  magicLink: (email) =>
    req("/auth/magic-link", { method: "POST", body: { email } }),
  requestReset: (email) =>
    req("/auth/password/reset-request", { method: "POST", body: { email } }),
  resetPassword: (token, password) =>
    req("/auth/password/reset", { method: "POST", body: { token, password } }),
  groups: () => req("/groups"),
  createGroup: (name) => req("/groups", { method: "POST", body: { name } }),
  joinGroup: (invite_code) =>
    req("/groups/join", { method: "POST", body: { invite_code } }),
  members: (groupId) => req(`/groups/${groupId}/members`),
  // group lifecycle (owner-gated on the server): rename, roll invite code,
  // delete; leave is self-service; removeMember is the owner kicking someone.
  renameGroup: (groupId, name) =>
    req(`/groups/${groupId}`, { method: "PATCH", body: { name } }),
  regenerateCode: (groupId) =>
    req(`/groups/${groupId}/regenerate-code`, { method: "POST" }),
  deleteGroup: (groupId) => req(`/groups/${groupId}`, { method: "DELETE" }),
  leaveGroup: (groupId) => req(`/groups/${groupId}/leave`, { method: "POST" }),
  removeMember: (groupId, memberId) =>
    req(`/groups/${groupId}/members/${memberId}`, { method: "DELETE" }),
  availability: (groupId, days = 14) =>
    req(`/groups/${groupId}/availability?days_ahead=${days}`),
  plans: (groupId) => req(`/groups/${groupId}/plans`),
  deletePlan: (planId) => req(`/plans/${planId}`, { method: "DELETE" }),
  createPlan: (groupId, body) =>
    req(`/groups/${groupId}/plans`, { method: "POST", body }),
  // host-only: when voting closes (deadline_iso, null clears it) and the
  // `minimum` — how many people must be able to make a time before it books
  // without anyone. Omit a field to leave it alone; sending deadline_iso: null
  // actively clears the deadline. There is no auto_book flag any more: a poll
  // converging on its minimum IS the automatic path.
  patchPlan: (planId, body) => req(`/plans/${planId}`, { method: "PATCH", body }),
  // host-only: the public vote link. sharePlan mints one (or, with regenerate,
  // replaces it — which kills every copy already sent); unsharePlan turns it off.
  sharePlan: (planId, regenerate = false) =>
    req(`/plans/${planId}/share?regenerate=${regenerate}`, { method: "POST" }),
  unsharePlan: (planId) => req(`/plans/${planId}/share`, { method: "DELETE" }),
  // ---- the guest side of that link: no session, no account. Identity is a
  // signed per-plan cookie the join call sets, so these still send credentials.
  sharedPlan: (token) => req(`/share/${token}`),
  joinSharedPlan: (token, name, email) =>
    req(`/share/${token}/join`, { method: "POST", body: { name, email } }),
  guestInterest: (token, yes) =>
    req(`/share/${token}/interest`, { method: "POST", body: { yes } }),
  // `answer` is three-state: "yes" | "no" | "if_needed". Guests answer exactly
  // the question members do, on exactly the same candidate times.
  guestTimeVote: (token, round_id, answer) =>
    req(`/share/${token}/time-vote`, { method: "POST", body: { round_id, answer } }),
  // Interest is asked ONLY by Float-an-idea polls (the ones created with no
  // times). Everywhere else a yes on a time is the interest signal, and the
  // endpoint 400s to say so.
  voteInterest: (planId, yes) =>
    req(`/plans/${planId}/interest`, { method: "POST", body: { yes } }),
  // Answer ONE candidate time. Every time is answerable independently and at
  // any moment — there is no active round to race against.
  voteTime: (planId, round_id, answer) =>
    req(`/plans/${planId}/time-vote`, { method: "POST", body: { round_id, answer } }),
  // Host moves, straight at the deterministic endpoints. These used to go
  // through the agent as an English sentence ("Lock in the active time for the
  // plan X"), which meant a real calendar booking depended on the model picking
  // the right tool AND matching the plan by its TITLE. Same moves, same
  // server-side guards, no model in the path.
  //
  // lockInPlan names the time explicitly rather than inheriting the spotlight:
  // "what we're leaning toward" and "what we're committing to" are different
  // statements, and coupling them would make locking in a different time a
  // two-step dance.
  lockInPlan: (planId, round_id) =>
    req(`/plans/${planId}/lock-in`, { method: "POST", body: { round_id } }),
  // Host: lean toward one time. Resets no votes and can be moved back or
  // cleared (round_id: null). Replaces the old /next-time, which skipped a
  // candidate and made every vote already cast on it irrelevant.
  spotlightTime: (planId, round_id) =>
    req(`/plans/${planId}/spotlight`, { method: "POST", body: { round_id } }),
  // ANY member may put a candidate time up; only its suggester can take it back.
  addRounds: (planId, slots) =>
    req(`/plans/${planId}/rounds`, { method: "POST", body: { slots } }),
  removeRound: (planId, roundId) =>
    req(`/plans/${planId}/rounds/${roundId}`, { method: "DELETE" }),
  myReviews: () => req("/reviews"),
  upsertReview: (body) => req("/reviews", { method: "POST", body }),
  deleteReview: (reviewId) => req(`/reviews/${reviewId}`, { method: "DELETE" }),
  groupReviews: (groupId) => req(`/groups/${groupId}/reviews`),
  getDrafts: () => req("/auth/me/drafts"),
  putDrafts: (drafts) => req("/auth/me/drafts", { method: "PUT", body: { drafts } }),
  getMemory: () => req("/auth/me/memory"),
  putMemory: (memory) => req("/auth/me/memory", { method: "PUT", body: { memory } }),
  // connected calendars (identity/calendar split): list + manage color / sync
  // mode / which is primary / disconnect. Adding one goes via the OAuth connect
  // URLs below, not here.
  calendars: () => req("/auth/me/calendars"),
  // `read_titles` is inbound sync's per-calendar opt-in. Turning it OFF must be
  // paired with `keep_titles`, the user's answer to "keep the titles already
  // pulled in?" — the server defaults it to false (purge), so a caller that
  // forgets to ask errs towards deleting text somebody withdrew consent for.
  patchCalendar: (id, body) =>
    req(`/auth/me/calendars/${id}`, { method: "PATCH", body }),
  // Poll this calendar now instead of waiting for the 5-minute tick. Same job
  // the background ticker runs, narrowed to one account.
  syncCalendar: (id) =>
    req(`/auth/me/calendars/${id}/sync`, { method: "POST" }),
  // keepEvents: the user's answer to "keep what was already synced from this
  // calendar?". Kept events survive as a frozen copy that no longer syncs.
  disconnectCalendar: (id, keepEvents = false) =>
    req(`/auth/me/calendars/${id}?keep_events=${keepEvents}`, { method: "DELETE" }),
  events: (groupId) => req(`/groups/${groupId}/events`),
  createEvent: (groupId, body) =>
    req(`/groups/${groupId}/events`, { method: "POST", body }),
  patchEvent: (eventId, body) =>
    req(`/events/${eventId}`, { method: "PATCH", body }),
  rsvpEvent: (eventId, status) =>
    req(`/events/${eventId}/rsvp`, { method: "POST", body: { status } }),
  deleteEvent: (eventId) => req(`/events/${eventId}`, { method: "DELETE" }),
  chat: (group_id, message, history) =>
    req("/chat", { method: "POST", body: { group_id, message, history } }),
};

// Social sign-in: the same OAuth consent that logs the user in also connects
// their calendar in one step. Both are full-page redirects (backend sets the
// session cookie on the callback and redirects back to "/").
export const loginUrl = "/auth/google/login";
export const msLoginUrl = "/auth/microsoft/login";

// Connect an ADDITIONAL calendar to the already-logged-in account (vs. the login
// URLs above, which sign you in). The backend attaches it to the current session
// user and redirects to /?tab=calendars&connected=<provider>.
export const googleConnectUrl = "/auth/google/connect";
export const msConnectUrl = "/auth/microsoft/connect";
