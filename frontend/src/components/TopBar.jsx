import { useApp } from "../ctx.js";
import { glass, gpill, heavy, dot } from "../theme.js";
import { SearchIcon, BellIcon } from "../Icons.jsx";
import { fmtKicker } from "../dates.js";

const TITLES = {
  home: null, // greeting, computed below
  calendar: "Calendar",
  activity: "Activity",
  polls: "Polls",
  settings: "Settings",
};

export default function TopBar() {
  const {
    page, displayName, members, notifOpen, setNotifOpen, setGroupOpen,
    notifs, unread, readNotifs, setReadNotifs,
  } = useApp();

  const hour = new Date().getHours();
  const greet = hour < 12 ? "Good morning" : hour < 18 ? "Good afternoon" : "Good evening";
  const title = TITLES[page] || `${greet}, ${displayName}`;

  // member avatar stack — real people, tooltips with names (not just circles)
  const stack = members.slice(0, 4);

  return (
    <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 12, position: "relative" }}>
      <div>
        <div style={{ fontSize: 10.5, fontWeight: 600, letterSpacing: ".08em", textTransform: "uppercase", color: "#a49c8c" }}>
          {fmtKicker(new Date())}
        </div>
        <div style={{ fontSize: 25, fontWeight: 600, marginTop: 5 }}>{title}</div>
      </div>

      <div style={{ display: "flex", alignItems: "center", gap: 11 }}>
        <div
          style={{
            ...glass(999), height: 38, width: 190, display: "flex", alignItems: "center",
            gap: 9, padding: "0 15px",
            boxShadow: "0 1px 2px rgba(96,78,54,.06), 0 6px 14px rgba(96,78,54,.08)",
          }}
        >
          <SearchIcon />
          <span style={{ fontSize: 12.5, color: "#a09889" }}>Search</span>
        </div>

        <div
          className="hov-glass"
          style={{
            ...gpill(true), width: 40, height: 40, borderRadius: "50%", padding: 0,
            position: "relative", justifyContent: "center",
          }}
          onClick={() => { setNotifOpen((v) => !v); setGroupOpen(false); }}
        >
          <BellIcon />
          {unread && (
            <span
              style={{
                position: "absolute", top: 9, right: 10, width: 7, height: 7,
                borderRadius: "50%", background: "#D95D39",
                border: "1.5px solid rgba(255,253,247,.9)",
              }}
            />
          )}
        </div>

        {/* the group's member avatars (hover a circle to see who it is) */}
        <div style={{ display: "flex" }} title="Group members">
          {stack.map((m, i) => (
            <div
              key={m.email}
              title={`${m.name} · ${m.email}`}
              style={{
                width: 28, height: 28, borderRadius: "50%", background: m.color,
                border: "2px solid rgba(255,253,247,.85)",
                marginRight: i < stack.length - 1 ? -7 : 0,
                display: "flex", alignItems: "center", justifyContent: "center",
                color: "#fff", fontSize: 9.5, fontWeight: 600,
              }}
            >
              {m.initials}
            </div>
          ))}
        </div>
      </div>

      {notifOpen && (
        <>
          <div style={{ position: "fixed", inset: 0, zIndex: 50 }} onClick={() => setNotifOpen(false)} />
          <div
            style={{
              ...heavy(20), position: "absolute", top: 52, right: 0, width: 320,
              padding: 10, display: "flex", flexDirection: "column", gap: 1, zIndex: 60,
              animation: "popIn .18s cubic-bezier(.4,0,.2,1)",
            }}
          >
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "4px 8px 6px" }}>
              <span style={{ fontSize: 10, fontWeight: 600, letterSpacing: ".08em", textTransform: "uppercase", color: "#a49c8c" }}>
                Notifications
              </span>
              <span
                style={{ fontSize: 11, fontWeight: 600, color: "#2B5B84", cursor: "pointer" }}
                onClick={() => setReadNotifs(notifs.map((n) => n.id))}
              >
                Mark all read
              </span>
            </div>
            {notifs.map((n) => {
              const read = readNotifs.includes(n.id);
              return (
                <div
                  key={n.id}
                  className="hov-row"
                  style={{ display: "flex", alignItems: "center", gap: 10, padding: "9px 10px", borderRadius: 12, cursor: "pointer" }}
                >
                  <div
                    style={{
                      ...dot(read ? "transparent" : n.dot),
                      border: read ? "1.4px solid #cbc3b3" : "none",
                    }}
                  />
                  <span style={{ fontSize: 12.5, lineHeight: 1.4, color: read ? "#a09889" : "#2D2D2D" }}>
                    {n.pre}<b>{n.bold}</b>{n.post}
                  </span>
                </div>
              );
            })}
            {notifs.length === 0 && (
              <div style={{ padding: "14px 10px", fontSize: 12.5, color: "#a09889" }}>
                Nothing yet — you're all caught up.
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}
