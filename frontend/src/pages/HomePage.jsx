import { useApp } from "../ctx.js";
import { glass, catChip, dpill, kicker } from "../theme.js";
import { OrbLogo } from "../components/OrbLogo.jsx";
import { fmtDayLong, fmtRange, sameDay } from "../dates.js";

const statBase = (r, tint, pct) => ({
  ...glass(r),
  padding: "18px 20px",
  display: "flex",
  flexDirection: "column",
  gap: 5,
  background: `linear-gradient(135deg, color-mix(in srgb, ${tint} ${pct}%, rgba(255,251,244,.6)), rgba(250,242,231,.4))`,
  backdropFilter: "blur(26px)",
  WebkitBackdropFilter: "blur(26px)",
});

const CAT_COLORS = { Event: "#D95D39", Meet: "#2A9D8F", Call: "#DCA744", Task: "#DCA744" };
const LIMIT = 4; // per block — nearest first, keeps the dashboard breathable

export default function HomePage() {
  const {
    activeGroup, events, tasks, openPlans, favoritePlace,
    setPage, setModal, doSend, voteInterest,
  } = useApp();

  const now = new Date();
  const upcoming = events.filter((e) => e.end >= now);
  const openTasks = tasks
    .filter((t) => !t.done)
    .sort((a, b) => (a.dueAt || Infinity) - (b.dueAt || Infinity));
  const monthOutings = events.filter(
    (e) => e.start.getMonth() === now.getMonth() && e.start.getFullYear() === now.getFullYear()
  ).length;
  const nextUp = upcoming[0];
  const isEmpty = upcoming.length === 0 && openTasks.length === 0 && openPlans.length === 0;

  if (!activeGroup || isEmpty)
    return (
      <div style={{ flex: 1, minHeight: 0, display: "flex", flexDirection: "column", gap: 16, animation: "fadeUp .35s cubic-bezier(.4,0,.2,1)" }}>
        <div style={{ flex: 1, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", gap: 16 }}>
          <div
            style={{
              width: 58, height: 58, display: "flex",
              alignItems: "center", justifyContent: "center",
              animation: "ofloat 3.4s ease-in-out infinite",
            }}
          >
            <OrbLogo size={58} />
          </div>
          <div style={{ textAlign: "center", maxWidth: 380 }}>
            <div style={{ fontSize: 19, fontWeight: 600 }}>
              {activeGroup ? "Nothing planned this week" : "You're not in a group yet"}
            </div>
            <div style={{ fontSize: 13.5, color: "#8c8577", marginTop: 6, lineHeight: 1.5 }}>
              {activeGroup
                ? "Ask Nudgy to find a time everyone's free — it checks live calendars."
                : "Create a group or join one from Settings → Groups to start planning."}
            </div>
          </div>
          {activeGroup ? (
            <div className="hov-lift-sm" style={dpill(false)} onClick={() => doSend("Find a time this week when everyone's free")}>
              Find a time
            </div>
          ) : (
            <div className="hov-lift-sm" style={dpill(false)} onClick={() => { setPage("settings"); }}>
              Open settings
            </div>
          )}
        </div>
      </div>
    );

  const listRow = (key, cat, title, meta, color, onClick) => (
    <div
      key={key}
      className="hov-row"
      onClick={onClick}
      style={{ display: "flex", alignItems: "center", gap: 11, padding: "9px 10px", borderRadius: 13, cursor: onClick ? "pointer" : "default" }}
    >
      <div style={catChip(color)}>{cat}</div>
      <span style={{ flex: 1, fontSize: 13.5, fontWeight: 600, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
        {title}
      </span>
      <span style={{ fontSize: 11.5, color: "#a09889", flex: "none" }}>{meta}</span>
    </div>
  );

  const blockHead = (label, count, go) => (
    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "0 10px 6px" }}>
      <span style={kicker}>
        {label}
        {count > 0 ? ` · ${count}` : ""}
      </span>
      <span
        style={{ fontSize: 11.5, fontWeight: 600, color: "#2B5B84", cursor: "pointer" }}
        onClick={go}
      >
        View all →
      </span>
    </div>
  );

  const emptyNote = (text) => (
    <div style={{ fontSize: 12.5, color: "#a09889", padding: "8px 10px" }}>{text}</div>
  );

  const block = (radius, children) => (
    <div
      className="hov-float"
      style={{ ...glass(radius), padding: "14px 12px", display: "flex", flexDirection: "column", gap: 2, minWidth: 0 }}
    >
      {children}
    </div>
  );

  return (
    <div style={{ flex: 1, minHeight: 0, display: "flex", flexDirection: "column", gap: 16, animation: "fadeUp .35s cubic-bezier(.4,0,.2,1)", overflow: "auto" }}>
      {/* stat row — asymmetric on purpose (radii 24/18/28, middle staggered) */}
      <div style={{ display: "grid", gridTemplateColumns: "1.25fr .85fr 1fr", gap: 14 }}>
        <div className="hov-float" style={statBase(24, "#2A9D8F", 20)}>
          <div style={{ ...kicker, color: "#7d8f85" }}>Next up</div>
          <div style={{ fontSize: nextUp ? 20 : 34, fontWeight: 600, lineHeight: 1.2, marginTop: nextUp ? 6 : 0 }}>
            {nextUp ? nextUp.title : "—"}
          </div>
          <div style={{ fontSize: 12, color: "#8c8577" }}>
            {nextUp ? `${fmtDayLong(nextUp.start)} · ${fmtRange(nextUp.start, nextUp.end)}` : "nothing booked yet"}
          </div>
        </div>
        <div className="hov-float" style={{ ...statBase(18, "#CBA39C", 26), marginTop: 12 }}>
          <div style={{ ...kicker, color: "#9d8680" }}>Outings</div>
          <div style={{ fontSize: 34, fontWeight: 600, lineHeight: 1.1 }}>{monthOutings}</div>
          <div style={{ fontSize: 12, color: "#8c8577" }}>this month</div>
        </div>
        <div
          className="hov-float"
          style={{ ...statBase(28, "#DCA744", 24), cursor: "pointer" }}
          onClick={() => { setPage("settings"); }}
          title="Rate places in Settings → Reviews"
        >
          <div style={{ ...kicker, color: "#a08a5f" }}>Favorite place</div>
          <div style={{ fontSize: favoritePlace ? 20 : 34, fontWeight: 600, lineHeight: 1.2, marginTop: favoritePlace ? 6 : 0 }}>
            {favoritePlace ? favoritePlace.name : "—"}
          </div>
          <div style={{ fontSize: 12, color: "#8c8577" }}>
            {favoritePlace ? favoritePlace.note : "rate a place after an outing"}
          </div>
        </div>
      </div>

      {/* one glass block per thing — each holds a short nearest-first list */}
      <div style={{ display: "grid", gridTemplateColumns: "1.1fr 1fr 1fr", gap: 14, flex: 1, minHeight: 0, alignItems: "start" }}>
        {block(24, (
          <>
            {blockHead("Events", upcoming.length, () => setPage("calendar"))}
            {upcoming.slice(0, LIMIT).map((e) =>
              listRow(
                e.id, e.cat || "Event", e.title,
                sameDay(e.start, now) ? `Today · ${fmtRange(e.start, e.end)}` : fmtDayLong(e.start),
                CAT_COLORS[e.cat] || "#D95D39",
                () => setModal({ type: "event", event: e })
              )
            )}
            {upcoming.length === 0 && emptyNote("No events coming up.")}
          </>
        ))}

        {block(20, (
          <>
            {blockHead("Tasks", openTasks.length, () => setPage("calendar"))}
            {openTasks.slice(0, LIMIT).map((t) =>
              listRow(
                "t" + t.id, "Task", t.title,
                t.due === "anytime" ? "anytime" : `due ${t.due.slice(5)}`,
                "#DCA744",
                () => setModal({ type: "task", task: t })
              )
            )}
            {openTasks.length === 0 && emptyNote("No open tasks.")}
          </>
        ))}

        {block(28, (
          <>
            {blockHead("Polls", openPlans.length, () => setPage("polls"))}
            {openPlans.slice(0, LIMIT).map((p) => {
              const needsMe = p.ballot?.stage === "interest" || p.ballot?.stage === "time";
              return (
                <div
                  key={p.id}
                  className="hov-row"
                  onClick={() => setPage("polls")}
                  style={{ display: "flex", flexDirection: "column", gap: 5, padding: "9px 10px", borderRadius: 13, cursor: "pointer" }}
                >
                  <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8 }}>
                    <span style={{ fontSize: 13.5, fontWeight: 600, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                      {p.title}
                    </span>
                    {needsMe && (
                      <span style={{ fontSize: 10.5, color: "#D95D39", fontWeight: 600, flex: "none" }}>
                        your turn
                      </span>
                    )}
                  </div>
                  <div style={{ fontSize: 11.5, color: "#a09889", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {p.location ? `${p.location} · ` : ""}{p.day}
                  </div>
                  {p.ballot?.stage === "interest" && (
                    <div style={{ display: "flex", gap: 6 }}>
                      <span
                        style={{ fontSize: 11.5, fontWeight: 600, color: "#2A9D8F", cursor: "pointer" }}
                        onClick={(ev) => { ev.stopPropagation(); voteInterest(p.id, true); }}
                      >
                        I'm in
                      </span>
                      <span style={{ fontSize: 11.5, color: "#c9c2b4" }}>·</span>
                      <span
                        style={{ fontSize: 11.5, fontWeight: 600, color: "#a09889", cursor: "pointer" }}
                        onClick={(ev) => { ev.stopPropagation(); voteInterest(p.id, false); }}
                      >
                        Not this time
                      </span>
                    </div>
                  )}
                </div>
              );
            })}
            {openPlans.length === 0 && emptyNote("No open polls.")}
          </>
        ))}
      </div>
    </div>
  );
}
