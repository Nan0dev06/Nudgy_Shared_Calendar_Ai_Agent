import { useState } from "react";
import { useApp } from "../ctx.js";
import { glass, heavy, dot, orbGradient, avatar } from "../theme.js";
import {
  HomeIcon, CalendarIcon, ActivityIcon, PollsIcon,
  ChevronLeft, ChevronRight, ChevronDown, PlusIcon, GearIcon, CheckIcon,
} from "../Icons.jsx";

const GROUP_COLORS = ["#2A9D8F", "#DCA744", "#D95D39", "#2B5B84", "#BCA9C9", "#CBA39C"];

const sectionHead = {
  fontSize: 10, fontWeight: 600, letterSpacing: ".08em", textTransform: "uppercase",
  color: "#a49c8c",
};

export default function Sidebar() {
  const {
    collapsed, setCollapsed, page, setPage, members, focusId, setFocusId,
    activeGroup, groups, activeGroupId, setActiveGroupId,
    groupOpen, setGroupOpen, setModal, me, displayName, drafts, setSettingsTab,
  } = useApp();

  const [groupsOpen, setGroupsOpen] = useState(true);
  const [peopleOpen, setPeopleOpen] = useState(true);

  const nav = (key, label, Icon) => {
    const on = page === key;
    return (
      <div
        key={key}
        onClick={() => setPage(key)}
        style={{
          display: "flex", alignItems: "center",
          gap: collapsed ? 0 : 11,
          padding: collapsed ? "10px 0" : "10px 12px",
          justifyContent: collapsed ? "center" : "flex-start",
          borderRadius: 13, cursor: "pointer", fontSize: 13.5, fontWeight: 600,
          color: on ? "#2D2D2D" : "#8c8577",
          background: on ? "rgba(255,253,247,.66)" : "transparent",
          boxShadow: on ? "0 2px 8px rgba(96,78,54,.07)" : "none",
          transition: "all .25s cubic-bezier(.4,0,.2,1)", whiteSpace: "nowrap",
        }}
        title={collapsed ? label : undefined}
      >
        <Icon />
        {!collapsed && <span className="sb-label">{label}</span>}
      </div>
    );
  };

  const collapser = (open, setOpen) => (
    <div
      className="hov-icon"
      style={{
        width: 20, height: 20, display: "flex", alignItems: "center", justifyContent: "center",
        borderRadius: 7, cursor: "pointer", color: "#a49c8c",
        transform: open ? "none" : "rotate(-90deg)", transition: "transform .25s cubic-bezier(.4,0,.2,1)",
      }}
      onClick={() => setOpen((v) => !v)}
    >
      <ChevronDown size={13} />
    </div>
  );

  // collapsible body: max-height animation keeps the open/close smooth
  const section = (open, children) => (
    <div
      style={{
        overflow: "hidden", maxHeight: open ? 400 : 0, opacity: open ? 1 : 0,
        transition: "max-height .35s cubic-bezier(.4,0,.2,1), opacity .3s",
        display: "flex", flexDirection: "column", gap: 2,
      }}
    >
      {children}
    </div>
  );

  return (
    <>
      <aside
        className={collapsed ? "sb-collapsed" : ""}
        style={{
          ...glass(26), width: collapsed ? 78 : 240,
          margin: "16px 0 16px 16px",
          padding: collapsed ? "18px 12px" : "18px 14px",
          display: "flex", flexDirection: "column", gap: 12, flex: "none",
          transition:
            "width .38s cubic-bezier(.4,0,.2,1), padding .38s cubic-bezier(.4,0,.2,1)",
          zIndex: 6, overflow: "hidden auto",
        }}
      >
        <div
          style={{
            display: "flex", alignItems: "center", gap: 10,
            justifyContent: collapsed ? "center" : "flex-start",
          }}
        >
          <div
            style={{
              width: 30, height: 30, flex: "none", borderRadius: "50%",
              background: orbGradient(18),
              boxShadow: "0 4px 12px rgba(45,45,45,.18)",
            }}
          />
          {!collapsed && (
            <>
              <div style={{ display: "flex", flexDirection: "column", minWidth: 0 }}>
                <span className="sb-label" style={{ fontSize: 15, fontWeight: 600 }}>Nudgy</span>
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
          <div style={{ display: "flex", justifyContent: "center" }}>
            <div
              className="hov-icon"
              style={{ width: 24, height: 24, display: "flex", alignItems: "center", justifyContent: "center", borderRadius: 8, cursor: "pointer", color: "#a49c8c" }}
              onClick={() => setCollapsed(false)}
            >
              <ChevronRight />
            </div>
          </div>
        )}

        <div style={{ display: "flex", flexDirection: "column", gap: 3 }}>
          {nav("home", "Home", HomeIcon)}
          {nav("calendar", "Calendar", CalendarIcon)}
          {nav("activity", "Activity", ActivityIcon)}
          {nav("polls", "Polls", PollsIcon)}
        </div>

        {/* Create — a simple rounded square with a plus, there even when collapsed */}
        <div
          title="Create…"
          onClick={() => setModal({ type: "create" })}
          className="hov-lift-sm"
          style={{
            alignSelf: collapsed ? "center" : "stretch",
            width: collapsed ? 40 : undefined, height: 40,
            borderRadius: 14, cursor: "pointer",
            display: "flex", alignItems: "center", justifyContent: "center", gap: 8,
            background: "linear-gradient(160deg, rgba(64,60,50,.92), rgba(45,45,45,.84))",
            color: "#F7F2EA", boxShadow: "0 8px 18px rgba(45,38,28,.24)",
            fontSize: 13, fontWeight: 600, transition: "all .25s cubic-bezier(.4,0,.2,1)",
          }}
        >
          <PlusIcon size={15} />
          {!collapsed && <span className="sb-label">Create</span>}
        </div>

        {/* Drafts — things started without a time, waiting to be finished */}
        {!collapsed && drafts.length > 0 && (
          <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
            <div style={{ ...sectionHead, padding: "2px 4px 4px" }}>In progress</div>
            {drafts.map((d) => (
              <div
                key={d.id}
                className="hov-row"
                onClick={() => setModal({ type: d.kind === "task" ? "newTask" : d.kind === "plan" ? "newPoll" : "newEvent", draft: d })}
                title="No time set yet — click to finish"
                style={{ display: "flex", alignItems: "center", gap: 8, padding: "7px 9px", borderRadius: 11, cursor: "pointer" }}
              >
                <div style={{ ...dot("#DCA744"), width: 7, height: 7 }} />
                <span className="sb-label" style={{ fontSize: 12, fontWeight: 600, overflow: "hidden", textOverflow: "ellipsis", flex: 1 }}>
                  {d.title || "Untitled"}
                </span>
                <span style={{ fontSize: 9.5, color: "#a49c8c", textTransform: "uppercase", letterSpacing: ".05em", flex: "none" }}>
                  {d.kind}
                </span>
              </div>
            ))}
          </div>
        )}

        {/* Groups — the switcher, right above People and impossible to miss */}
        {!collapsed && (
          <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "0 4px 2px" }}>
              <span style={sectionHead}>Groups</span>
              <div style={{ display: "flex", alignItems: "center", gap: 2 }}>
                <div
                  className="hov-icon"
                  style={{ width: 20, height: 20, display: "flex", alignItems: "center", justifyContent: "center", borderRadius: 7, cursor: "pointer", color: "#a49c8c" }}
                  onClick={() => setModal({ type: "newGroup" })}
                  title="New group / join"
                >
                  <PlusIcon />
                </div>
                {collapser(groupsOpen, setGroupsOpen)}
              </div>
            </div>
            {section(
              groupsOpen,
              groups.map((g, i) => (
                <div
                  key={g.id}
                  className="hov-row"
                  onClick={() => setActiveGroupId(g.id)}
                  style={{
                    display: "flex", alignItems: "center", gap: 9, padding: "7px 9px",
                    borderRadius: 11, cursor: "pointer",
                    background: g.id === activeGroupId ? "rgba(255,253,247,.66)" : "transparent",
                    boxShadow: g.id === activeGroupId ? "0 2px 8px rgba(96,78,54,.07)" : "none",
                    transition: "all .2s",
                  }}
                >
                  <div style={avatar(GROUP_COLORS[i % GROUP_COLORS.length], 18)} />
                  <span className="sb-label" style={{ flex: 1, fontSize: 12.5, fontWeight: g.id === activeGroupId ? 600 : 500, overflow: "hidden", textOverflow: "ellipsis" }}>
                    {g.name}
                  </span>
                  {g.id === activeGroupId && <CheckIcon size={12} color="#2A9D8F" />}
                </div>
              ))
            )}
          </div>
        )}

        {collapsed && (
          <div
            title={activeGroup ? `Group: ${activeGroup.name}` : "Groups"}
            onClick={() => setCollapsed(false)}
            style={{ display: "flex", justifyContent: "center", cursor: "pointer" }}
          >
            <div style={avatar(GROUP_COLORS[Math.max(0, groups.findIndex((g) => g.id === activeGroupId)) % GROUP_COLORS.length], 20)} />
          </div>
        )}

        {/* People in the active group */}
        {!collapsed && (
          <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "0 4px 2px" }}>
              <span style={sectionHead}>
                People{activeGroup ? ` · ${activeGroup.name}` : ""}
              </span>
              <div style={{ display: "flex", alignItems: "center", gap: 2 }}>
                <div
                  className="hov-icon"
                  style={{ width: 20, height: 20, display: "flex", alignItems: "center", justifyContent: "center", borderRadius: 7, cursor: "pointer", color: "#a49c8c" }}
                  onClick={() => setModal({ type: "invite" })}
                  title="Invite people"
                >
                  <PlusIcon />
                </div>
                {collapser(peopleOpen, setPeopleOpen)}
              </div>
            </div>
            {section(
              peopleOpen,
              <>
                {members.map((m) => (
                  <div
                    key={m.email}
                    onMouseEnter={() => setFocusId(m.email)}
                    onMouseLeave={() => setFocusId(null)}
                    title={m.email}
                    style={{
                      display: "flex", alignItems: "center", gap: 10,
                      padding: "7px 9px", borderRadius: 11, cursor: "pointer",
                      background: focusId === m.email ? "rgba(255,253,247,.66)" : "transparent",
                      transition: "all .2s",
                    }}
                  >
                    <div style={dot(m.color)} />
                    <div style={{ display: "flex", flexDirection: "column", minWidth: 0 }}>
                      <span className="sb-label" style={{ fontSize: 12.5, fontWeight: 600 }}>
                        {m.name}
                        {m.isMe ? " (you)" : ""}
                      </span>
                      <span className="sb-label" style={{ fontSize: 10.5, color: "#a09889" }}>
                        {m.connected ? "calendar connected" : "no calendar yet"}
                      </span>
                    </div>
                  </div>
                ))}
                {members.length === 0 && (
                  <div style={{ fontSize: 11.5, color: "#a09889", padding: "4px 9px" }}>
                    No group selected
                  </div>
                )}
              </>
            )}
          </div>
        )}

        {collapsed && (
          <div style={{ display: "flex", flexDirection: "column", gap: 8, alignItems: "center" }}>
            {members.slice(0, 5).map((m) => (
              <div
                key={m.email}
                title={m.name}
                onMouseEnter={() => setFocusId(m.email)}
                onMouseLeave={() => setFocusId(null)}
                style={dot(m.color)}
              />
            ))}
          </div>
        )}

        <div
          style={{
            marginTop: "auto", paddingTop: 12,
            borderTop: "1px solid rgba(150,142,128,.2)",
            display: "flex", alignItems: "center", gap: 9,
            flexDirection: collapsed ? "column" : "row",
          }}
        >
          <div
            onClick={() => { setPage("settings"); setSettingsTab("Account"); }}
            className="hov-lift-sm"
            style={{
              width: 28, height: 28, flex: "none", borderRadius: "50%", cursor: "pointer",
              background: "linear-gradient(160deg, #2A9D8F, #237c72)",
              border: "2px solid rgba(255,253,247,.8)",
              display: "flex", alignItems: "center", justifyContent: "center",
              color: "#fff", fontSize: 10, fontWeight: 600, transition: "all .18s",
            }}
            title="Your profile"
          >
            {displayName.charAt(0).toUpperCase()}
          </div>
          {!collapsed && (
            <div
              style={{ display: "flex", flexDirection: "column", minWidth: 0, flex: 1, cursor: "pointer" }}
              onClick={() => { setPage("settings"); setSettingsTab("Account"); }}
              title="Your profile"
            >
              <span className="sb-label" style={{ fontSize: 12.5, fontWeight: 600, overflow: "hidden", textOverflow: "ellipsis" }}>
                {displayName}
              </span>
              <span className="sb-label" style={{ fontSize: 10.5, color: "#a09889" }}>You</span>
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

      {/* legacy popover no longer used — the switcher lives in the sidebar */}
      {groupOpen && (
        <div style={{ position: "fixed", inset: 0, zIndex: 50 }} onClick={() => setGroupOpen(false)} />
      )}
    </>
  );
}
