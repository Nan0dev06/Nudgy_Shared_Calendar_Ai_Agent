import { useState } from "react";
import { useApp } from "../ctx.js";
import {
  glass, gpill, dpill, dashPill, avatar, fieldStyle, fieldRead, fieldLabel,
  toggleStyle, knobStyle, prefCard,
} from "../theme.js";

const TABS = ["Account", "Preferences", "Memory", "Groups", "Notifications"];

export default function SettingsPage() {
  const {
    me, displayName, profile, setProfile, prefs, setPrefs, memory, setMemory,
    groups, members, activeGroup, setModal, logout, setSettingsTab, settingsTab,
  } = useApp();

  const [memInput, setMemInput] = useState("");
  const [editingName, setEditingName] = useState(false);
  const [nameDraft, setNameDraft] = useState(displayName);
  const [editIdx, setEditIdx] = useState(null);
  const [editDraft, setEditDraft] = useState("");

  const toggleRow = (key, label, sub) => (
    <div key={key} style={prefCard}>
      <div style={{ flex: 1 }}>
        <div style={{ fontSize: 14, fontWeight: 600 }}>{label}</div>
        <div style={{ fontSize: 12, color: "#8c8577", marginTop: 2 }}>{sub}</div>
      </div>
      <div style={toggleStyle(prefs[key])} onClick={() => setPrefs((p) => ({ ...p, [key]: !p[key] }))}>
        <div style={knobStyle(prefs[key])} />
      </div>
    </div>
  );

  const addMemory = () => {
    const t = memInput.trim();
    if (!t) return;
    setMemory((m) => [...m, t]);
    setMemInput("");
  };

  return (
    <div style={{ flex: 1, minHeight: 0, display: "flex", gap: 16, animation: "fadeUp .35s cubic-bezier(.4,0,.2,1)" }}>
      <div style={{ ...glass(22), width: 200, flex: "none", padding: "14px 11px", display: "flex", flexDirection: "column", gap: 3 }}>
        {TABS.map((t) => (
          <div
            key={t}
            onClick={() => setSettingsTab(t)}
            style={{
              padding: "10px 12px", borderRadius: 12, fontSize: 13,
              fontWeight: settingsTab === t ? 600 : 500, cursor: "pointer",
              color: settingsTab === t ? "#2D2D2D" : "#8c8577",
              background: settingsTab === t ? "rgba(255,253,247,.66)" : "transparent",
              transition: "all .18s",
            }}
          >
            {t}
          </div>
        ))}
        <div
          style={{ marginTop: "auto", padding: "10px 12px", borderRadius: 12, fontSize: 13, fontWeight: 500, color: "#D95D39", cursor: "pointer" }}
          onClick={logout}
        >
          Log out
        </div>
      </div>

      <div style={{ ...glass(22), flex: 1, minWidth: 0, padding: "20px 22px", display: "flex", flexDirection: "column", gap: 16, overflow: "auto" }}>
        {settingsTab === "Account" && (
          <>
            <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
              <div
                style={{
                  width: 52, height: 52, borderRadius: "50%",
                  background: "linear-gradient(160deg, #2A9D8F, #237c72)",
                  border: "2.5px solid rgba(255,253,247,.85)",
                  display: "flex", alignItems: "center", justifyContent: "center",
                  color: "#fff", fontSize: 18, fontWeight: 600,
                }}
              >
                {displayName.charAt(0).toUpperCase()}
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
                <span style={{ fontSize: 16, fontWeight: 600 }}>{displayName}</span>
                <span style={{ fontSize: 12, color: "#a09889" }}>{me?.email}</span>
              </div>
            </div>

            <div style={{ display: "grid", gridTemplateColumns: "1.3fr 1fr", gap: 14 }}>
              <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                <span style={fieldLabel}>Display name</span>
                {editingName ? (
                  <div style={{ display: "flex", gap: 8 }}>
                    <input
                      autoFocus
                      value={nameDraft}
                      onChange={(e) => setNameDraft(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") {
                          setProfile((p) => ({ ...p, name: nameDraft.trim() || displayName }));
                          setEditingName(false);
                        }
                      }}
                      style={fieldStyle}
                    />
                    <div
                      className="hov-lift-sm"
                      style={dpill(true)}
                      onClick={() => {
                        setProfile((p) => ({ ...p, name: nameDraft.trim() || displayName }));
                        setEditingName(false);
                      }}
                    >
                      Save
                    </div>
                  </div>
                ) : (
                  <div
                    style={{ ...fieldRead, cursor: "pointer" }}
                    title="Click to edit"
                    onClick={() => { setNameDraft(displayName); setEditingName(true); }}
                  >
                    {displayName} <span style={{ color: "#a49c8c", fontSize: 11 }}>· edit</span>
                  </div>
                )}
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                <span style={fieldLabel}>Timezone</span>
                <div style={fieldRead}>{me?.timezone}</div>
              </div>
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1.3fr", gap: 14 }}>
              <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                <span style={fieldLabel}>Email</span>
                <div style={fieldRead}>{me?.email}</div>
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                <span style={fieldLabel}>Calendar</span>
                <div style={fieldRead}>
                  {me?.calendar_connected ? "Google Calendar connected" : "Not connected"}
                </div>
              </div>
            </div>

            <div style={{ height: 1, background: "rgba(150,142,128,.22)" }} />
            <div style={{ display: "flex", flexDirection: "column", gap: 9 }}>
              <span style={fieldLabel}>What the agent remembers about you</span>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
                {memory.slice(0, 6).map((m, i) => (
                  <div key={i} style={gpill(true)}>{m}</div>
                ))}
                <div style={dashPill(true)} onClick={() => setSettingsTab("Memory")}>
                  + add
                </div>
              </div>
            </div>
          </>
        )}

        {settingsTab === "Preferences" && (
          <>
            {toggleRow("push", "Push notifications", "Votes, RSVPs, mentions")}
            {toggleRow("digest", "Weekly email digest", "Sunday evening summary")}
            {toggleRow("auto", "Auto-decline conflicts", "Skip polls that clash with existing events")}
            {toggleRow("busy", "Share busy times only", "The agent never sees your event titles or details")}
          </>
        )}

        {settingsTab === "Memory" && (
          <>
            <div style={{ fontSize: 13, color: "#8c8577", marginTop: -6 }}>
              What the agent has learned about your group — correct anything
              that's wrong. Orbi reads this when planning.
            </div>
            {memory.map((m, i) => (
              <div key={i} style={prefCard}>
                {editIdx === i ? (
                  <input
                    autoFocus
                    value={editDraft}
                    onChange={(e) => setEditDraft(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") {
                        setMemory((ms) => ms.map((x, j) => (j === i ? editDraft.trim() || x : x)));
                        setEditIdx(null);
                      }
                    }}
                    onBlur={() => {
                      setMemory((ms) => ms.map((x, j) => (j === i ? editDraft.trim() || x : x)));
                      setEditIdx(null);
                    }}
                    style={{ ...fieldStyle, flex: 1 }}
                  />
                ) : (
                  <span style={{ flex: 1, fontSize: 13.5 }}>{m}</span>
                )}
                <span
                  style={{ fontSize: 12, fontWeight: 600, color: "#2B5B84", cursor: "pointer" }}
                  onClick={() => { setEditIdx(i); setEditDraft(m); }}
                >
                  Edit
                </span>
                <span
                  style={{ fontSize: 12, fontWeight: 600, color: "#b08a80", cursor: "pointer" }}
                  onClick={() => setMemory((ms) => ms.filter((_, j) => j !== i))}
                >
                  Remove
                </span>
              </div>
            ))}
            <div style={{ display: "flex", gap: 10 }}>
              <input
                placeholder="Teach it something — e.g. Aya can't do Fridays"
                value={memInput}
                onChange={(e) => setMemInput(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && addMemory()}
                style={fieldStyle}
              />
              <div className="hov-lift-sm" style={dpill(true)} onClick={addMemory}>
                Add
              </div>
            </div>
          </>
        )}

        {settingsTab === "Groups" && (
          <>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
              <span style={{ fontSize: 16, fontWeight: 600 }}>Your groups</span>
              <div className="hov-lift-sm" style={dpill(true)} onClick={() => setModal({ type: "newGroup" })}>
                + New group
              </div>
            </div>
            {groups.map((g) => (
              <div key={g.id} style={prefCard}>
                <div style={{ flex: 1 }}>
                  <div style={{ fontSize: 14.5, fontWeight: 600 }}>{g.name}</div>
                  <div style={{ fontSize: 12, color: "#8c8577", marginTop: 2 }}>
                    Invite code: <b style={{ letterSpacing: ".08em" }}>{g.invite_code}</b>
                  </div>
                </div>
                {g.id === activeGroup?.id && (
                  <div style={{ display: "flex" }}>
                    {members.map((m, i) => (
                      <div key={m.email} title={m.name} style={{ ...avatar(m.color, 24), marginRight: i < members.length - 1 ? -7 : 0 }} />
                    ))}
                  </div>
                )}
                <CopyChip text={g.invite_code} />
              </div>
            ))}
            {activeGroup && (
              <div className="hov-glass" style={{ ...gpill(true), alignSelf: "flex-start" }} onClick={() => setModal({ type: "invite" })}>
                Invite people to {activeGroup.name}
              </div>
            )}
          </>
        )}

        {settingsTab === "Notifications" && (
          <>
            {toggleRow("nvote", "Votes needed", "When a poll is waiting on you")}
            {toggleRow("nrsvp", "RSVP updates", "When someone answers going or can't")}
            {toggleRow("nment", "Mentions", "When someone tags you")}
            {toggleRow("ndigest", "Quiet hours", "Mute everything 10pm–8am")}
          </>
        )}
      </div>
    </div>
  );
}

function CopyChip({ text }) {
  const [copied, setCopied] = useState(false);
  return (
    <div
      className="hov-glass"
      style={gpill(true)}
      onClick={() => {
        navigator.clipboard?.writeText(text);
        setCopied(true);
        setTimeout(() => setCopied(false), 1500);
      }}
    >
      {copied ? "Copied ✓" : "Copy code"}
    </div>
  );
}
