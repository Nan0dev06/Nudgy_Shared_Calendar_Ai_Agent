// Shared glass form fields: place picker with live search, date + time pickers
// that match the liquid-glass language (no bare native widgets), star ratings.
import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { fieldStyle, fieldLabel } from "../theme.js";
import { PinIcon, ChevronLeft, ChevronRight, StarIcon } from "../Icons.jsx";
import { LOCAL_PLACES, matchPlaces, searchOsm } from "../places.js";
import { fmtDayLong, monthCells, sameDay, fmtMonth } from "../dates.js";

const drop = {
  position: "absolute",
  top: "calc(100% + 8px)",
  left: 0,
  right: 0,
  zIndex: 90,
  display: "flex",
  flexDirection: "column",
  gap: 2,
  padding: 8,
  background: "rgba(255,253,247,.82)",
  backdropFilter: "blur(30px)",
  WebkitBackdropFilter: "blur(30px)",
  border: "1px solid rgba(255,255,255,.8)",
  borderRadius: 16,
  boxShadow: "0 20px 44px rgba(45,45,45,.2)",
  animation: "popIn .18s cubic-bezier(.4,0,.2,1)",
  maxHeight: 240,
  overflow: "auto",
};

// Dropdowns inside a modal card would be clipped by its overflow (the poll
// composer's slide track needs overflow:hidden), and position:fixed alone
// doesn't save them: the card's backdrop-filter and the track's transform
// both create CSS containing blocks, so "fixed" would still be relative to
// the CARD. The panels therefore render through a PORTAL on document.body
// (which has no transform/filter), pinned to viewport coordinates measured
// from the field — flipping above it when there's no room below.
function useFixedDrop(boxRef, panelRef, open, panelH = 260, minW = 0) {
  const [pos, setPos] = useState(null);
  useEffect(() => {
    if (!open || !boxRef.current) {
      setPos(null);
      return;
    }
    const place = () => {
      if (!boxRef.current) return;
      const r = boxRef.current.getBoundingClientRect();
      // once mounted, use the panel's REAL height so an above-flip lands
      // flush against the field instead of leaving an estimate-sized gap
      const h = panelRef?.current?.offsetHeight || panelH;
      const below = window.innerHeight - r.bottom;
      const width = Math.max(r.width, minW);
      setPos({
        position: "fixed",
        left: Math.max(12, Math.min(r.left, window.innerWidth - width - 12)),
        width,
        right: "auto",
        ...(below > h + 16 || r.top < h + 16
          ? { top: r.bottom + 8 }
          : { top: r.top - h - 8 }),
        zIndex: 200,
      });
    };
    place();
    // re-place after paint, when the portaled panel exists and has a height
    const raf = requestAnimationFrame(place);
    window.addEventListener("resize", place);
    window.addEventListener("scroll", place, true);
    return () => {
      cancelAnimationFrame(raf);
      window.removeEventListener("resize", place);
      window.removeEventListener("scroll", place, true);
    };
  }, [open, boxRef, panelRef, panelH, minW]);
  return pos;
}

// Portal wrapper: nothing renders until the viewport position is measured, so
// the panel never flashes at a wrong spot for a frame.
function DropPanel({ pos, panelRef, style, children }) {
  if (!pos) return null;
  return createPortal(
    <div ref={panelRef} style={{ ...style, ...pos }}>
      {children}
    </div>,
    document.body
  );
}

const RECENTS_KEY = "ov.placeRecents";
const getRecents = () => {
  try {
    return JSON.parse(localStorage.getItem(RECENTS_KEY)) || [];
  } catch {
    return [];
  }
};
export const rememberPlace = (name) => {
  if (!name) return;
  try {
    const r = [name, ...getRecents().filter((x) => x !== name)].slice(0, 12);
    localStorage.setItem(RECENTS_KEY, JSON.stringify(r));
  } catch {
    /* fine */
  }
};

