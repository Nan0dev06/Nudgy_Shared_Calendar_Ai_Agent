import { useEffect, useRef } from "react";
import { useApp } from "../ctx.js";
import {
  glass, gpill, dpill, evBlock, catChip, avatar, popover, dot, kicker, sagePill,
} from "../theme.js";
import { ChevronLeft, ChevronRight, CheckIcon } from "../Icons.jsx";
import {
  DAY_MS, mondayOf, sameDay, fmtDayShort, fmtDayLong,
  fmtMonth, fmtWeekLabel, fmtRange, monthCells,
} from "../dates.js";
import { dayClusters, dayFreeWindows } from "../availability.js";
import { nameFromEmail } from "../people.js";

const CAT_COLORS = { Event: "#D95D39", Meet: "#2A9D8F", Call: "#DCA744", Task: "#DCA744" };
const seg = (on) => ({
  fontSize: 12.5, fontWeight: 600, padding: "6px 16px", borderRadius: 999,
  cursor: "pointer", color: on ? "#F7F2EA" : "#8c8577",
  background: on ? "#2D2D2D" : "transparent", transition: "all .18s", userSelect: "none",
});

// Neutral gray glass — reserved for unresolved multi-person busy blocks
const ovStyle = (hovered) => ({
  borderRadius: 14, padding: "12px 14px", display: "flex", flexDirection: "column",
  gap: 3, cursor: "default", position: "relative",
  background: "rgba(150,142,128,.24)",
  backdropFilter: "blur(10px)", WebkitBackdropFilter: "blur(10px)",
  border: "1px solid rgba(255,253,247,.5)",
  transition: "all .22s cubic-bezier(.34,1.56,.64,1)",
  ...(hovered
    ? { transform: "translateY(-3px) scale(1.02)", boxShadow: "0 16px 34px rgba(96,78,54,.2)", zIndex: 5 }
    : {}),
});

const freeStyle = {
  borderRadius: 16, padding: "13px 14px", display: "flex", flexDirection: "column",
  gap: 7, cursor: "pointer", background: "rgba(255,253,247,.42)",
  border: "1.6px solid #2A9D8F", transition: "all .25s",
};

