import { useApp } from "../ctx.js";
import { glass, dot, kicker } from "../theme.js";
import { relTime } from "../dates.js";

export default function ActivityPage() {
  const { activity } = useApp();

  const dayAgo = Date.now() - 86400000;
  const todayRows = activity.filter((a) => a.ts >= dayAgo);
  const earlier = activity.filter((a) => a.ts < dayAgo);

  const row = (a, i) => (
    <div
      key={i}
      className="hov-row"
      style={{ display: "flex", alignItems: "center", gap: 11, padding: "11px 10px", borderRadius: 13 }}
    >
      <div style={dot(a.dot)} />
      <span style={{ fontSize: 13.5, lineHeight: 1.4 }}>
        {a.pre}<b>{a.bold}</b>{a.post}
      </span>
      <span style={{ marginLeft: "auto", fontSize: 11.5, color: "#a09889", flex: "none" }}>
        {relTime(a.ts)}
      </span>
    </div>
  );

  return (
    <div style={{ flex: 1, minHeight: 0, overflow: "auto", animation: "fadeUp .35s cubic-bezier(.4,0,.2,1)" }}>
      <div style={{ ...glass(24), padding: 12, display: "flex", flexDirection: "column", gap: 1, maxWidth: 640 }}>
        <div style={{ ...kicker, padding: "6px 10px 4px" }}>Today</div>
        {todayRows.map(row)}
        {todayRows.length === 0 && (
          <div style={{ fontSize: 12.5, color: "#a09889", padding: "8px 10px" }}>
            Nothing yet today.
          </div>
        )}
        {earlier.length > 0 && (
          <>
            <div style={{ ...kicker, padding: "12px 10px 4px" }}>Earlier</div>
            {earlier.map(row)}
          </>
        )}
        <div style={{ textAlign: "center", fontSize: 12.5, color: "#a09889", padding: "14px 0 8px" }}>
          That's it for now!
        </div>
      </div>
    </div>
  );
}
