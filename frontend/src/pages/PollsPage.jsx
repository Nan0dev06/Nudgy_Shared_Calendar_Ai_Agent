import { useApp } from "../ctx.js";
import { glass, gpill, dpill, sagePill, agentBox } from "../theme.js";
import { CheckIcon, PinIcon, ClockIcon } from "../Icons.jsx";
import { nameFromEmail } from "../people.js";

// Two-stage plan cascade. Stage 1 asks "are you in for this at all?"; a yes
// immediately opens stage 2 — "does the active candidate time work?". Nothing
// auto-books: the HOST reads the tally and (through Nudgy) locks a time in or
// moves to the next one.

// host_box fields are lists of emails — show a count plus the names
const tallyLine = (list, word) => {
  const l = list || [];
  return l.length
    ? `${l.length} ${word} (${l.map(nameFromEmail).join(", ")})`
    : `0 ${word}`;
};
export default function PollsPage() {
  const {
    plans, activeGroup, voteInterest, voteTime, setModal, setPage, setView, doSend,
  } = useApp();

  let expectedMap = {};
  try {
    expectedMap = JSON.parse(localStorage.getItem("ov.expected") || "{}");
  } catch { /* fine */ }

  const choice = (label, sub, color, onClick, selected) => (
    <div
      className="hov-lift-sm"
      onClick={onClick}
      style={{
        flex: 1, borderRadius: 16, padding: "12px 15px", display: "flex",
        flexDirection: "column", gap: 3, cursor: "pointer", transition: "all .2s",
        background: selected
          ? `linear-gradient(135deg, color-mix(in srgb, ${color} 16%, rgba(255,251,244,.6)), rgba(250,242,231,.4))`
          : "rgba(255,253,247,.45)",
        border: selected ? `1.6px solid ${color}` : "1px solid rgba(255,255,255,.6)",
      }}
    >
      <span style={{ fontSize: 14, fontWeight: 600, color: selected ? color : "#2D2D2D" }}>{label}</span>
      {sub && <span style={{ fontSize: 11.5, color: "#a09889" }}>{sub}</span>}
    </div>
  );

  const statusChip = (p) => {
    const map = {
      open: ["#D95D39", "Poll · open"],
      scheduled: ["#2A9D8F", "Locked in"],
      dead: ["#a09889", "Didn't work out"],
    };
    const [c, label] = map[p.status] || ["#a09889", p.status];
    return (
      <span style={{ fontSize: 10.5, fontWeight: 600, letterSpacing: ".07em", textTransform: "uppercase", color: c }}>
        {label}
      </span>
    );
  };

  return (
    <div style={{ flex: 1, minHeight: 0, overflow: "auto", display: "flex", flexDirection: "column", gap: 18, animation: "fadeUp .35s cubic-bezier(.4,0,.2,1)" }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", width: "min(640px, 100%)" }}>
        <span style={{ fontSize: 13, color: "#8c8577" }}>
          {plans.length
            ? "Say if you're in first — then vote on the time."
            : "No polls yet — ask who's in, or propose a full plan."}
        </span>
        <div className="hov-lift-sm" style={dpill(true)} onClick={() => setModal({ type: "newPoll" })}>
          + New poll
        </div>
      </div>

      {plans.map((p) => {
        const b = p.ballot || {};
        const times = p.times || [];
        const bookedRound = times.find((t) => t.booked);
        const hb = p.host_box;
        return (
          <div
            key={p.id}
            id={`plan-${p.id}`}
            style={{
              ...glass(24), width: "min(640px, 100%)", padding: "20px 22px",
              display: "flex", flexDirection: "column", gap: 14, flex: "none",
            }}
          >
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
              {statusChip(p)}
              <span style={{ fontSize: 12, color: "#a09889" }}>
                by {p.is_host ? "you" : nameFromEmail(p.host || "")}
              </span>
            </div>

            <div>
              <div style={{ fontSize: 18, fontWeight: 600 }}>{p.title}</div>
              <div style={{ display: "flex", alignItems: "center", gap: 14, marginTop: 5, fontSize: 12.5, color: "#8c8577", flexWrap: "wrap" }}>
                {p.location && (
                  <span style={{ display: "inline-flex", alignItems: "center", gap: 5 }}>
                    <PinIcon size={13} /> {p.location}
                  </span>
                )}
                <span style={{ display: "inline-flex", alignItems: "center", gap: 5 }}>
                  <ClockIcon size={13} /> {p.day}
                </span>
                {expectedMap[p.id] && (
                  <span style={{ fontSize: 11.5, fontWeight: 600, color: "#2B5B84" }}>
                    aiming for {expectedMap[p.id]} people
                  </span>
                )}
              </div>
            </div>

            {/* ---- my ballot -------------------------------------------- */}
            {p.status === "open" && b.stage === "interest" && (
              <>
                <div style={{ fontSize: 13, fontWeight: 600 }}>Are you in?</div>
                <div style={{ display: "flex", gap: 11 }}>
                  {choice("I'm in", times.length ? "you'll get the time question next" : null, "#2A9D8F", () => voteInterest(p.id, true))}
                  {choice("Not this time", null, "#D95D39", () => voteInterest(p.id, false))}
                </div>
              </>
            )}

            {p.status === "open" && b.stage === "time" && b.round_id && (
              <>
                <div style={{ fontSize: 13, fontWeight: 600 }}>
                  Does <span style={{ color: "#2B5B84" }}>{b.time_label}</span> work for you?
                </div>
                <div style={{ display: "flex", gap: 11 }}>
                  {choice("Works for me", null, "#2A9D8F", () => voteTime(p.id, true, b.round_id))}
                  {choice("Can't at that time", "you stay in the plan — the host may try another time", "#D95D39", () => voteTime(p.id, false, b.round_id))}
                </div>
              </>
            )}

            {b.note && (
              <div style={{ fontSize: 12.5, color: "#8c8577", lineHeight: 1.5 }}>{b.note}</div>
            )}

            {/* ---- candidate times queue -------------------------------- */}
            {times.length > 0 && p.status === "open" && (
              <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                <span style={{ fontSize: 10.5, fontWeight: 600, letterSpacing: ".07em", textTransform: "uppercase", color: "#a49c8c" }}>
                  Candidate times
                </span>
                <div style={{ display: "flex", gap: 7, flexWrap: "wrap" }}>
                  {times.map((t) => (
                    <span
                      key={t.round_id}
                      style={{
                        fontSize: 11.5, fontWeight: 600, padding: "5px 12px", borderRadius: 999,
                        background:
                          t.status === "active" ? "rgba(42,157,143,.14)"
                          : t.status === "skipped" ? "rgba(150,142,128,.14)"
                          : "rgba(255,253,247,.55)",
                        color:
                          t.status === "active" ? "#2A9D8F"
                          : t.status === "skipped" ? "#a09889"
                          : "#5c564b",
                        textDecoration: t.status === "skipped" ? "line-through" : "none",
                        border: "1px solid rgba(255,255,255,.6)",
                      }}
                    >
                      {t.label}
                      {t.status === "active" ? " · now asking" : ""}
                    </span>
                  ))}
                </div>
              </div>
            )}

            {/* ---- host's decision box ---------------------------------- */}
            {hb && p.status === "open" && (
              <div style={agentBox}>
                <span style={{ fontSize: 12, fontWeight: 600, color: "#2A9D8F" }}>
                  Your host box
                </span>
                <span style={{ fontSize: 12.5, lineHeight: 1.5, color: "#5c564b" }}>
                  {tallyLine(hb.interested, "in")} · {tallyLine(hb.not_interested, "out")} ·{" "}
                  {(hb.no_answer || []).length
                    ? `waiting on ${(hb.no_answer || []).map(nameFromEmail).join(", ")}`
                    : "everyone answered"}
                  {times.length > 0 &&
                    ` — active time: ${tallyLine(hb.time_yes, "yes")}, ${tallyLine(hb.time_no, "no")}, ${(hb.time_waiting || []).length} waiting`}
                </span>
                {hb.note && (
                  <span style={{ fontSize: 12, lineHeight: 1.5, color: "#8c8577" }}>{hb.note}</span>
                )}
                <div style={{ display: "flex", gap: 8, marginTop: 4, flexWrap: "wrap" }}>
                  {times.length > 0 && (
                    <>
                      <div
                        className="hov-lift-sm"
                        style={{ ...sagePill(true), padding: "5px 13px", fontSize: 11.5 }}
                        onClick={() => doSend(`Lock in the active time for the plan "${p.title}"`)}
                      >
                        Lock it in
                      </div>
                      <div
                        className="hov-glass"
                        style={{ ...gpill(true), padding: "5px 13px", fontSize: 11.5 }}
                        onClick={() => doSend(`The current time doesn't work — move to the next candidate time for the plan "${p.title}"`)}
                      >
                        Try the next time
                      </div>
                    </>
                  )}
                  {times.length === 0 && (
                    <div
                      className="hov-lift-sm"
                      style={{ ...dpill(true), padding: "5px 13px", fontSize: 11.5 }}
                      onClick={() =>
                        setModal({
                          type: "newPoll",
                          proposeChange: {
                            planId: p.id, title: p.title, location: p.location || "",
                            skipToTimes: true,
                          },
                        })
                      }
                    >
                      Add times now
                    </div>
                  )}
                </div>
              </div>
            )}

            {/* ---- locked / dead states --------------------------------- */}
            {p.status === "scheduled" && bookedRound && (
              <>
                <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                  <div style={{ width: 30, height: 30, borderRadius: "50%", background: "#2A9D8F", color: "#fff", display: "flex", alignItems: "center", justifyContent: "center" }}>
                    <CheckIcon size={15} color="#fff" sw={2.6} />
                  </div>
                  <span style={{ fontSize: 15, fontWeight: 600 }}>{bookedRound.label}</span>
                </div>
                <div style={{ height: 56, borderRadius: 14, background: "linear-gradient(120deg, #F3C9A8, #A9CBB6, #C9B6D4)" }} />
                <div style={{ display: "flex", gap: 9 }}>
                  <div className="hov-glass" style={gpill(true)} onClick={() => { setPage("calendar"); setView("week"); }}>
                    View on calendar
                  </div>
                  {bookedRound.event_link && (
                    <a href={bookedRound.event_link} target="_blank" rel="noreferrer" style={{ ...gpill(true), textDecoration: "none" }}>
                      Open in Google Calendar
                    </a>
                  )}
                </div>
              </>
            )}

            {p.status === "dead" && (
              <>
                <div style={{ fontSize: 12.5, color: "#a09889", lineHeight: 1.5 }}>
                  Every candidate time was tried and none worked. Suggest a change —
                  same plan, different times or place.
                </div>
                <div
                  className="hov-glass"
                  style={{ ...gpill(true), alignSelf: "flex-start" }}
                  onClick={() =>
                    setModal({
                      type: "newPoll",
                      proposeChange: {
                        planId: p.id, title: p.title, location: p.location || "",
                        slots: (p.times || []).map((t) => ({ start_iso: t.start_iso, end_iso: t.end_iso })),
                      },
                    })
                  }
                >
                  Suggest a change
                </div>
              </>
            )}
          </div>
        );
      })}

      {plans.length === 0 && activeGroup && (
        <div style={{ ...glass(24), width: "min(640px, 100%)", padding: "26px 22px", textAlign: "center", color: "#8c8577", fontSize: 13.5 }}>
          That's it for now!
        </div>
      )}
    </div>
  );
}
