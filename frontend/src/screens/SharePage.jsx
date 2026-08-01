import { useCallback, useEffect, useState } from "react";
import { heavy, gpill, dpill, fieldStyle, fieldLabel } from "../theme.js";
import { OrbLogo } from "../components/OrbLogo.jsx";
import { api } from "../api.js";

// Voting without an account. Reached from a link the host shares — it lands on
// "/?share=<token>", which App.jsx renders INSTEAD of the sign-in gate (the SPA
// has no router; same trick the password-reset link uses).
//
// The whole point is that nothing here asks you to sign up. Give a name, answer
// the same questions members answer, done. The one account-shaped thing — an
// email — is optional and buys exactly one thing: the calendar invite if the
// plan gets booked. Say so, so it doesn't read as a signup field in disguise.
//
// Rewritten for the 2026-08-01 engine: a guest sees the SAME grid a member does
// — every candidate time answerable at once, three states each — because a
// guest's vote is a real vote counted in the same tally. There is no "active
// time" to single out any more.

const ANSWERS = [
  ["yes", "Yes", "#2A9D8F"],
  ["if_needed", "If needed", "#DCA744"],
  ["no", "Can't", "#D95D39"],
];

function readShareToken() {
  return new URLSearchParams(window.location.search).get("share");
}

// Times come back with a host-timezone label AND the raw instant. A guest has
// no stored timezone, so render the instant in whatever the browser says.
const localTime = (iso) =>
  new Date(iso).toLocaleString(undefined, {
    weekday: "short", day: "numeric", month: "short",
    hour: "2-digit", minute: "2-digit",
  });

const closesLabel = (iso) => {
  if (!iso) return null;
  const ms = new Date(iso).getTime() - Date.now();
  if (ms <= 0) return "Voting has closed";
  const hrs = Math.round(ms / 3600000);
  if (hrs < 1) return `Closes in ${Math.max(1, Math.round(ms / 60000))} min`;
  if (hrs < 48) return `Closes in ${hrs} ${hrs === 1 ? "hour" : "hours"}`;
  return `Closes in ${Math.round(hrs / 24)} days`;
};

