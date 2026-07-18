import { useMemo, useEffect } from "react";
import { useApp } from "../ctx.js";
import { glass, gpill, dpill, kicker, dot, avatar } from "../theme.js";
import { StarRow } from "../components/Fields.jsx";
import { PinIcon, ClockIcon } from "../Icons.jsx";
import { fmtDayLong, fmtRange, relTime } from "../dates.js";
import { nameFromEmail } from "../people.js";

// Every place the group has touched — your reviews, your friends' reviews and
// the outings that happened there — folded into one profile per place.
export default function PlacesPage() {
  const {
    me, members, reviews, friendReviews, events, placeFocus, setPlaceFocus,
    setModal,
  } = useApp();

  const memberByEmail = Object.fromEntries(members.map((m) => [m.email, m]));

  const profiles = useMemo(() => {
    const map = new Map();
    const ensure = (name) => {
      if (!map.has(name))
        map.set(name, { name, mine: null, friends: [], visits: [] });
      return map.get(name);
    };
    for (const r of reviews) ensure(r.place).mine = r;
    for (const r of friendReviews) {
      if (r.email === me?.email) continue;
      ensure(r.place).friends.push(r);
    }
    for (const e of events) {
      if (e.where && e.where !== "—") ensure(e.where).visits.push(e);
    }
    const out = [...map.values()];
    for (const p of out) {
      const stars = [
        ...(p.mine ? [p.mine.stars] : []),
        ...p.friends.map((f) => f.stars),
      ];
      p.avg = stars.length
        ? Math.round((stars.reduce((a, b) => a + b, 0) / stars.length) * 10) / 10
        : null;
      p.nReviews = stars.length;
      p.visits.sort((a, b) => b.start - a.start);
    }
    out.sort(
      (a, b) =>
        (b.mine?.stars || 0) - (a.mine?.stars || 0) ||
        b.nReviews - a.nReviews ||
        b.visits.length - a.visits.length
    );
    return out;
  }, [reviews, friendReviews, events, me]);

  // keep the selection valid: default to the top place
  useEffect(() => {
    if (profiles.length && !profiles.some((p) => p.name === placeFocus))
      setPlaceFocus(profiles[0].name);
  }, [profiles, placeFocus, setPlaceFocus]);

  const sel = profiles.find((p) => p.name === placeFocus) || profiles[0] || null;
  const now = new Date();

  if (profiles.length === 0)
    return (
      <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center", animation: "fadeUp .35s cubic-bezier(.4,0,.2,1)" }}>
        <div style={{ textAlign: "center", maxWidth: 380 }}>
          <div style={{ fontSize: 18, fontWeight: 600 }}>No places yet</div>
          <div style={{ fontSize: 13, color: "#8c8577", marginTop: 6, lineHeight: 1.5 }}>
            Rate a spot after an outing, or add a location to an event — every
            place the group touches gets a profile here.
          </div>
          <div
            className="hov-lift-sm"
            style={{ ...dpill(true), marginTop: 14, display: "inline-flex" }}
            onClick={() => setModal({ type: "review" })}
          >
            ★ Rate a place
          </div>
        </div>
      </div>
    );

  const reviewRow = (email, stars, text, ts, isMe) => {
    const m = memberByEmail[email];
    const name = isMe ? "You" : m?.name || nameFromEmail(email || "");
    return (
      <div key={(email || "me") + ts} style={{ display: "flex", gap: 10, padding: "10px 12px", borderRadius: 13, background: "rgba(255,253,247,.5)", border: "1px solid rgba(255,255,255,.6)" }}>
        <div style={{ ...avatar(m?.color || "#2A9D8F", 24), display: "flex", alignItems: "center", justifyContent: "center", color: "#fff", fontSize: 9, fontWeight: 600, flex: "none" }}>
          {name.charAt(0).toUpperCase()}
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 3, minWidth: 0, flex: 1 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <span style={{ fontSize: 12.5, fontWeight: 600 }}>{name}</span>
            <StarRow value={stars} size={11} />
            {ts && <span style={{ fontSize: 10, color: "#a09889", marginLeft: "auto" }}>{relTime(typeof ts === "number" ? ts : Date.parse(ts))}</span>}
          </div>
          {text && <span style={{ fontSize: 12.5, color: "#5c564b", lineHeight: 1.45 }}>{text}</span>}
        </div>
      </div>
    );
  };

  return (
    <div style={{ flex: 1, minHeight: 0, display: "flex", gap: 16, animation: "fadeUp .35s cubic-bezier(.4,0,.2,1)" }}>
      {/* ---- left: every place the group knows ---- */}
      <div style={{ ...glass(22), width: 250, flex: "none", padding: "14px 11px", display: "flex", flexDirection: "column", gap: 3, overflow: "auto" }}>
        <div style={{ ...kicker, padding: "0 6px 6px" }}>Places · {profiles.length}</div>
        {profiles.map((p) => (
          <div
            key={p.name}
            onClick={() => setPlaceFocus(p.name)}
            className="hov-row"
            style={{
              display: "flex", flexDirection: "column", gap: 2, padding: "9px 11px",
              borderRadius: 12, cursor: "pointer",
              background: sel?.name === p.name ? "rgba(255,253,247,.66)" : "transparent",
              boxShadow: sel?.name === p.name ? "0 2px 8px rgba(96,78,54,.07)" : "none",
              transition: "all .18s",
            }}
          >
            <span style={{ fontSize: 13, fontWeight: 600, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
              {p.name}
            </span>
            <span style={{ fontSize: 11, color: "#a09889" }}>
              {p.avg ? `★ ${p.avg} · ${p.nReviews} review${p.nReviews > 1 ? "s" : ""}` : "no reviews yet"}
              {p.visits.length ? ` · ${p.visits.length} outing${p.visits.length > 1 ? "s" : ""}` : ""}
            </span>
          </div>
        ))}
      </div>

      {/* ---- right: the selected place's profile ---- */}
      {sel && (
        <div style={{ ...glass(22), flex: 1, minWidth: 0, padding: 0, display: "flex", flexDirection: "column", overflow: "hidden" }}>
          <div style={{ height: 84, background: "linear-gradient(120deg, #F3C9A8, #A9CBB6, #C9B6D4)", flex: "none" }} />
          <div style={{ padding: "18px 22px 22px", display: "flex", flexDirection: "column", gap: 16, overflow: "auto" }}>
            <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 12 }}>
              <div>
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <PinIcon size={16} />
                  <span style={{ fontSize: 22, fontWeight: 600 }}>{sel.name}</span>
                </div>
                <div style={{ fontSize: 12.5, color: "#8c8577", marginTop: 4 }}>
                  {sel.avg ? `★ ${sel.avg} from ${sel.nReviews} review${sel.nReviews > 1 ? "s" : ""}` : "No reviews yet"}
                  {sel.visits.length ? ` · ${sel.visits.length} outing${sel.visits.length > 1 ? "s" : ""} here` : ""}
                </div>
              </div>
              <div
                className="hov-lift-sm"
                style={dpill(true)}
                onClick={() => setModal({ type: "newPoll", proposeChange: { title: `Hang at ${sel.name}?`, location: sel.name } })}
              >
                Plan something here
              </div>
            </div>

            {/* your review — or the nudge to write one */}
            <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              <span style={kicker}>Your review</span>
              {sel.mine ? (
                reviewRow(me?.email, sel.mine.stars, sel.mine.text, sel.mine.ts, true)
              ) : (
                <div
                  className="hov-glass"
                  style={{ ...gpill(true), alignSelf: "flex-start", color: "#DCA744" }}
                  onClick={() => setModal({ type: "review", place: sel.name })}
                >
                  ★ Rate this place
                </div>
              )}
            </div>

            {/* friends' reviews */}
            <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              <span style={kicker}>Friends' reviews · {sel.friends.length}</span>
              {sel.friends.map((f) => reviewRow(f.email, f.stars, f.text, f.ts, false))}
              {sel.friends.length === 0 && (
                <span style={{ fontSize: 12.5, color: "#a09889" }}>
                  Nobody else has reviewed this spot yet.
                </span>
              )}
            </div>

            {/* outings there */}
            {sel.visits.length > 0 && (
              <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                <span style={kicker}>Outings here</span>
                {sel.visits.slice(0, 6).map((e) => (
                  <div
                    key={e.id}
                    className="hov-row"
                    onClick={() => setModal({ type: "event", event: e })}
                    style={{ display: "flex", alignItems: "center", gap: 10, padding: "8px 11px", borderRadius: 12, cursor: "pointer" }}
                  >
                    <div style={dot(e.end < now ? "#c9c2b4" : "#2A9D8F")} />
                    <span style={{ fontSize: 13, fontWeight: 600, flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                      {e.title}
                    </span>
                    <span style={{ fontSize: 11.5, color: "#a09889", display: "inline-flex", alignItems: "center", gap: 5, flex: "none" }}>
                      <ClockIcon size={12} />
                      {fmtDayLong(e.start)} · {fmtRange(e.start, e.end)}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