// value is a plain string (the place name) so it stays compatible with the
// backend's `location` field; suggestions come from recents + curated + OSM.
export function PlacePicker({ value, onChange, placeholder = "Search a place…", reviewedPlaces = [] }) {
  const [open, setOpen] = useState(false);
  const [osmRows, setOsmRows] = useState([]);
  const boxRef = useRef(null);
  const panelRef = useRef(null);
  const abortRef = useRef(null);
  const dropPos = useFixedDrop(boxRef, panelRef, open, 250);

  useEffect(() => {
    const onDoc = (e) => {
      if (
        boxRef.current && !boxRef.current.contains(e.target) &&
        !(panelRef.current && panelRef.current.contains(e.target))
      )
        setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, []);

  useEffect(() => {
    if (!open) return;
    abortRef.current?.abort();
    const ctl = new AbortController();
    abortRef.current = ctl;
    const t = setTimeout(async () => {
      setOsmRows(await searchOsm(value, ctl.signal));
    }, 350);
    return () => {
      clearTimeout(t);
      ctl.abort();
    };
  }, [value, open]);

  const mine = [...new Set([...reviewedPlaces, ...getRecents()])];
  const mineHits = matchPlaces(mine, value, 3).map((name) => ({ name, area: "recent", mine: true }));
  const localHits = matchPlaces(LOCAL_PLACES, value, 4).filter(
    (p) => !mineHits.some((m) => m.name === p.name)
  );
  const osmHits = osmRows.filter(
    (p) => !mineHits.some((m) => m.name === p.name) && !localHits.some((l) => l.name === p.name)
  );
  const rows = [...mineHits, ...localHits, ...osmHits];

  const pick = (name) => {
    onChange(name);
    rememberPlace(name);
    setOpen(false);
  };

  return (
    <div ref={boxRef} style={{ position: "relative", display: "flex" }}>
      <div style={{ position: "absolute", left: 13, top: "50%", transform: "translateY(-50%)", color: "#a49c8c", pointerEvents: "none", display: "flex" }}>
        <PinIcon size={13} />
      </div>
      <input
        placeholder={placeholder}
        value={value || ""}
        onChange={(e) => {
          onChange(e.target.value);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        style={{ ...fieldStyle, paddingLeft: 34 }}
      />
      {open && (rows.length > 0 || (value || "").trim()) && (
        <DropPanel pos={dropPos} panelRef={panelRef} style={drop}>
          {rows.map((p, i) => (
            <div
              key={p.name + i}
              className="hov-row"
              onMouseDown={() => pick(p.name)}
              style={{ display: "flex", alignItems: "center", gap: 9, padding: "8px 10px", borderRadius: 11, cursor: "pointer" }}
            >
              <PinIcon size={12} />
              <span style={{ fontSize: 13, fontWeight: 600, flex: "none" }}>{p.name}</span>
              <span style={{ fontSize: 11, color: "#a09889", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {p.mine ? "· you've been here" : p.area ? `· ${p.area}` : ""}
              </span>
            </div>
          ))}
          {(value || "").trim() &&
            !rows.some((p) => p.name.toLowerCase() === value.trim().toLowerCase()) && (
              <div
                className="hov-row"
                onMouseDown={() => pick(value.trim())}
                style={{ padding: "8px 10px", borderRadius: 11, cursor: "pointer", fontSize: 12.5, color: "#2B5B84", fontWeight: 600 }}
              >
                Use “{value.trim()}”
              </div>
            )}
        </DropPanel>
      )}
    </div>
  );
}

// ---- glass date picker ------------------------------------------------------
export function GlassDatePicker({ value, onChange, placeholder = "Pick a day" }) {
  const [open, setOpen] = useState(false);
  const [anchor, setAnchor] = useState(() => (value ? new Date(value) : new Date()));
  const boxRef = useRef(null);
  const panelRef = useRef(null);
  const dropPos = useFixedDrop(boxRef, panelRef, open, 300, 258);
  useEffect(() => {
    const onDoc = (e) => {
      if (
        boxRef.current && !boxRef.current.contains(e.target) &&
        !(panelRef.current && panelRef.current.contains(e.target))
      )
        setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, []);

  const sel = value ? new Date(value) : null;
  const today = new Date();
  const shiftMonth = (dir) => {
    const d = new Date(anchor);
    d.setMonth(d.getMonth() + dir);
    setAnchor(d);
  };

  return (
    <div ref={boxRef} style={{ position: "relative", display: "flex", flex: 1 }}>
      <div
        onClick={() => setOpen((v) => !v)}
        style={{ ...fieldStyle, cursor: "pointer", display: "flex", alignItems: "center", color: sel ? "#2D2D2D" : "#a49c8c" }}
      >
        {sel ? fmtDayLong(sel) : placeholder}
      </div>
      {open && (
        <DropPanel pos={dropPos} panelRef={panelRef} style={{ ...drop, maxHeight: "none", padding: 12 }}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "0 2px 6px" }}>
            <div className="hov-icon" style={{ width: 24, height: 24, borderRadius: 8, display: "flex", alignItems: "center", justifyContent: "center", cursor: "pointer", color: "#8c8577" }} onClick={() => shiftMonth(-1)}>
              <ChevronLeft size={13} />
            </div>
            <span style={{ fontSize: 12.5, fontWeight: 600 }}>{fmtMonth(anchor)}</span>
            <div className="hov-icon" style={{ width: 24, height: 24, borderRadius: 8, display: "flex", alignItems: "center", justifyContent: "center", cursor: "pointer", color: "#8c8577" }} onClick={() => shiftMonth(1)}>
              <ChevronRight size={13} />
            </div>
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(7,1fr)", gap: 2, fontSize: 9.5, fontWeight: 600, color: "#a49c8c", textAlign: "center", padding: "0 0 3px" }}>
            <span>M</span><span>T</span><span>W</span><span>T</span><span>F</span><span>S</span><span>S</span>
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(7,1fr)", gap: 2 }}>
            {monthCells(anchor).map(({ date, dim }, i) => {
              const isSel = sel && sameDay(date, sel);
              const isToday = sameDay(date, today);
              return (
                <div
                  key={i}
                  onClick={() => {
                    onChange(
                      `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`
                    );
                    setOpen(false);
                  }}
                  style={{
                    height: 28, display: "flex", alignItems: "center", justifyContent: "center",
                    borderRadius: 9, fontSize: 12, cursor: "pointer",
                    fontWeight: isSel || isToday ? 600 : 500,
                    color: dim ? "#c9c2b4" : isSel ? "#F7F2EA" : isToday ? "#2A9D8F" : "#2D2D2D",
                    background: isSel ? "#2D2D2D" : "transparent",
                    transition: "all .15s",
                  }}
                  onMouseEnter={(e) => { if (!isSel) e.currentTarget.style.background = "rgba(255,253,247,.8)"; }}
                  onMouseLeave={(e) => { if (!isSel) e.currentTarget.style.background = "transparent"; }}
                >
                  {date.getDate()}
                </div>
              );
            })}
          </div>
        </DropPanel>
      )}
    </div>
  );
}

// ---- glass time picker ------------------------------------------------------
const TIMES = [];
for (let h = 0; h < 24; h++) for (const m of [0, 30]) TIMES.push(`${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}`);

const timeLabel = (t) => {
  const [h, m] = t.split(":").map(Number);
  const ampm = h >= 12 ? "PM" : "AM";
  const hh = h % 12 || 12;
  return `${hh}:${String(m).padStart(2, "0")} ${ampm}`;
};

// 5 visible rows (30-min pitch), the rest reachable by scrolling
const TIME_ROW = 34; // 32px row + 2px gap
const TIME_PANEL_H = 5 * TIME_ROW + 14;

export function GlassTimePicker({ value, onChange, placeholder = "Time" }) {
  const [open, setOpen] = useState(false);
  const boxRef = useRef(null);
  const listRef = useRef(null);
  const dropPos = useFixedDrop(boxRef, listRef, open, TIME_PANEL_H, 116);
  useEffect(() => {
    const onDoc = (e) => {
      if (
        boxRef.current && !boxRef.current.contains(e.target) &&
        !(listRef.current && listRef.current.contains(e.target))
      )
        setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, []);
  useEffect(() => {
    // the panel only mounts once dropPos is measured, so wait for it too
    if (open && dropPos && listRef.current) {
      // land on the picked time, or a sane default (9:00) — 2 rows of context above
      const idx = TIMES.indexOf(value || "09:00");
      if (idx > 0) listRef.current.scrollTop = idx * TIME_ROW - 2 * TIME_ROW;
    }
  }, [open, value, dropPos]);

  return (
    <div ref={boxRef} style={{ position: "relative", display: "flex", flex: 1 }}>
      <div
        onClick={() => setOpen((v) => !v)}
        style={{ ...fieldStyle, cursor: "pointer", display: "flex", alignItems: "center", justifyContent: "center", color: value ? "#2D2D2D" : "#a49c8c", whiteSpace: "nowrap" }}
      >
        {value ? timeLabel(value) : placeholder}
      </div>
      {open && (
        <DropPanel pos={dropPos} panelRef={listRef} style={{ ...drop, maxHeight: TIME_PANEL_H, minWidth: 116 }}>
          {TIMES.map((t) => (
            <div
              key={t}
              className="hov-row"
              onClick={() => { onChange(t); setOpen(false); }}
              style={{
                padding: "6px 12px", borderRadius: 9, cursor: "pointer", fontSize: 12.5,
                textAlign: "center", flex: "none", height: 32, display: "flex",
                alignItems: "center", justifyContent: "center",
                fontWeight: value === t ? 600 : 500,
                background: value === t ? "rgba(42,157,143,.14)" : undefined,
                color: value === t ? "#2A9D8F" : "#2D2D2D",
              }}
            >
              {timeLabel(t)}
            </div>
          ))}
        </DropPanel>
      )}
    </div>
  );
}

// ---- stars ------------------------------------------------------------------
export function StarRow({ value, onChange, size = 22 }) {
  const [hover, setHover] = useState(0);
  const shown = hover || value || 0;
  return (
    <div style={{ display: "flex", gap: 4 }} onMouseLeave={() => setHover(0)}>
      {[1, 2, 3, 4, 5].map((n) => (
        <div
          key={n}
          onMouseEnter={() => onChange && setHover(n)}
          onClick={() => onChange && onChange(n)}
          style={{
            cursor: onChange ? "pointer" : "default",
            color: n <= shown ? "#DCA744" : "rgba(150,142,128,.4)",
            display: "flex",
            animation: onChange && value === n ? "starPop .3s ease" : undefined,
          }}
        >
          <StarIcon size={size} filled={n <= shown} />
        </div>
      ))}
    </div>
  );
}

export { fieldLabel };
