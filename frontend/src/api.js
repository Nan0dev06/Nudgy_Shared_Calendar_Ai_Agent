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
  // host-only: when voting closes (deadline_iso, null clears it) and whether a
  // plan everyone said yes to may book itself (auto_book). Omit a field to
  // leave it alone — sending deadline_iso: null actively clears the deadline.
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
  guestTimeVote: (token, yes, round_id) =>
    req(`/share/${token}/time-vote`, { method: "POST", body: { yes, round_id } }),
  voteInterest: (planId, yes) =>
    req(`/plans/${planId}/interest`, { method: "POST", body: { yes } }),
  voteTime: (planId, yes, round_id) =>
    req(`/plans/${planId}/time-vote`, { method: "POST", body: { yes, round_id } }),
  addRounds: (planId, slots) =>
    req(`/plans/${planId}/rounds`, { method: "POST", body: { slots } }),
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
  patchCalendar: (id, body) =>
    req(`/auth/me/calendars/${id}`, { method: "PATCH", body }),
  disconnectCalendar: (id) =>
    req(`/auth/me/calendars/${id}`, { method: "DELETE" }),
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