export default function SharePage() {
  const token = readShareToken();
  const [view, setView] = useState(null);
  const [err, setErr] = useState("");
  const [gone, setGone] = useState(false);
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [busy, setBusy] = useState(false);
  // set when the join call comes back 409 "you're in this group already" — the
  // backend is the authority on that, so we don't try to guess it up front
  const [inGroup, setInGroup] = useState(false);

  const load = useCallback(async () => {
    try {
      setView(await api.sharedPlan(token));
    } catch (e) {
      // a revoked or made-up link is indistinguishable on purpose
      setGone(true);
      setErr(e.message || "This link isn't active any more.");
    }
  }, [token]);

  useEffect(() => { load(); }, [load]);

  const act = async (fn) => {
    if (busy) return;
    setBusy(true);
    setErr("");
    try {
      setView(await fn());
    } catch (e) {
      setErr(e.message || "That didn't go through — try again.");
      if (e.status === 409 && /in this group/i.test(e.message || "")) setInGroup(true);
      // the host may have moved the question on us; re-read rather than guess
      load();
    } finally {
      setBusy(false);
    }
  };

  const shell = (children) => (
    <div style={{ minHeight: "100dvh", display: "flex", alignItems: "center", justifyContent: "center", padding: 20 }}>
      <div style={{ ...heavy(28), width: "min(460px, 100%)", padding: "28px 26px", display: "flex", flexDirection: "column", gap: 16 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 11 }}>
          <OrbLogo size={34} />
          <span style={{ fontSize: 13, color: "#8c8577" }}>Nudgy</span>
        </div>
        {children}
      </div>
    </div>
  );

  if (gone)
    return shell(
      <>
        <div style={{ fontSize: 19, fontWeight: 600 }}>This link isn't active</div>
        <div style={{ fontSize: 13, color: "#8c8577", lineHeight: 1.6 }}>
          {err} Whoever sent it can share a new one.
        </div>
      </>
    );

  if (!view)
    return shell(<div style={{ fontSize: 13, color: "#8c8577" }}>Loading…</div>);

  const me = view.me;
  const closes = closesLabel(view.deadline_iso);
  const times = view.times || [];

  const choice = (label, sub, color, onClick) => (
    <div
      className="hov-lift-sm"
      onClick={onClick}
      style={{
        flex: 1, borderRadius: 16, padding: "13px 15px", display: "flex",
        flexDirection: "column", gap: 3, cursor: busy ? "default" : "pointer",
        background: "rgba(255,253,247,.5)", border: "1px solid rgba(255,255,255,.6)",
        opacity: busy ? 0.6 : 1, transition: "all .2s",
      }}
    >
      <span style={{ fontSize: 14, fontWeight: 600, color }}>{label}</span>
      {sub && <span style={{ fontSize: 11.5, color: "#a09889" }}>{sub}</span>}
    </div>
  );

  // A guest answers exactly the question a member does, on exactly the same
  // grid — every candidate time at once, three states each. Rendered in the
  // BROWSER's zone: a guest has no stored timezone for us to use.
  const timeRow = (t) => (
    <div
      key={t.round_id}
      style={{
        borderRadius: 14, padding: "11px 13px", display: "flex",
        flexDirection: "column", gap: 8,
        background: t.spotlit ? "rgba(42,157,143,.07)" : "rgba(255,253,247,.5)",
        border: t.spotlit
          ? "1.4px solid rgba(42,157,143,.45)"
          : "1px solid rgba(255,255,255,.6)",
        opacity: busy ? 0.6 : 1, transition: "opacity .15s",
      }}
    >
      <div style={{ display: "flex", alignItems: "baseline", gap: 8, flexWrap: "wrap" }}>
        <span style={{ fontSize: 13.5, fontWeight: 600 }}>{localTime(t.start_iso)}</span>
        {t.spotlit && (
          <span style={{ fontSize: 10.5, fontWeight: 600, letterSpacing: ".06em", textTransform: "uppercase", color: "#2A9D8F" }}>
            ★ they're leaning this way
          </span>
        )}
      </div>
      <div style={{ display: "flex", gap: 7 }}>
        {ANSWERS.map(([value, label, color]) => (
          <div
            key={value}
            className="hov-lift-sm"
            onClick={() => act(() => api.guestTimeVote(token, t.round_id, value))}
            style={{
              flex: 1, textAlign: "center", borderRadius: 11, padding: "7px 9px",
              fontSize: 12, fontWeight: 600, cursor: busy ? "default" : "pointer",
              transition: "all .2s",
              color: t.my_answer === value ? "#fff" : color,
              background: t.my_answer === value ? color : "rgba(255,253,247,.7)",
              border: t.my_answer === value ? `1.4px solid ${color}`
                : `1px solid color-mix(in srgb, ${color} 30%, transparent)`,
            }}
          >
            {label}
          </div>
        ))}
      </div>
    </div>
  );

  return shell(
    <>
      <div>
        <div style={{ fontSize: 21, fontWeight: 600 }}>{view.title}</div>
        <div style={{ fontSize: 13, color: "#8c8577", marginTop: 4, lineHeight: 1.6 }}>
          {view.host_name} is putting this together
          {view.group_name ? ` with ${view.group_name}` : ""}
          {view.location ? ` · ${view.location}` : ""} · {view.day}
        </div>
        <div style={{ display: "flex", gap: 12, marginTop: 8, flexWrap: "wrap", fontSize: 11.5, fontWeight: 600 }}>
          <span style={{ color: "#2A9D8F" }}>
            {view.going_count} of {view.people_count} in so far
          </span>
          {closes && (
            <span style={{ color: view.voting_open ? "#D95D39" : "#a09889" }}>{closes}</span>
          )}
        </div>
      </div>

      {/* A member of this group has a real ballot in the app; the backend
          refuses to give them a second one as a guest (share_routes.join). */}
      {!me && inGroup && (
        <>
          <div style={{ fontSize: 13, color: "#5c564b", lineHeight: 1.6 }}>
            You're already in this group, so you have a vote in the app — no need
            for the guest link.
          </div>
          <a
            href="/"
            className="hov-lift-sm"
            style={{ ...dpill(false), justifyContent: "center", textDecoration: "none" }}
          >
            Open Nudgy to vote
          </a>
        </>
      )}

      {/* ---- not identified yet: one field, then straight to the question --- */}
      {!me && !inGroup && (
        <>
          <div style={{ fontSize: 13, color: "#5c564b", lineHeight: 1.6 }}>
            No account needed — just tell them who's answering.
          </div>
          <label style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            <span style={fieldLabel}>Your name</span>
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              style={fieldStyle}
              placeholder="What should they call you?"
              autoFocus
            />
          </label>
          <label style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            <span style={fieldLabel}>Email — optional</span>
            <input
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              style={fieldStyle}
              placeholder="only used to send you the invite if this happens"
            />
          </label>
          {err && <div style={{ fontSize: 12.5, color: "#D95D39" }}>{err}</div>}
          <div
            className="hov-lift-sm"
            style={{ ...dpill(false), justifyContent: "center", opacity: busy || !name.trim() ? 0.55 : 1 }}
            onClick={() =>
              name.trim() && act(() => api.joinSharedPlan(token, name.trim(), email.trim() || null))
            }
          >
            {busy ? "One sec…" : "Continue"}
          </div>
        </>
      )}

      {/* ---- the same questions members get -------------------------------- */}
      {/* Interest is asked ONLY by a Float-an-idea poll (one started with no
          times). Anywhere else a yes on a time IS the answer, so asking
          separately would be a redundant tap. */}
      {me && view.voting_open && me.stage === "interest" && (
        <>
          <div style={{ fontSize: 14, fontWeight: 600 }}>Are you in, {me.name}?</div>
          <div style={{ display: "flex", gap: 11 }}>
            {choice("I'm in", "you'll be asked about times as they go up",
              "#2A9D8F", () => act(() => api.guestInterest(token, true)))}
            {choice("Not this time", null, "#D95D39",
              () => act(() => api.guestInterest(token, false)))}
          </div>
        </>
      )}

      {me && view.voting_open && me.stage !== "interest" && me.stage !== "out" && times.length > 0 && (
        <>
          <div style={{ fontSize: 14, fontWeight: 600 }}>
            {times.length === 1
              ? "Does this work for you?"
              : `Which of these ${times.length} work for you?`}
          </div>
          <div style={{ fontSize: 11.5, color: "#a09889", lineHeight: 1.5, marginTop: -8 }}>
            Answer each one. “If needed” means you could make it work but would
            rather not — it still counts, and it's how most of these get decided.
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {times.map(timeRow)}
          </div>
        </>
      )}

      {me && me.note && (me.stage === "waiting" || me.stage === "out" || me.stage === "closed") && (
        <div style={{ fontSize: 13, color: "#8c8577", lineHeight: 1.6 }}>{me.note}</div>
      )}

      {err && me && <div style={{ fontSize: 12.5, color: "#D95D39" }}>{err}</div>}

      {/* changing your mind is a normal thing to want, so don't hide it. Only
          a Float poll has an interest answer to flip; elsewhere the time
          buttons above already are the change. */}
      {me && view.voting_open && view.asks_interest && me.stage !== "interest" && (
        <div
          className="hov-row"
          style={{ fontSize: 12, fontWeight: 600, color: "#2B5B84", cursor: "pointer", alignSelf: "flex-start" }}
          onClick={() => act(() => api.guestInterest(token, me.stage === "out"))}
        >
          {me.stage === "out" ? "Actually, I'm in →" : "Actually, I'm out →"}
        </div>
      )}

      {view.status === "booked" && (
        <div style={{ fontSize: 13, color: "#2A9D8F", fontWeight: 600, lineHeight: 1.6 }}>
          It's booked.{" "}
          {me?.name
            ? "If you said you could make it and left an email, the invite is on its way."
            : ""}
        </div>
      )}

      {view.status === "expired" && (
        <div style={{ fontSize: 13, color: "#8c8577", lineHeight: 1.6 }}>
          Voting closed without enough people on one time. Whoever organised it
          can still pick one from the answers that came in — yours was kept.
        </div>
      )}

      <div style={{ fontSize: 11, color: "#a09889", lineHeight: 1.6, borderTop: "1px solid rgba(160,152,137,.16)", paddingTop: 12 }}>
        You're answering as a guest — this link only shows you this one plan.
      </div>
    </>
  );
}

export { readShareToken };
