// Thin wrapper over the Orbi REST API (docs/api.md). Session cookie based —
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
  groups: () => req("/groups"),
  createGroup: (name) => req("/groups", { method: "POST", body: { name } }),
  joinGroup: (invite_code) =>
    req("/groups/join", { method: "POST", body: { invite_code } }),
  members: (groupId) => req(`/groups/${groupId}/members`),
  availability: (groupId, days = 14) =>
    req(`/groups/${groupId}/availability?days_ahead=${days}`),
  plans: (groupId) => req(`/groups/${groupId}/plans`),
  createPlan: (groupId, body) =>
    req(`/groups/${groupId}/plans`, { method: "POST", body }),
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
  events: (groupId) => req(`/groups/${groupId}/events`),
  createEvent: (groupId, body) =>
    req(`/groups/${groupId}/events`, { method: "POST", body }),
  patchEvent: (eventId, body) =>
    req(`/events/${eventId}`, { method: "PATCH", body }),
  deleteEvent: (eventId) => req(`/events/${eventId}`, { method: "DELETE" }),
  chat: (group_id, message, history) =>
    req("/chat", { method: "POST", body: { group_id, message, history } }),
};

export const loginUrl = "/auth/google/login";
