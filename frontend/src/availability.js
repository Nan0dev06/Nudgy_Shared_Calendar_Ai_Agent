// Turn the availability endpoint's per-member busy ranges into the design's
// calendar blocks: gray "N busy" overlap clusters (with who/when rows) and
// sage "everyone free" windows.

import { addDays, startOfDay } from "./dates.js";

// Busy intervals of every member that touch `day`, clamped to that day.
function dayIntervals(membersBusy, day) {
  const d0 = startOfDay(day).getTime();
  const d1 = addDays(day, 1).getTime();
  const out = [];
  for (const m of membersBusy || []) {
    for (const b of m.busy || []) {
      const s = new Date(b.start_iso).getTime();
      const e = new Date(b.end_iso).getTime();
      if (e <= d0 || s >= d1) continue;
      out.push({
        email: m.email,
        start: new Date(Math.max(s, d0)),
        end: new Date(Math.min(e, d1)),
        // Present only where the server decided the VIEWER is entitled to the
        // detail: a non-anonymous personal event, or their own external event
        // from a calendar they switched titles on (docs/inbound-sync.md).
        // Groupmates' rows never carry these — free/busy has no titles, and the
        // mirror's are only ever read for their owner. Nothing here needs to
        // enforce that; it is enforced before the payload is built.
        title: b.title || null,
        where: b.where || null,
        calendar: b.calendar || null,
      });
    }
  }
  return out.sort((a, b) => a.start - b.start);
}

// Merge overlapping/adjacent busy intervals (across members) into clusters:
// each cluster = one calendar block. rows keep per-member ranges for the
// hover popover; count = distinct members busy in the cluster.
export function dayClusters(membersBusy, day) {
  const ivs = dayIntervals(membersBusy, day);
  const clusters = [];
  for (const iv of ivs) {
    const cur = clusters[clusters.length - 1];
    if (cur && iv.start <= cur.end) {
      cur.end = new Date(Math.max(cur.end, iv.end));
      cur.rows.push(iv);
    } else {
      clusters.push({ start: iv.start, end: new Date(iv.end), rows: [iv] });
    }
  }
  for (const c of clusters) {
    c.emails = [...new Set(c.rows.map((r) => r.email))];
    c.count = c.emails.length;
  }
  return clusters;
}

// Common free windows (from the endpoint) that fall on `day`.
export function dayFreeWindows(commonSlots, day) {
  const d0 = startOfDay(day).getTime();
  const d1 = addDays(day, 1).getTime();
  return (commonSlots || [])
    .map((s) => ({ start: new Date(s.start_iso), end: new Date(s.end_iso) }))
    .filter((s) => s.end.getTime() > d0 && s.start.getTime() < d1)
    .map((s) => ({
      start: new Date(Math.max(s.start.getTime(), d0)),
      end: new Date(Math.min(s.end.getTime(), d1)),
    }));
}
