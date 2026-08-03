import { useState } from "react";
import { useApp } from "../ctx.js";
import { heavy, gpill, dpill, fieldStyle, fieldLabel } from "../theme.js";
import { OrbLogo } from "../components/OrbLogo.jsx";

// Shown ONLY to someone with no groups at all — there is nowhere for them to
// plan yet, so there is nothing else to show. Creating or joining refreshes the
// group list, which is what moves them on; this screen doesn't need to say so.
// Adding more groups later happens from the shell, not from a door screen.
export default function GroupGate() {
  const { groups, createGroup, joinGroup } = useApp();
  const [mode, setMode] = useState(null); // null | 'create' | 'join'
  const [val, setVal] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  const submit = async () => {
    if (!val.trim() || busy) return;
    setBusy(true);
    setErr("");
    try {
      if (mode === "create") await createGroup(val.trim());
      else await joinGroup(val);
      // No onDone: both calls reload the group list, and a non-empty list is
      // what renders the shell.
    } catch (e) {
      setErr(e.message || "That didn't work — try again.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      style={{
        flex: 1, display: "flex", alignItems: "center", justifyContent: "center",
        position: "relative", zIndex: 1,
        animation: "fadeUp .35s cubic-bezier(.4,0,.2,1)",
      }}
    >
      <div
        style={{
          ...heavy(28), width: 400, padding: 28, display: "flex",
          flexDirection: "column", gap: 18,
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <div style={{ width: 32, height: 32, flex: "none", display: "flex" }}>
            <OrbLogo size={32} />
          </div>
          <span style={{ fontSize: 18, fontWeight: 600 }}>Nudgy</span>
        </div>
        <div>
          <div style={{ fontSize: 22, fontWeight: 600 }}>Your groups</div>
          <div style={{ fontSize: 13, color: "#8c8577", marginTop: 3 }}>
            {groups.length
              ? `You're in ${groups.length} group${groups.length > 1 ? "s" : ""}. Add another or continue.`
              : "Create a group or join one with an invite code."}
          </div>
        </div>

        {mode == null && (
          <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            <div className="hov-glass" style={{ ...gpill(false), justifyContent: "center" }} onClick={() => { setMode("create"); setVal(""); setErr(""); }}>
              Create a group
            </div>
            <div className="hov-glass" style={{ ...gpill(false), justifyContent: "center" }} onClick={() => { setMode("join"); setVal(""); setErr(""); }}>
              Join with an invite code
            </div>
          </div>
        )}

        {mode != null && (
          <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            <span style={fieldLabel}>
              {mode === "create" ? "Group name" : "Invite code"}
            </span>
            <input
              autoFocus
              placeholder={mode === "create" ? "e.g. Beirut Crew" : "e.g. 4PYJU8"}
              value={val}
              maxLength={mode === "join" ? 6 : 80}
              onChange={(e) => setVal(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && submit()}
              style={fieldStyle}
            />
            {err && (
              <div style={{ fontSize: 12, color: "#D95D39" }}>{err}</div>
            )}
            <div style={{ display: "flex", gap: 9 }}>
              <div
                className="hov-lift-sm"
                style={{ ...dpill(false), flex: 1, justifyContent: "center", opacity: busy ? 0.6 : 1 }}
                onClick={submit}
              >
                {busy ? "Working…" : mode === "create" ? "Create" : "Join"}
              </div>
              <div className="hov-glass" style={{ ...gpill(false), justifyContent: "center" }} onClick={() => setMode(null)}>
                Back
              </div>
            </div>
          </div>
        )}

      </div>
    </div>
  );
}
