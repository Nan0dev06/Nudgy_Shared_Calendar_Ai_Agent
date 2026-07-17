import { useApp } from "../ctx.js";
import { glass, heavy, dot, orbGradient, avatar } from "../theme.js";
import {
  HomeIcon, CalendarIcon, ActivityIcon, PollsIcon,
  ChevronLeft, ChevronRight, PlusIcon, GearIcon, CheckIcon,
} from "../Icons.jsx";

const GROUP_COLORS = ["#2A9D8F", "#DCA744", "#D95D39", "#2B5B84", "#BCA9C9", "#CBA39C"];

export default function Sidebar() {
  const {
    collapsed, setCollapsed, page, setPage, members, focusId, setFocusId,
    activeGroup, groups, activeGroupId, setActiveGroupId,
    groupOpen, setGroupOpen, setNotifOpen, setModal, me, displayName,
  } = useApp();

  const nav = (key, label, Icon) => {
    const on = page === key;
    return (
      <div
        key={key}
        onClick={() => setPage(key)}
        style={{
          display: "flex", alignItems: "center",
          gap: collapsed ? 0 : 11,
          // collapsed: perfectly centered icon pills (fixes the misplaced icons)
          padding: collapsed ? "10px 0" : "10px 12px",
          justifyContent: collapsed ? "center" : "flex-start",
          borderRadius: 13, cursor: "pointer", fontSize: 13.5, fontWeight: 600,
          color: on ? "#2D2D2D" : "#8c8577",
          background: on ? "rgba(255,253,247,.66)" : "transparent",
          boxShadow: on ? "0 2px 8px rgba(96,78,54,.07)" : "none",
          transition: "all .2s", whiteSpace: "nowrap",
        }}
        title={collapsed ? label : undefined}
      >
        <Icon />
        {!collapsed && <span>{label}</span>}
      </div>
    );
  };

  return (
    <>
      <aside
        style={{
          ...glass(26), width: collapsed ? 78 : 240,
          margin: "16px 0 16px 16px",
          padding: collapsed ? "18px 12px" : "18px 14px",
          display: "flex", flexDirection: "column", gap: 14, flex: "none",
          transition: "width .3s cubic-bezier(.4,0,.2,1), padding .3s",
          zIndex: 6, overflow: "hidden",
        }}
      >
        <div
          style={{
            display: "flex", alignItems: "center", gap: 10,
            justifyContent: collapsed ? "center" : "flex-start",
          }}
        >
          <div
            onClick={() => setGroupOpen((v) => !v)}
            style={{
              width: 30, height: 30, flex: "none", borderRadius: "50%", cursor: "pointer",
              background: orbGradient(18),
              boxShadow: "0 4px 12px rgba(45,45,45,.18)",
            }}
          />
          {!collapsed && (
            <>
              <div
                style={{ display: "flex", flexDirection: "column", minWidth: 0, cursor: "pointer" }}
                onClick={() => setGroupOpen((v) => !v)}
              >
                <span style={{ fontSize: 15, fontWeight: 600, whiteSpace: "nowrap" }}>Overlap</span>
                <span style={{ fontSize: 10, fontWeight: 600, letterSpacing: ".06em", textTransform: "uppercase", color: "#a49c8c", whiteSpace: "nowrap" }}>
                  {activeGroup ? activeGroup.name : "No group yet"}
                </span>
              </div>
              <div
                className="hov-icon"
                style={{ marginLeft: "auto", width: 24, height: 24, flex: "none", display: "flex", alignItems: "center", justifyContent: "center", borderRadius: 8, cursor: "pointer", color: "#a49c8c" }}
                onClick={() => setCollapsed(true)}
              >
                <ChevronLeft />
              </div>
            </>
          )}
        </div>

        {collapsed && (
          <div style={{ display: "flex", justifyContent: "center", padding: "2px 0" }}>
            <div
              className="hov-icon"
              style={{ width: 24, height: 24, display: "flex", alignItems: "center", justifyContent: "center", borderRadius: 8, cursor: "pointer", color: "#a49c8c" }}
              onClick={() => setCollapsed(false)}
            >
              <ChevronRight />
            </div>
          </div>
        )}

        <div style={{ display: "flex", flexDirection: "column", gap: 3, marginTop: 2 }}>
          {nav("home", "Home", HomeIcon)}
          {nav("calendar", "Calendar", CalendarIcon)}
          {nav("activity", "Activity", ActivityIcon)}
          {nav("polls", "Polls", PollsIcon)}
        </div>

        {!collapsed && (
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginTop: 10, padding: "0 4px" }}>
            <span style={{ fontSize: 10, fontWeight: 600, letterSpacing: ".08em", textTransform: "uppercase", color: "#a49c8c" }}>People</span>
            <div
              className="hov-icon"
              style={{ width: 20, height: 20, display: "flex", alignItems: "center", justifyContent: "center", borderRadius: 7, cursor: "pointer", color: "#a49c8c" }}
              onClick={() => setModal({ type: "invite" })}
              title="Invite people"
            >
              <PlusIcon />
            </div>
          </div>
        )}

        <div style={{ display: "flex", flexDirection: "column", gap: 2, marginTop: collapsed ? 10 : 2 }}>
          {members.map((m) => (
            <div
              key={m.email}
              onMouseEnter={() => setFocusId(m.email)}
              onMouseLeave={() => setFocusId(null)}
              title={m.email}
              style={{
                display: "flex", alignItems: "center", gap: 10,
                padding: collapsed ? "7px 0" : "7px 9px", borderRadius: 11, cursor: "pointer",
                justifyContent: collapsed ? "center" : "flex-start",
                background: focusId === m.email ? "rgba(255,253,247,.66)" : "transparent",
                transition: "all .2s",
              }}
            >
              <div style={dot(m.color)} />
              {!collapsed && (
                <div style={{ display: "flex", flexDirection: "column", minWidth: 0 }}>
                  <span style={{ fontSize: 12.5, fontWeight: 600, whiteSpace: "nowrap" }}>
                    {m.name}
                    {m.isMe ? " (you)" : ""}
                  </span>
                  <span style={{ fontSize: 10.5, color: "#a09889", whiteSpace: "nowrap" }}>
                    {m.connected ? "calendar connected" : "no calendar yet"}
                  </span>
                </div>
              )}
            </div>
          ))}
          {members.length === 0 && !collapsed && (
            <div style={{ fontSize: 11.5, color: "#a09889", padding: "4px 9px" }}>
              No group selected
            </div>
          )}
        </div>

        <div
          style={{
            marginTop: "auto", paddingTop: 12,
            borderTop: "1px solid rgba(150,142,128,.2)",
            display: "flex", alignItems: "center", gap: 9,
            flexDirection: collapsed ? "column" : "row",
          }}
        >
          <div
            style={{
              width: 28, height: 28, flex: "none", borderRadius: "50%",
              background: "linear-gradient(160deg, #2A9D8F, #237c72)",
              border: "2px solid rgba(255,253,247,.8)",
              display: "flex", alignItems: "center", justifyContent: "center",
              color: "#fff", fontSize: 10, fontWeight: 600,
            }}
            title={me?.email}
          >
            {displayName.charAt(0).toUpperCase()}
          </div>
          {!collapsed && (
            <div style={{ display: "flex", flexDirection: "column", minWidth: 0, flex: 1 }}>
              <span style={{ fontSize: 12.5, fontWeight: 600, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                {displayName}
              </span>
              <span style={{ fontSize: 10.5, color: "#a09889" }}>You</span>
            </div>
          )}
          <div
            title="Settings"
            onClick={() => setPage("settings")}
            style={{
              marginLeft: collapsed ? 0 : "auto", width: 28, height: 28, borderRadius: 10,
              display: "flex", alignItems: "center", justifyContent: "center", cursor: "pointer",
              color: page === "settings" ? "#2D2D2D" : "#8c8577",
              background: page === "settings" ? "rgba(255,253,247,.75)" : "rgba(255,255,255,.35)",
              border: "1px solid rgba(255,255,255,.55)", flex: "none", transition: "all .18s",
            }}
          >
            <GearIcon />
          </div>
        </div>
      </aside>

      {/* group switcher popover */}
      {groupOpen && (
        <>
          <div style={{ position: "fixed", inset: 0, zIndex: 50 }} onClick={() => setGroupOpen(false)} />
          <div
            style={{
              ...heavy(20), position: "absolute", top: 76, left: 26, width: 240,
              padding: 9, display: "flex", flexDirection: "column", gap: 2, zIndex: 60,
              animation: "popIn .18s cubic-bezier(.4,0,.2,1)",
            }}
          >
            {groups.map((g, i) => (
              <div
                key={g.id}
                className="hov-row"
                onClick={() => { setActiveGroupId(g.id); setGroupOpen(false); }}
                style={{
                  display: "flex", alignItems: "center", gap: 10, padding: "9px 10px",
                  borderRadius: 11, cursor: "pointer",
                  background: g.id === activeGroupId ? "rgba(255,253,247,.5)" : "transparent",
                }}
              >
                <div style={avatar(GROUP_COLORS[i % GROUP_COLORS.length], 20)} />
                <span style={{ flex: 1, fontSize: 13, fontWeight: g.id === activeGroupId ? 600 : 500 }}>
                  {g.name}
                </span>
                {g.id === activeGroupId && <CheckIcon color="#2A9D8F" />}
              </div>
            ))}
            <div style={{ height: 1, background: "rgba(150,142,128,.22)", margin: "4px 6px" }} />
            <div
              className="hov-row"
              style={{ padding: "9px 10px", fontSize: 12.5, fontWeight: 600, color: "#2B5B84", cursor: "pointer", borderRadius: 11 }}
              onClick={() => { setGroupOpen(false); setModal({ type: "newGroup" }); }}
            >
              + New group
            </div>
          </div>
        </>
      )}
    </>
  );
}