export default function CalendarPage() {
  const {
    view, setView, calAnchor, setCalAnchor, events, tasks, members, activeGroup,
    avail, setModal, focusId, hoverKey, setHoverKey, doSend,
  } = useApp();

  const today = new Date();
  const isToday = sameDay(calAnchor, today);
  const dayScrollRef = useRef(null);
  const monday = mondayOf(calAnchor);
  const memberByEmail = Object.fromEntries(members.map((m) => [m.email, m]));
  const colorOf = (email) => memberByEmail[email]?.color || "#8A8A8A";
  const nameOf = (email) => memberByEmail[email]?.name || nameFromEmail(email);

  const label =
    view === "day" ? fmtDayLong(calAnchor)
    : view === "week" ? fmtWeekLabel(monday)
    : fmtMonth(calAnchor);

  const shift = (dir) => {
    const d = new Date(calAnchor);
    if (view === "day") d.setDate(d.getDate() + dir);
    else if (view === "week") d.setDate(d.getDate() + 7 * dir);
    else d.setMonth(d.getMonth() + dir);
    setCalAnchor(d);
  };

  // hover-focus from the sidebar: lift my events, fade unrelated ones
  const focusAdj = (emails) => {
    if (!focusId) return {};
    const hit = (emails || []).includes(focusId);
    return hit
      ? { transform: "translateY(-6px)", boxShadow: "0 2px 4px rgba(96,78,54,.08), 0 18px 38px rgba(96,78,54,.2)", zIndex: 2 }
      : { opacity: 0.25, filter: "blur(1.6px)", transform: "translateY(5px)" };
  };

  const tintFor = (e) => colorOf(e.emails && e.emails[0]);
  const openEvent = (e) => setModal({ type: "event", event: e });

  const eventCard = (e, r) => (
    <div
      key={e.id}
      className="hov-lift"
      style={{ ...evBlock(tintFor(e), r), ...focusAdj(e.emails) }}
      onClick={() => openEvent(e)}
    >
      <div style={catChip(CAT_COLORS[e.cat] || "#D95D39")}>{e.cat || "Event"}</div>
      <div style={{ fontSize: 14, fontWeight: 600, lineHeight: 1.3 }}>{e.title}</div>
      <div style={{ display: "flex", alignItems: "center", gap: 7, marginTop: 2, fontSize: 11.5, color: "#a09889" }}>
        <div style={avatar(tintFor(e))} />
        {fmtRange(e.start, e.end)}
      </div>
    </div>
  );

  // Gray overlap cluster block with the "who's busy" hover popover. Rows that
  // came from a NON-anonymous personal event carry a title/place — those show
  // what the person is actually doing; anonymous ones stay just "busy".
  const clusterBlock = (c, key, popRight, extraStyle = {}) => {
    const single = c.count === 1;
    const label = single
      ? `${nameOf(c.emails[0])} busy`
      : `${c.count} busy`;
    return (
      <div key={key} style={{ position: "relative", ...focusAdj(c.emails) }}>
        <div
          style={{ ...ovStyle(hoverKey === key), ...extraStyle }}
          onMouseEnter={() => setHoverKey(key)}
          onMouseLeave={() => setHoverKey(null)}
        >
          <div style={{ fontSize: 12.5, fontWeight: 600, color: "#5c564b" }}>{label}</div>
          <div style={{ fontSize: 11, color: "#7c7568" }}>
            {fmtRange(c.start, c.end)}{single ? "" : " · hover to see"}
          </div>
        </div>
        {hoverKey === key && (
          <div style={popover(popRight)}>
            <div style={{ fontSize: 12, fontWeight: 600 }}>
              Who's busy · {fmtRange(c.start, c.end)}
            </div>
            {c.rows.map((r, i) => (
              <div key={i} style={{ display: "flex", flexDirection: "column", gap: 1 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <div style={dot(colorOf(r.email))} />
                  <span style={{ fontSize: 12, fontWeight: 600, color: colorOf(r.email) }}>
                    {nameOf(r.email)}
                  </span>
                  <span style={{ marginLeft: "auto", fontSize: 11.5, color: "#8c8577" }}>
                    {fmtRange(r.start, r.end)}
                  </span>
                </div>
                {r.title && (
                  <span style={{ fontSize: 11.5, color: "#5c564b", paddingLeft: 18 }}>
                    {r.title}
                    {r.where ? ` · ${r.where}` : ""}
                  </span>
                )}
              </div>
            ))}
            <span style={{ fontSize: 10, color: "#a09889" }}>
              Details show only what people chose to share — the rest stays anonymous.
            </span>
          </div>
        )}
      </div>
    );
  };

  // Task chip — a due item pinned onto its day
  const taskChip = (t) => (
    <div
      key={"tk" + t.id}
      className="hov-lift-sm"
      onClick={() => setModal({ type: "task", task: t })}
      style={{
        display: "flex", alignItems: "center", gap: 7, padding: "7px 11px",
        borderRadius: 11, cursor: "pointer",
        background: "rgba(220,167,68,.14)", border: "1px solid rgba(220,167,68,.35)",
        transition: "all .2s",
      }}
    >
      {t.done ? (
        <CheckIcon size={11} color="#DCA744" />
      ) : (
        <div style={{ ...dot("#DCA744"), width: 7, height: 7 }} />
      )}
      <span style={{ fontSize: 11.5, fontWeight: 600, color: "#8a6f33", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", textDecoration: t.done ? "line-through" : "none" }}>
        {t.title}
      </span>
    </div>
  );

  const freeBlock = (w, key) => (
    <div
      key={key}
      className="hov-lift"
      style={{ ...freeStyle, ...focusAdj(members.map((m) => m.email)) }}
      onClick={() => setModal({ type: "free", slot: w })}
    >
      <div style={{ alignSelf: "flex-start", fontSize: 9, fontWeight: 600, letterSpacing: ".04em", textTransform: "uppercase", padding: "3px 10px", borderRadius: 999, background: "rgba(42,157,143,.14)", color: "#2A9D8F" }}>
        Free
      </div>
      <div style={{ fontSize: 13, fontWeight: 600, color: "#2A9D8F", lineHeight: 1.3 }}>
        Everyone free {fmtRange(w.start, w.end)}
      </div>
    </div>
  );

  // ---------------- header ----------------
  // 1fr | auto | 1fr grid: the view switcher sits in the middle column and
  // stays put no matter how long the date label is or which view is active.
  const header = (
    <div style={{ display: "grid", gridTemplateColumns: "1fr auto 1fr", alignItems: "center", gap: 12 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, minWidth: 0 }}>
        <div className="hov-glass" style={{ ...gpill(true), width: 32, height: 32, padding: 0, borderRadius: 11, justifyContent: "center", flex: "none" }} onClick={() => shift(-1)}>
          <ChevronLeft size={14} />
        </div>
        <div className="hov-glass" style={{ ...gpill(true), width: 32, height: 32, padding: 0, borderRadius: 11, justifyContent: "center", flex: "none" }} onClick={() => shift(1)}>
          <ChevronRight size={14} />
        </div>
        <span style={{ fontSize: 15, fontWeight: 600, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
          {label}
        </span>
        <div
          className="hov-glass"
          style={{ ...gpill(true), flex: "none" }}
          onClick={() => {
            // viewing today's day rail: jump to the current hour instead
            if (view === "day" && isToday) scrollDayTo(today.getHours() - 1);
            else setCalAnchor(new Date());
          }}
        >
          {view === "day" && isToday ? "Now" : "Today"}
        </div>
      </div>
      <div
        style={{
          display: "inline-flex", background: "rgba(255,253,247,.55)",
          border: "1px solid rgba(255,253,247,.7)", borderRadius: 999, padding: 3,
          boxShadow: "0 1px 2px rgba(96,78,54,.06), 0 6px 14px rgba(96,78,54,.08)",
          justifySelf: "center",
        }}
      >
        <span style={seg(view === "day")} onClick={() => setView("day")}>Day</span>
        <span style={seg(view === "week")} onClick={() => setView("week")}>Week</span>
        <span style={seg(view === "month")} onClick={() => setView("month")}>Month</span>
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 10, justifySelf: "end" }}>
        {view === "month" && (
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <span style={kicker}>Free</span>
            <div style={{ width: 52, height: 8, borderRadius: 4, background: "linear-gradient(90deg, rgba(255,253,247,.8), #8A8A8A)" }} />
            <span style={kicker}>Busy</span>
          </div>
        )}
        <div className="hov-lift-sm" style={dpill(true)} onClick={() => setModal({ type: "newEvent" })}>
          + Event
        </div>
        <div className="hov-lift-sm" style={gpill(true)} onClick={() => setModal({ type: "newPoll" })}>
          + Poll
        </div>
      </div>
    </div>
  );

  // ---------------- week ----------------
  const weekView = () => {
    const cols = Array.from({ length: 7 }, (_, i) => {
      const d = new Date(monday.getTime() + i * DAY_MS);
      return {
        date: d,
        evs: events.filter((e) => sameDay(e.start, d)),
        dueTasks: tasks.filter((t) => t.dueAt && sameDay(t.dueAt, d)),
        clusters: dayClusters(avail.members_busy, d),
        free: dayFreeWindows(avail.common_slots, d),
      };
    });
    const anything = cols.some(
      (c) => c.evs.length + c.dueTasks.length + c.clusters.length + c.free.length > 0
    );
    if (!anything)
      return (
        <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center" }}>
          <div style={{ textAlign: "center" }}>
            <div style={{ fontSize: 16, fontWeight: 600 }}>
              A quiet week for {activeGroup?.name || "your group"}
            </div>
            <div style={{ fontSize: 13, color: "#8c8577", marginTop: 5 }}>
              No busy blocks shared yet — invite people or connect calendars.
            </div>
          </div>
        </div>
      );
    return (
      <div style={{ flex: 1, display: "grid", gridTemplateColumns: "repeat(7, 1fr)", gap: 12, minHeight: 0, overflow: "hidden auto", alignItems: "start", paddingBottom: 8 }}>
        {cols.map((col, ci) => {
          // one chronological stack per day: events, busy clusters, free windows
          const items = [
            ...col.evs.map((e) => ({ t: "ev", at: e.start, e })),
            ...col.clusters.map((c, i) => ({ t: "ov", at: c.start, c, i })),
            ...col.free.map((w, i) => ({ t: "free", at: w.start, w, i })),
          ].sort((a, b) => a.at - b.at);
          return (
            <div key={col.date.toISOString()} style={{ display: "flex", flexDirection: "column", gap: 10, minHeight: 0, overflow: "visible" }}>
              <div style={{ ...kicker, color: sameDay(col.date, today) ? "#2A9D8F" : "#a49c8c", padding: "0 4px" }}>
                {fmtDayShort(col.date)}
              </div>
              {col.dueTasks.map(taskChip)}
              {items.map((it, idx) => {
                if (it.t === "ev") return eventCard(it.e, [16, 20, 18][idx % 3]);
                if (it.t === "ov")
                  return clusterBlock(it.c, `w${ci}ov${it.i}`, ci >= 4);
                return freeBlock(it.w, `w${ci}fr${it.i}`);
              })}
            </div>
          );
        })}
      </div>
    );
  };

  // ---------------- day ----------------
  // A full 24h rail inside a scroll container (HOUR_PX per hour); the view
  // auto-scrolls to the morning (or the current hour on today).
  const HOUR_PX = 56;
  const scrollDayTo = (hour) => {
    dayScrollRef.current?.scrollTo({
      top: Math.max(0, hour) * HOUR_PX,
      behavior: "smooth",
    });
  };

  const dayView = () => {
    const frac = (d) =>
      Math.max(0, Math.min(1, (d.getHours() + d.getMinutes() / 60) / 24));
    const dayEvs = events.filter((e) => sameDay(e.start, calAnchor));
    const dayTasks = tasks.filter((t) => t.dueAt && sameDay(t.dueAt, calAnchor));
    const clusters = dayClusters(avail.members_busy, calAnchor);
    const free = dayFreeWindows(avail.common_slots, calAnchor);
    const nowFrac = (today.getHours() + today.getMinutes() / 60) / 24;
    const showNow = isToday;
    const empty = dayEvs.length + clusters.length + free.length === 0;
    const railH = 24 * HOUR_PX;
    return (
      <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: 8, minHeight: 0 }}>
        {dayTasks.length > 0 && (
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", flex: "none" }}>
            <span style={{ ...kicker, alignSelf: "center" }}>Due today</span>
            {dayTasks.map(taskChip)}
          </div>
        )}
        <div ref={dayScrollRef} className="day-scroll" style={{ flex: 1, minHeight: 0, overflow: "hidden auto" }}>
          <div style={{ display: "flex", gap: 12, height: railH, position: "relative" }}>
            <div style={{ width: 44, flex: "none", position: "relative", fontSize: 11, color: "#a09889", textAlign: "right" }}>
              {Array.from({ length: 24 }, (_, h) => (
                <span key={h} style={{ position: "absolute", top: h * HOUR_PX - 7, right: 0 }}>
                  {h === 0 ? "12am" : h < 12 ? `${h}am` : h === 12 ? "12pm" : `${h - 12}pm`}
                </span>
              ))}
            </div>
            <div style={{ flex: 1, position: "relative", borderLeft: "1.5px solid rgba(150,142,128,.3)" }}>
              {Array.from({ length: 24 }, (_, h) => (
                <div key={h} style={{ position: "absolute", top: h * HOUR_PX, left: 0, right: 0, borderTop: "1px solid rgba(150,142,128,.12)" }} />
              ))}
          {dayEvs.map((e) => (
            <div
              key={e.id}
              className="hov-lift"
              onClick={() => openEvent(e)}
              style={{
                ...evBlock(tintFor(e), 14), ...focusAdj(e.emails),
                position: "absolute", left: 14, right: "34%",
                top: `${frac(e.start) * 100}%`,
                height: `${Math.max(0.05, frac(e.end) - frac(e.start)) * 100}%`,
                justifyContent: "center", gap: 2, padding: "8px 14px",
              }}
            >
              <span style={{ fontSize: 13, fontWeight: 600 }}>{e.title}</span>
              <span style={{ fontSize: 11, color: "#8c8577" }}>{fmtRange(e.start, e.end)}</span>
            </div>
          ))}
          {clusters.map((c, i) =>
            clusterBlock(c, `dov${i}`, false, {
              position: "absolute", left: 14, right: 14,
              top: `${frac(c.start) * 100}%`,
              height: `${Math.max(0.06, frac(c.end) - frac(c.start)) * 100}%`,
              justifyContent: "center",
            })
          )}
          {free.map((w, i) => (
            <div
              key={"dfr" + i}
              style={{
                position: "absolute", left: 14, right: 14,
                top: `${frac(w.start) * 100}%`,
                height: `${Math.max(0.08, frac(w.end) - frac(w.start)) * 100}%`,
                border: "2px solid #2A9D8F", borderRadius: 16,
                background: "rgba(42,157,143,.07)",
                display: "flex", flexDirection: "column", justifyContent: "center",
                gap: 8, padding: "0 16px",
              }}
            >
              <div style={{ fontSize: 13.5, fontWeight: 600, color: "#2A9D8F" }}>
                Everyone free · {fmtRange(w.start, w.end)}
              </div>
              <div style={{ display: "flex", gap: 8 }}>
                <div
                  className="hov-lift-sm"
                  style={sagePill(true)}
                  onClick={() => setModal({ type: "free", slot: w })}
                >
                  Book it
                </div>
                <div
                  className="hov-sage"
                  style={{ ...gpill(true), border: "1.4px solid #2A9D8F", color: "#2A9D8F", background: "rgba(255,253,247,.4)" }}
                  onClick={() => setModal({ type: "newPoll", prefill: { start: w.start, end: w.end } })}
                >
                  Start poll
                </div>
              </div>
            </div>
          ))}
          {empty && (
            <div style={{ position: "absolute", inset: 0, display: "flex", alignItems: "center", justifyContent: "center", fontSize: 13, color: "#8c8577" }}>
              Nothing on this day.
            </div>
          )}
          {showNow && (
            <>
              <div style={{ position: "absolute", top: `${nowFrac * 100}%`, left: 0, right: 0, borderTop: "1.6px dashed #D95D39", zIndex: 3 }} />
              <div style={{ position: "absolute", top: `${nowFrac * 100}%`, left: -4, width: 9, height: 9, marginTop: -4.5, borderRadius: "50%", background: "#D95D39", zIndex: 3 }} />
            </>
          )}
            </div>
          </div>
        </div>
      </div>
    );
  };

  // land the day rail somewhere useful: the current hour on today, 8am otherwise
  useEffect(() => {
    if (view !== "day" || !dayScrollRef.current) return;
    const hour = sameDay(calAnchor, new Date()) ? new Date().getHours() - 1 : 8;
    dayScrollRef.current.scrollTop = Math.max(0, hour) * HOUR_PX;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [view, calAnchor]);

  // ---------------- month ----------------
  const monthView = () => {
    const cells = monthCells(calAnchor);
    return (
      <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: 8, minHeight: 0 }}>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(7, 1fr)", gap: 8, fontSize: 10.5, fontWeight: 600, letterSpacing: ".06em", color: "#a49c8c", textAlign: "center" }}>
          <span>M</span><span>T</span><span>W</span><span>T</span><span>F</span><span>S</span><span>S</span>
        </div>
        <div style={{ flex: 1, display: "grid", gridTemplateColumns: "repeat(7, 1fr)", gridTemplateRows: "repeat(5, 1fr)", gap: 8, minHeight: 0 }}>
          {cells.map(({ date, dim }, i) => {
            const key = "mc" + i;
            const dayEvs = events.filter((e) => sameDay(e.start, date));
            const clusters = dim ? [] : dayClusters(avail.members_busy, date);
            const busyCount = clusters.reduce((a, c) => a + c.count, 0);
            const isToday = sameDay(date, today);
            const load = Math.min(0.55, busyCount * 0.14 + (dayEvs.length > 1 ? 0.2 : 0));
            const accent = dayEvs.length >= 1 ? tintFor(dayEvs[0]) : null;
            let bg = dim ? "rgba(255,253,247,.28)" : "rgba(255,253,247,.4)";
            if (!dim && load > 0)
              bg = `color-mix(in srgb, #8A8A8A ${Math.round(load * 100)}%, rgba(255,253,247,.6))`;
            if (!dim && accent)
              bg = `color-mix(in srgb, ${accent} 18%, rgba(255,253,247,.6))`;
            const hov = hoverKey === key;
            const pr = i % 7 >= 5, pu = i >= 21;
            const popText = dayEvs.length
              ? dayEvs.map((e) => e.title).join(" · ")
              : busyCount
                ? `${busyCount} busy block${busyCount > 1 ? "s" : ""} — ask the orb for a window`
                : "Nothing planned — ask the orb to find a window";
            return (
              <div
                key={key}
                onMouseEnter={dim ? undefined : () => setHoverKey(key)}
                onMouseLeave={dim ? undefined : () => setHoverKey(null)}
                onClick={dim ? undefined : () => { setCalAnchor(date); setView("day"); }}
                style={{
                  borderRadius: 12, padding: 10, display: "flex", flexDirection: "column",
                  gap: 5, position: "relative", cursor: dim ? "default" : "pointer",
                  background: bg, transition: "all .18s",
                  ...(isToday && !dim ? { outline: "2px solid #2A9D8F", outlineOffset: -2, background: "rgba(255,253,247,.6)" } : {}),
                  ...(hov ? { transform: "scale(1.03)", boxShadow: "0 12px 28px rgba(96,78,54,.18)", zIndex: 5 } : {}),
                }}
              >
                <span style={{ fontSize: 12.5, fontWeight: 600, color: dim ? "#c9c2b4" : isToday ? "#2A9D8F" : "#2D2D2D" }}>
                  {date.getDate()}
                </span>
                {(dayEvs.length > 0 || busyCount > 0) && !dim && (
                  <div style={{ height: 5, borderRadius: 3, background: accent || "#8A8A8A", width: `${Math.min(90, 30 + (dayEvs.length + busyCount) * 18)}%`, marginTop: "auto" }} />
                )}
                {hov && !dim && (
                  <div style={popover(pr, pu)}>
                    <div style={{ fontSize: 12, fontWeight: 600 }}>
                      {fmtDayLong(date)}{isToday ? " · Today" : ""}
                    </div>
                    <div style={{ fontSize: 11.5, lineHeight: 1.45, color: "#8c8577" }}>{popText}</div>
                    <div
                      className="hov-lift-sm"
                      style={{ ...sagePill(true), alignSelf: "flex-start", padding: "5px 12px", fontSize: 11.5 }}
                      onClick={(ev) => {
                        ev.stopPropagation();
                        setHoverKey(null);
                        doSend(`Find a time on ${fmtDayLong(date)} when everyone's free`);
                      }}
                    >
                      Find a time
                    </div>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </div>
    );
  };

  return (
    <div style={{ flex: 1, minHeight: 0, display: "flex", flexDirection: "column", gap: 14, animation: "fadeUp .35s cubic-bezier(.4,0,.2,1)" }}>
      {header}
      {view === "week" && weekView()}
      {view === "day" && dayView()}
      {view === "month" && monthView()}
    </div>
  );
}
