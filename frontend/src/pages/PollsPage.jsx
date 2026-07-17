import { useApp } from "../ctx.js";
import { glass, gpill, dpill, avatar, agentBox, kicker } from "../theme.js";
import { CheckIcon } from "../Icons.jsx";
import { nameFromEmail } from "../people.js";

// Real polls from the backend: one proposed slot, yes/no votes, decision rule
// (zero NOs + min_yes YESes → approved → auto-booked). You can change your
// vote any time while the poll is open.
export default function PollsPage() {
  const { polls, members, myVotes, vote, setPage, setView, setModal, activeGroup } = useApp();

  const total = members.length || 1;

  const optionRow = (p, yes) => {
    const count = yes ? p.yes : p.no;
    const c = yes ? "#2A9D8F" : "#D95D39";
    const selected = myVotes[p.id] === (yes ? "yes" : "no");
    return (
      <div
        className="hov-lift-sm"
        onClick={() => vote(p.id, yes)}
        style={{
          borderRadius: 16, padding: "13px 15px", display: "flex",
          flexDirection: "column", gap: 8, cursor: "pointer", transition: "all .2s",
          background: selected
            ? `linear-gradient(135deg, color-mix(in srgb, ${c} 16%, rgba(255,251,244,.6)), rgba(250,242,231,.4))`
            : "rgba(255,253,247,.45)",
          border: selected ? `1.6px solid ${c}` : "1px solid rgba(255,255,255,.6)",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <span style={{ fontSize: 14, fontWeight: 600 }}>
            {yes ? "Works for me" : "Can't make it"}
          </span>
          <span style={{ fontSize: 12.5, fontWeight: 600, color: count > 0 ? c : "#a09889" }}>
            {count} {count === 1 ? "vote" : "votes"}
          </span>
        </div>
        <div style={{ height: 7, borderRadius: 4, background: "rgba(150,142,128,.2)" }}>
          <div
            style={{
              height: "100%", borderRadius: 4, background: c,
              width: `${Math.round((count / total) * 100)}%`,
              transition: "width .4s cubic-bezier(.4,0,.2,1)",
            }}
          />
        </div>
      </div>
    );
  };

  return (
    <div style={{ flex: 1, minHeight: 0, overflow: "auto", display: "flex", flexDirection: "column", gap: 18, animation: "fadeUp .35s cubic-bezier(.4,0,.2,1)" }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", width: "min(620px, 100%)" }}>
        <span style={{ fontSize: 13, color: "#8c8577" }}>
          {polls.length
            ? "You can change your vote until a poll locks."
            : "No polls yet — start one and Orbi books the winner."}
        </span>
        <div className="hov-lift-sm" style={dpill(true)} onClick={() => setModal({ type: "newPoll" })}>
          + New poll
        </div>
      </div>

      {polls.map((p) => {
        const locked = p.status === "approved" || p.booked;
        const rejected = p.status === "rejected";
        const voters = p.yes + p.no;
        return (
          <div
            key={p.id}
            style={{
              ...glass(24), width: "min(620px, 100%)", padding: "20px 22px",
              display: "flex", flexDirection: "column", gap: 14, flex: "none",
            }}
          >
            {!locked && !rejected && (
              <>
                <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                  <span style={{ fontSize: 10.5, fontWeight: 600, letterSpacing: ".07em", textTransform: "uppercase", color: "#D95D39" }}>
                    Poll · open
                  </span>
                  <span style={{ fontSize: 12, color: "#a09889" }}>
                    {voters} of {total} voted
                  </span>
                </div>
                <div>
                  <div style={{ fontSize: 18, fontWeight: 600 }}>{p.title}</div>
                  <div style={{ fontSize: 12.5, color: "#8c8577", marginTop: 4 }}>
                    {p.start_local} – {p.end_local}
                    {p.location ? ` · ${p.location}` : ""}
                  </div>
                </div>
                <div style={{ display: "flex", flexDirection: "column", gap: 11 }}>
                  {optionRow(p, true)}
                  {optionRow(p, false)}
                </div>
                <div style={{ fontSize: 12, color: "#a09889" }}>
                  Needs {p.min_yes} yes and zero no to lock automatically.
                  {(p.waiting_on || []).length > 0 &&
                    ` Waiting on ${p.waiting_on.map(nameFromEmail).join(", ")}.`}
                </div>
              </>
            )}

            {locked && (
              <>
                <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                  <div style={{ width: 30, height: 30, borderRadius: "50%", background: "#2A9D8F", color: "#fff", display: "flex", alignItems: "center", justifyContent: "center" }}>
                    <CheckIcon size={15} color="#fff" sw={2.6} />
                  </div>
                  <span style={{ fontSize: 16, fontWeight: 600 }}>Locked in</span>
                </div>
                <div style={{ height: 64, borderRadius: 14, background: "linear-gradient(120deg, #F3C9A8, #A9CBB6, #C9B6D4)" }} />
                <div>
                  <div style={{ fontSize: 17, fontWeight: 600 }}>
                    {p.title}
                    {p.location ? ` · ${p.location}` : ""}
                  </div>
                  <div style={{ fontSize: 12, color: "#a09889", marginTop: 3 }}>
                    {p.start_local} — {p.yes} of {total} said yes, none said no
                  </div>
                </div>
                <div style={agentBox}>
                  <span style={{ fontSize: 12, fontWeight: 600, color: "#2A9D8F" }}>
                    Why this one locked
                  </span>
                  <span style={{ fontSize: 12.5, lineHeight: 1.5, color: "#5c564b" }}>
                    Zero no votes and at least {p.min_yes} yes — the group's rule
                    passed, so Orbi {p.booked ? "booked it on everyone's calendar." : "is booking it now."}
                  </span>
                </div>
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <div style={{ display: "flex" }}>
                    {members.map((m, i) => (
                      <div key={m.email} title={m.name} style={{ ...avatar(m.color, 22), marginRight: i < members.length - 1 ? -6 : 0 }} />
                    ))}
                  </div>
                  <span style={{ fontSize: 12, color: "#a09889" }}>
                    {p.booked ? "Added to everyone's calendar" : "Booking…"}
                  </span>
                </div>
                <div style={{ display: "flex", gap: 9 }}>
                  <div className="hov-glass" style={gpill(true)} onClick={() => { setPage("calendar"); setView("week"); }}>
                    View on calendar
                  </div>
                  {p.event_link && (
                    <a href={p.event_link} target="_blank" rel="noreferrer" style={{ ...gpill(true), textDecoration: "none" }}>
                      Open in Google Calendar
                    </a>
                  )}
                </div>
              </>
            )}

            {rejected && (
              <>
                <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                  <span style={{ fontSize: 10.5, fontWeight: 600, letterSpacing: ".07em", textTransform: "uppercase", color: "#a09889" }}>
                    Poll · declined
                  </span>
                  <span style={{ fontSize: 12, color: "#a09889" }}>{voters} of {total} voted</span>
                </div>
                <div>
                  <div style={{ fontSize: 17, fontWeight: 600, color: "#8c8577" }}>{p.title}</div>
                  <div style={{ fontSize: 12.5, color: "#a09889", marginTop: 3 }}>
                    {p.start_local} — someone couldn't make it, so this slot won't be booked.
                  </div>
                </div>
                <div className="hov-glass" style={{ ...gpill(true), alignSelf: "flex-start" }} onClick={() => setModal({ type: "newPoll" })}>
                  Propose another time
                </div>
              </>
            )}
          </div>
        );
      })}

      {polls.length === 0 && activeGroup && (
        <div style={{ ...glass(24), width: "min(620px, 100%)", padding: "26px 22px", textAlign: "center", color: "#8c8577", fontSize: 13.5 }}>
          That's it for now!
        </div>
      )}
    </div>
  );
}
