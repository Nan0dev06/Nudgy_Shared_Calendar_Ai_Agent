// Members come from the backend as emails only. Derive display names,
// initials, and a stable per-member accent color (person accents are per
// member, never a theme — see design system).

const ACCENTS = [
  "#2A9D8F", // sage
  "#E68E36", // orange
  "#CBA39C", // dusty rose
  "#2B5B84", // slate blue
  "#D95D39", // terracotta
  "#DCA744", // mustard
  "#BCA9C9", // lilac
];

export function nameFromEmail(email) {
  const raw = (email || "").split("@")[0];
  // first alphabetic run reads best: "hussein2008yassine" -> "Hussein"
  const first = (raw.match(/[a-zA-Z]+/) || [raw.split(/[._\-+]/)[0] || raw])[0];
  return first.charAt(0).toUpperCase() + first.slice(1);
}

export function initials(email) {
  const parts = (email || "?").split("@")[0].split(/[._\-+]/).filter(Boolean);
  const a = (parts[0] || "?").charAt(0);
  const b = parts[1] ? parts[1].charAt(0) : "";
  return (a + b).toUpperCase();
}

function hash(s) {
  let h = 0;
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) >>> 0;
  return h;
}

// Assign colors by position in the member list so a group never repeats a
// color until the palette runs out; fall back to hashing for extras.
export function colorForMember(email, index) {
  if (index != null && index < ACCENTS.length) return ACCENTS[index];
  return ACCENTS[hash(email) % ACCENTS.length];
}

export function decorateMembers(rawMembers, myEmail) {
  return (rawMembers || []).map((m, i) => ({
    email: m.email,
    connected: m.calendar_connected,
    name: nameFromEmail(m.email),
    initials: initials(m.email),
    color: colorForMember(m.email, i),
    isMe: m.email === myEmail,
  }));
}
