import { useEffect, useRef, useState } from "react";
import { useApp } from "../ctx.js";
import { glass, gpill, heavy, dot, catChip } from "../theme.js";
import { SearchIcon, BellIcon } from "../Icons.jsx";
import { fmtKicker, fmtDayLong } from "../dates.js";

const TITLES = {
  home: null, // greeting, computed below
  calendar: "Calendar",
  activity: "Activity",
  polls: "Polls",
  places: "Places",
  settings: "Settings",
};

const CAT_COLORS = { Event: "#D95D39", Meet: "#2A9D8F", Call: "#DCA744", Task: "#DCA744", Poll: "#D95D39", Place: "#2B5B84" };

export default function TopBar() {
  const {
    page, displayName, members, notifOpen, setNotifOpen, setGroupOpen,
    notifs, unread, readNotifs, setReadNotifs,
    events, tasks, plans, setModal, setPage, setCalAnchor, setView,
    reviews, friendReviews, setPlaceFocus,
  } = useApp();

  const hour = new Date().getHours();
  const greet = hour < 12 ? "Good morning" : hour < 18 ? "Good afternoon" : "Good evening";
  const title = TITLES[page] || `${greet}, ${displayName}`;

  // ---- search over events, tasks and polls ---------------------------------
  const [q, setQ] = useState("");
  const [searchOpen, setSearchOpen] = useState(false);
  const searchRef = useRef(null);
  useEffect(() => {
    const onDoc = (e) => {
      if (searchRef.current && !searchRef.current.contains(e.target)) setSearchOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, []);

  const n = q.toLowerCase().trim();
  const hit = (s) => (s || "").toLowerCase().includes(n);
  // every place the app knows about — reviews (yours + friends') and event
  // locations — so "bhi" finds BHive's profile
  const knownPlaces = [
    ...new Set(
      [
        ...reviews.map((r) => r.place),
        ...friendReviews.map((r) => r.place),
        ...events.map((e) => e.where),
      ].filter((p) => p && p !== "—")
    ),
  ];
  const results = !n
    ? []
    : [
        ...events
          .filter((e) => hit(e.title))
          .slice(0, 4)
          .map((e) => ({
            kind: e.cat || "Event",
            title: e.title,
            meta: fmtDayLong(e.start),
            go: () => setModal({ type: "event", event: e }),
          })),
        // a task takes you to its day on the calendar, in context
        ...tasks
          .filter((t) => hit(t.title))
          .slice(0, 4)
          .map((t) => ({
            kind: "Task",
            title: t.title,
            meta: t.done ? "done" : `due ${t.due}`,
            go: () => {
              setCalAnchor(t.dueAt || new Date());
              setView("day");
              setPage("calendar");
            },
          })),
        ...plans
          .filter((p) => hit(p.title) || hit(p.location))
          .slice(0, 3)
          .map((p) => ({
            kind: "Poll",
            title: p.title,
            meta: p.status,
            go: () => setPage("polls"),
          })),
        // a place opens its review profile — yours and your friends' takes
        ...knownPlaces
          .filter((p) => hit(p))
          .slice(0, 3)
          .map((p) => ({
            kind: "Place",
            title: p,
            meta: "reviews",
            go: () => {
              setPlaceFocus(p);
              setPage("places");
            },
          })),
      ].slice(0, 9);

  const pick = (r) => {
    r.go();
    setSearchOpen(false);
    setQ("");
  };

  const allRead = notifs.length === 0 || notifs.every((x) => readNotifs.includes(x.id));

  const openNotifTarget = (nf) => {
    setReadNotifs((r) => (r.includes(nf.id) ? r : [...r, nf.id]));
    setNotifOpen(false);
    if (nf.go?.modal) setModal(nf.go.modal);
    else if (nf.go?.page) setPage(nf.go.page);
  };

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
        <div ref={searchRef} style={{ position: "relative" }}>
          <div
            style={{
              ...glass(999), height: 38, width: 220, display: "flex", alignItems: "center",
              gap: 9, padding: "0 15px",
              boxShadow: "0 1px 2px rgba(96,78,54,.06), 0 6px 14px rgba(96,78,54,.08)",
            }}
          >
            <SearchIcon />
            <input
              placeholder="Search tasks, events, polls…"
              value={q}
              onChange={(e) => { setQ(e.target.value); setSearchOpen(true); }}
              onFocus={() => setSearchOpen(true)}
              style={{ flex: 1, minWidth: 0, background: "transparent", border: "none", outline: "none", fontSize: 12.5 }}
            />
          </div>
          {searchOpen && n && (
            <div
              style={{
                ...heavy(18), position: "absolute", top: 46, right: 0, width: 300,
                padding: 8, display: "flex", flexDirection: "column", gap: 1, zIndex: 70,
                animation: "popIn .18s cubic-bezier(.4,0,.2,1)",
              }}
            >
              {results.map((r, i) => (
                <div
                  key={i}
                  className="hov-row"
                  onClick={() => pick(r)}
                  style={{ display: "flex", alignItems: "center", gap: 10, padding: "9px 10px", borderRadius: 12, cursor: "pointer" }}
                >
                  <div style={catChip(CAT_COLORS[r.kind] || "#8A8A8A")}>{r.kind}</div>
                  <span style={{ flex: 1, fontSize: 13, fontWeight: 600, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {r.title}
                  </span>
                  <span style={{ fontSize: 11, color: "#a09889", flex: "none" }}>{r.meta}</span>
                </div>
              ))}
              {results.length === 0 && (
                <div style={{ padding: "12px 10px", fontSize: 12.5, color: "#a09889" }}>
                  Nothing matches “{q.trim()}”.
                </div>
              )}
            </div>
          )}
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
                style={{
                  fontSize: 11, fontWeight: 600,
                  color: allRead ? "#c9c2b4" : "#2B5B84",
                  cursor: allRead ? "default" : "pointer",
                  pointerEvents: allRead ? "none" : "auto",
                }}
                onClick={() => !allRead && setReadNotifs(notifs.map((x) => x.id))}
              >
                Mark all read
              </span>
            </div>
            {notifs.map((nf) => {
              const read = readNotifs.includes(nf.id);
              return (
                <div
                  key={nf.id}
                  className="hov-row"
                  onClick={() => openNotifTarget(nf)}
                  style={{ display: "flex", alignItems: "center", gap: 10, padding: "9px 10px", borderRadius: 12, cursor: "pointer" }}
                >
                  <div
                    style={{
                      ...dot(read ? "transparent" : nf.dot),
                      border: read ? "1.4px solid #cbc3b3" : "none",
                    }}
                  />
                  <span style={{ fontSize: 12.5, lineHeight: 1.4, color: read ? "#a09889" : "#2D2D2D" }}>
                    {nf.pre}<b>{nf.bold}</b>{nf.post}
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
