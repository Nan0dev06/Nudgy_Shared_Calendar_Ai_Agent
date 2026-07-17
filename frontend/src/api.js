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
  logout: () => req("/auth/logout", { method: "POST" }),
  groups: () => req("/groups"),
  createGroup: (name) => req("/groups", { method: "POST", body: { name } }),
  joinGroup: (invite_code) =>
    req("/groups/join", { method: "POST", body: { invite_code } }),
  members: (groupId) => req(`/groups/${groupId}/members`),
  polls: (groupId) => req(`/groups/${groupId}/polls`),
  vote: (pollId, yes) =>
    req(`/polls/${pollId}/vote`, { method: "POST", body: { yes } }),
  chat: (group_id, message, history) =>
    req("/chat", { method: "POST", body: { group_id, message, history } }),
};

export const loginUrl = "/auth/google/login";
