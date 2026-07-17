import { useApp } from "../ctx.js";
import { glass, catChip, avatar, dpill, kicker, orbGradient, sagePill } from "../theme.js";
import { PlusIcon } from "../Icons.jsx";
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

export default function HomePage() {
  const {
    activeGroup, events, tasks, openPolls, members, me,
    setPage, setModal, setChatOpen, doSend, vote,
  } = useApp();

  const now = new Date();
  const upcoming = events.filter((e) => e.end >= now).slice(0, 5);
  const openTasks = tasks.filter((t) => !t.done).slice(0, 5);
  const monthOutings = events.filter(
    (e) => e.start.getMonth() === now.getMonth() && e.start.getFullYear() === now.getFullYear()
  ).length;
  const nextUp = upcoming[0];
  const isEmpty = upcoming.length === 0 && openTasks.length === 0 && openPolls.length === 0;

  if (!activeGroup || isEmpty)
    return (
      <div style={{ flex: 1, minHeight: 0, display: "flex", flexDirection: "column", gap: 16, animation: "fadeUp .35s cubic-bezier(.4,0,.2,1)" }}>
        <div style={{ flex: 1, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", gap: 16 }}>
          <div
            style={{
              width: 58, height: 58, borderRadius: "50%", background: orbGradient(34),
              boxShadow: "0 12px 30px rgba(45,45,45,.22)", display: "flex",
              alignItems: "center", justifyContent: "center", color: "#fff", fontSize: 22,
              animation: "ofloat 3.4s ease-in-out infinite",
            }}
          >
            ✦
          </div>
          <div style={{ textAlign: "center", maxWidth: 380 }}>
            <div style={{ fontSize: 19, fontWeight: 600 }}>
              {activeGroup ? "Nothing planned this week" : "You're not in a group yet"}
            </div>
            <div style={{ fontSize: 13.5, color: "#8c8577", marginTop: 6, lineHeight: 1.5 }}>
              {activeGroup
                ? "Ask the orb to find a time everyone's free — it checks live calendars."
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
      style={{ display: "flex", alignItems: "center", gap: 11, padding: "10px 10px", borderRadius: 13, cursor: onClick ? "pointer" : "default" }}
    >
      <div style={catChip(color)}>{cat}</div>
      <span style={{ flex: 1, fontSize: 13.5, fontWeight: 600, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
        {title}
      </span>
      <span style={{ fontSize: 11.5, color: "#a09889", flex: "none" }}>{meta}</span>
    </div>
  );

  const endCap = (
    <div style={{ textAlign: "center", fontSize: 12.5, color: "#a09889", padding: "14px 0 6px" }}>
      That's it for now!
    </div>
  );

  return (
    <div style={{ flex: 1, minHeight: 0, display: "flex", flexDirection: "column", gap: 16, animation: "fadeUp .35s cubic-bezier(.4,0,.2,1)", overflow: "auto" }}>
      {/* stat row — asymmetric on purpose (radii 24/18/28, middle staggered) */}
      <div style={{ display: "grid", gridTemplateColumns: "1.25fr .85fr 1fr", gap: 14 }}>
        <div style={statBase(24, "#2A9D8F", 20)}>
          <div style={{ ...kicker, color: "#7d8f85" }}>Next up</div>
          <div style={{ fontSize: nextUp ? 20 : 34, fontWeight: 600, lineHeight: 1.2, marginTop: nextUp ? 6 : 0 }}>
            {nextUp ? nextUp.title : "—"}
          </div>
          <div style={{ fontSize: 12, color: "#8c8577" }}>
            {nextUp ? `${fmtDayLong(nextUp.start)} · ${fmtRange(nextUp.start, nextUp.end)}` : "nothing booked yet"}
          </div>
        </div>
        <div style={{ ...statBase(18, "#CBA39C", 26), marginTop: 12 }}>
          <div style={{ ...kicker, color: "#9d8680" }}>Outings</div>
          <div style={{ fontSize: 34, fontWeight: 600, lineHeight: 1.1 }}>{monthOutings}</div>
          <div style={{ fontSize: 12, color: "#8c8577" }}>this month</div>
        </div>
        <div style={statBase(28, "#DCA744", 24)}>
          <div style={{ ...kicker, color: "#a08a5f" }}>Open polls</div>
          <div style={{ fontSize: 34, fontWeight: 600, lineHeight: 1.1 }}>{openPolls.length}</div>
          <div style={{ fontSize: 12, color: "#8c8577" }}>
            {openPolls.length ? "waiting on votes" : "none right now"}
          </div>
        </div>
      </div>

      {/* lists: events & tasks, open polls */}
      <div style={{ display: "grid", gridTemplateColumns: "1.15fr 1fr .12fr", gap: 14, flex: 1, minHeight: 0, alignItems: "start" }}>
        <div style={{ ...glass(24), padding: "14px 12px", display: "flex", flexDirection: "column", gap: 2 }}>
          <div style={{ ...kicker, padding: "0 10px 6px" }}>Coming up</div>
          {upcoming.map((e) =>
            listRow(
              e.id, e.cat || "Event", e.title,
              sameDay(e.start, now) ? `Today · ${fmtRange(e.start, e.end)}` : `${fmtDayLong(e.start)}`,
              CAT_COLORS[e.cat] || "#D95D39",
              () => setModal({ type: "event", event: e })
            )
          )}
          {openTasks.map((t) =>
            listRow(
              t.id, "Task", t.title, t.due || "anytime", "#DCA744",
              () => setModal({ type: "task", task: t })
            )
          )}
          {upcoming.length + openTasks.length === 0 && (
            <div style={{ fontSize: 12.5, color: "#a09889", padding: "8px 10px" }}>
              No events or tasks yet.
            </div>
          )}
          {endCap}
        </div>

        <div style={{ ...glass(20), padding: "14px 12px", display: "flex", flexDirection: "column", gap: 2 }}>
          <div style={{ ...kicker, padding: "0 10px 6px" }}>Open polls</div>
          {openPolls.map((p) => (
            <div
              key={p.id}
              className="hov-row"
              onClick={() => setPage("polls")}
              style={{ display: "flex", flexDirection: "column", gap: 6, padding: "10px 10px", borderRadius: 13, cursor: "pointer" }}
            >
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                <span style={{ fontSize: 13.5, fontWeight: 600 }}>{p.title}</span>
                <span style={{ fontSize: 11.5, color: "#D95D39", fontWeight: 600 }}>
                  {p.yes + p.no} of {members.length} voted
                </span>
              </div>
              <div style={{ fontSize: 11.5, color: "#a09889" }}>{p.start_local}</div>
              <div style={{ height: 6, borderRadius: 4, background: "rgba(150,142,128,.2)" }}>
                <div
                  style={{
                    height: "100%", borderRadius: 4, background: "#2A9D8F",
                    width: `${Math.min(100, Math.round((p.yes / Math.max(1, p.min_yes)) * 100))}%`,
                    transition: "width .4s cubic-bezier(.4,0,.2,1)",
                  }}
                />
              </div>
              {(p.waiting_on || []).includes(me?.email) && (
                <div
                  className="hov-lift-sm"
                  style={{ ...sagePill(true), alignSelf: "flex-start", padding: "5px 12px", fontSize: 11.5 }}
                  onClick={(ev) => { ev.stopPropagation(); vote(p.id, true); }}
                >
                  Vote yes
                </div>
              )}
            </div>
          ))}
          {openPolls.length === 0 && (
            <div style={{ fontSize: 12.5, color: "#a09889", padding: "8px 10px" }}>
              No open polls.
            </div>
          )}
          {endCap}
        </div>

        {/* dashed "+" — new task/event */}
        <div
          className="hov-icon"
          onClick={() => setModal({ type: "newTask" })}
          title="New task"
          style={{
            border: "1.6px dashed rgba(150,142,128,.45)", borderRadius: 24,
            background: "rgba(255,253,247,.28)", display: "flex", alignItems: "center",
            justifyContent: "center", color: "#a49c8c", cursor: "pointer",
            transition: "all .2s", minHeight: 120, alignSelf: "stretch",
          }}
        >
          <PlusIcon size={22} />
        </div>
      </div>
    </div>
  );
}
