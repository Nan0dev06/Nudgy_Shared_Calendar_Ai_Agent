import { useEffect, useState } from "react";
import { useApp } from "../ctx.js";
import {
  heavy, gpill, dpill, catChip, avatar, agentBox, fieldStyle, fieldRead,
  fieldLabel, toggleStyle, knobStyle,
} from "../theme.js";
import { ClockIcon, PinIcon } from "../Icons.jsx";
import { fmtDayLong, fmtRange } from "../dates.js";

const CAT_COLORS = { Event: "#D95D39", Meet: "#2A9D8F", Call: "#DCA744", Task: "#DCA744" };

const scrim = {
  position: "fixed", inset: 0, background: "rgba(45,41,38,.24)",
  backdropFilter: "blur(9px)", WebkitBackdropFilter: "blur(9px)",
  display: "flex", alignItems: "center", justifyContent: "center",
  zIndex: 80, animation: "fadeUp .2s",
};

const card = {
  ...heavy(26), width: 400, padding: 22, display: "flex", flexDirection: "column",
  gap: 14, animation: "popIn .22s cubic-bezier(.34,1.56,.64,1)",
};

export default function Modals() {
  const { modal, setModal } = useApp();
  useEffect(() => {
    const onKey = (e) => e.key === "Escape" && setModal(null);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [setModal]);
  if (!modal) return null;
  return (
    <div style={scrim} onClick={() => setModal(null)}>
      {modal.type === "event" && <EventModal />}
      {modal.type === "task" && <TaskModal />}
      {modal.type === "newEvent" && <NewEventModal />}
      {modal.type === "newTask" && <NewTaskModal />}
      {modal.type === "newPoll" && <NewPollModal />}
      {modal.type === "free" && <FreeModal />}
      {modal.type === "invite" && <InviteModal />}
      {modal.type === "newGroup" && <NewGroupModal />}
    </div>
  );
}

const stop = (e) => e.stopPropagation();

function EventModal() {
  const { modal, rsvp, setRsvp, members } = useApp();
  const e = modal.event;
  const my = rsvp[e.id];
  const rsvpBtn = (v, label) => (
    <div
      style={{
        flex: 1, justifyContent: "center",
        ...(my === v ? dpill(true) : gpill(true)),
        ...(v === "cant" && my !== v ? { color: "#a09889" } : {}),
      }}
      onClick={() => setRsvp((r) => ({ ...r, [e.id]: v }))}
    >
      {label}
    </div>
  );
  const avs = members.filter((m) => (e.emails || []).includes(m.email));
  return (
    <div style={{ ...card, padding: 0, overflow: "hidden" }} onClick={stop}>
      <div style={{ height: 92, background: "linear-gradient(120deg, #F3C9A8, #A9CBB6, #C9B6D4)" }} />
      <div style={{ padding: "18px 22px 22px", display: "flex", flexDirection: "column", gap: 14 }}>
        <div>
          <span style={catChip(CAT_COLORS[e.cat] || "#D95D39")}>{e.cat || "Event"}</span>
          <div style={{ fontSize: 21, fontWeight: 600, marginTop: 8 }}>{e.title}</div>
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 9 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10, fontSize: 13, color: "#8c8577" }}>
            <ClockIcon />
            {fmtDayLong(e.start)} · {fmtRange(e.start, e.end)}
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 10, fontSize: 13, color: "#8c8577" }}>
            <PinIcon />
            {e.where || "—"}
          </div>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <div style={{ display: "flex" }}>
            {avs.map((m, i) => (
              <div key={m.email} title={m.name} style={{ ...avatar(m.color, 24), marginRight: i < avs.length - 1 ? -7 : 0 }} />
            ))}
          </div>
          <span style={{ fontSize: 12, color: "#a09889" }}>
            {avs.length ? avs.map((m) => m.name).join(", ") : "No invitees"}
          </span>
        </div>
        {e.agent && (
          <div style={agentBox}>
            <span style={{ fontSize: 12, fontWeight: 600, color: "#2A9D8F" }}>Chosen by the agent</span>
            <span style={{ fontSize: 12.5, lineHeight: 1.5, color: "#5c564b" }}>{e.agent}</span>
          </div>
        )}
        {e.link && (
          <a href={e.link} target="_blank" rel="noreferrer" style={{ ...gpill(true), textDecoration: "none", alignSelf: "flex-start" }}>
            Open in Google Calendar
          </a>
        )}
        <div style={{ display: "flex", gap: 9 }}>
          {rsvpBtn("going", "Going")}
          {rsvpBtn("maybe", "Maybe")}
          {rsvpBtn("cant", "Can't go")}
        </div>
      </div>
    </div>
  );
}

function TaskModal() {
  const { modal, setModal, setTaskDone, removeEvent, pushActivity } = useApp();
  const t = modal.task;
  return (
    <div style={card} onClick={stop}>
      <div>
        <span style={catChip("#DCA744")}>Task</span>
        <div style={{ fontSize: 21, fontWeight: 600, marginTop: 8 }}>{t.title}</div>
      </div>
      <div style={{ fontSize: 13, color: "#8c8577" }}>Due {t.due || "anytime"}</div>
      {t.where && (
        <div style={{ display: "flex", alignItems: "center", gap: 10, fontSize: 13, color: "#8c8577" }}>
          <PinIcon /> {t.where}
        </div>
      )}
      <div style={{ display: "flex", gap: 9 }}>
        <div
          className="hov-lift-sm"
          style={{ ...dpill(true), flex: 1, justifyContent: "center" }}
          onClick={async () => {
            await setTaskDone(t.id, true);
            pushActivity({ dot: "#DCA744", pre: "You completed ", bold: t.title, post: "" });
            setModal(null);
          }}
        >
          Mark done
        </div>
        <div
          className="hov-glass"
          style={{ ...gpill(true), flex: 1, justifyContent: "center", color: "#b08a80" }}
          onClick={async () => {
            await removeEvent(t.id);
            setModal(null);
          }}
        >
          Remove
        </div>
      </div>
    </div>
  );
}

// Invitee picker by NAME (not just avatar circles) — shows name + email.
function InviteePicker({ selected, setSelected }) {
  const { members, me } = useApp();
  const others = members.filter((m) => m.email !== me?.email);
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      {others.map((m) => {
        const on = selected.includes(m.email);
        return (
          <div
            key={m.email}
            onClick={() =>
              setSelected(on ? selected.filter((x) => x !== m.email) : [...selected, m.email])
            }
            style={{
              display: "flex", alignItems: "center", gap: 10, padding: "8px 10px",
              borderRadius: 12, cursor: "pointer", transition: "all .18s",
              background: on ? "rgba(42,157,143,.12)" : "rgba(255,253,247,.4)",
              border: on ? "1.4px solid #2A9D8F" : "1px solid rgba(255,255,255,.6)",
            }}
          >
            <div style={{ ...avatar(m.color, 22), display: "flex", alignItems: "center", justifyContent: "center", color: "#fff", fontSize: 9, fontWeight: 600 }}>
              {m.initials}
            </div>
            <div style={{ display: "flex", flexDirection: "column", minWidth: 0, flex: 1 }}>
              <span style={{ fontSize: 12.5, fontWeight: 600 }}>{m.name}</span>
              <span style={{ fontSize: 10.5, color: "#a09889", overflow: "hidden", textOverflow: "ellipsis" }}>{m.email}</span>
            </div>
            {on && <span style={{ fontSize: 11, color: "#2A9D8F", fontWeight: 600 }}>Invited</span>}
          </div>
        );
      })}
      {others.length === 0 && (
        <div style={{ fontSize: 12, color: "#a09889" }}>No one else in this group yet.</div>
      )}
    </div>
  );
}

function catPills(list, cur, setCur) {
  return (
    <div style={{ display: "flex", gap: 8 }}>
      {list.map((l) => (
        <div key={l} style={cur === l ? dpill(true) : gpill(true)} onClick={() => setCur(l)}>
          {l}
        </div>
      ))}
    </div>
  );
}

function NewEventModal() {
  const { setModal, createEvent, members, me, setPage, setView } = useApp();
  const [title, setTitle] = useState("");
  const [date, setDate] = useState(() => new Date().toISOString().slice(0, 10));
  const [start, setStart] = useState("19:00");
  const [end, setEnd] = useState("21:00");
  const [where, setWhere] = useState("");
  const [cat, setCat] = useState("Event");
  const [inv, setInv] = useState(members.filter((m) => m.email !== me?.email).map((m) => m.email));
  const [sync, setSync] = useState(!!me?.calendar_connected);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  const create = async () => {
    if (busy) return;
    setBusy(true);
    setErr("");
    try {
      await createEvent({
        kind: "event",
        title: title.trim() || "New event",
        category: cat,
        location: where.trim() || null,
        start_iso: new Date(`${date}T${start}`).toISOString(),
        end_iso: new Date(`${date}T${end}`).toISOString(),
        invite_emails: inv,
        sync_google: sync,
      });
      setModal(null);
      setPage("calendar");
      setView("week");
    } catch (e) {
      setErr(e.message || "That didn't work — try again.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div style={{ ...card, maxHeight: "88vh", overflow: "auto" }} onClick={stop}>
      <div style={{ fontSize: 19, fontWeight: 600 }}>New event</div>
      <input placeholder="Title — e.g. Dinner with the crew" value={title} onChange={(e) => setTitle(e.target.value)} style={fieldStyle} autoFocus />
      <div style={{ display: "flex", gap: 9 }}>
        <input type="date" value={date} onChange={(e) => setDate(e.target.value)} style={{ ...fieldStyle, flex: 1.4 }} />
        <input type="time" value={start} onChange={(e) => setStart(e.target.value)} style={fieldStyle} />
        <input type="time" value={end} onChange={(e) => setEnd(e.target.value)} style={fieldStyle} />
      </div>
      <input placeholder="Add a location — optional" value={where} onChange={(e) => setWhere(e.target.value)} style={fieldStyle} />
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        <span style={fieldLabel}>Invite</span>
        <InviteePicker selected={inv} setSelected={setInv} />
      </div>
      {catPills(["Meet", "Event", "Call"], cat, setCat)}
      <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
        <div style={{ flex: 1 }}>
          <div style={{ fontSize: 13.5, fontWeight: 600 }}>Sync with Google Calendar</div>
          <div style={{ fontSize: 11.5, color: "#8c8577", marginTop: 2 }}>
            {me?.calendar_connected
              ? "Creates the event on Google Calendar and invites everyone — their calendars update automatically."
              : "Connect your Google Calendar to sync events."}
          </div>
        </div>
        <div style={toggleStyle(sync)} onClick={() => me?.calendar_connected && setSync((v) => !v)}>
          <div style={knobStyle(sync)} />
        </div>
      </div>
      {err && <div style={{ fontSize: 12, color: "#D95D39" }}>{err}</div>}
      <div className="hov-lift-sm" style={{ ...dpill(false), justifyContent: "center", opacity: busy ? 0.6 : 1 }} onClick={create}>
        {busy ? "Creating…" : "Create event"}
      </div>
    </div>
  );
}

function NewTaskModal() {
  const { setModal, createEvent } = useApp();
  const [title, setTitle] = useState("");
  const [cat, setCat] = useState("Task");
  const [due, setDue] = useState("");
  const [where, setWhere] = useState("");
  const [busy, setBusy] = useState(false);

  const create = async () => {
    if (busy) return;
    setBusy(true);
    try {
      await createEvent({
        kind: "task",
        title: title.trim() || "New task",
        category: cat,
        location: where.trim() || null,
        start_iso: due ? new Date(`${due}T12:00`).toISOString() : null,
      });
      setModal(null);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div style={card} onClick={stop}>
      <div style={{ fontSize: 19, fontWeight: 600 }}>New task</div>
      <input placeholder="e.g. Pick up the cake for Aya's party" value={title} onChange={(e) => setTitle(e.target.value)} style={fieldStyle} autoFocus />
      {catPills(["Task", "Errand", "Prep"], cat, setCat)}
      <input type="date" value={due} onChange={(e) => setDue(e.target.value)} style={fieldStyle} />
      <input placeholder="Add a location — optional" value={where} onChange={(e) => setWhere(e.target.value)} style={fieldStyle} />
      <div style={agentBox}>
        <span style={{ fontSize: 12, fontWeight: 600, color: "#2A9D8F" }}>Closest for the group</span>
        <span style={{ fontSize: 12.5, lineHeight: 1.5, color: "#5c564b" }}>
          Once you add a place, the agent flags who it's most convenient for.
        </span>
      </div>
      <div className="hov-lift-sm" style={{ ...dpill(false), justifyContent: "center", opacity: busy ? 0.6 : 1 }} onClick={create}>
        {busy ? "Creating…" : "Create task"}
      </div>
    </div>
  );
}

const hhmm = (d) =>
  `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;

function NewPollModal() {
  const { modal, setModal, createPollDirect, members, setPage } = useApp();
  const pre = modal.prefill || {};
  const [title, setTitle] = useState(pre.title || "");
  const [date, setDate] = useState(() =>
    (pre.start ? new Date(pre.start) : new Date()).toISOString().slice(0, 10)
  );
  const [start, setStart] = useState(pre.start ? hhmm(new Date(pre.start)) : "19:00");
  const [end, setEnd] = useState(pre.end ? hhmm(new Date(pre.end)) : "21:00");
  const [where, setWhere] = useState("");
  const [minYes, setMinYes] = useState(Math.max(1, members.length));
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  const create = async () => {
    if (busy) return;
    setBusy(true);
    setErr("");
    try {
      await createPollDirect({
        title: title.trim() || "Group meetup",
        start_iso: new Date(`${date}T${start}`).toISOString(),
        end_iso: new Date(`${date}T${end}`).toISOString(),
        location: where.trim() || null,
        min_yes: minYes,
      });
      setModal(null);
      setPage("polls");
    } catch (e) {
      setErr(e.message || "That didn't work — try again.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div style={card} onClick={stop}>
      <div>
        <div style={{ fontSize: 19, fontWeight: 600 }}>New poll</div>
        <div style={{ fontSize: 12, color: "#8c8577", marginTop: 3 }}>
          Orbi proposes it to the group; it books automatically once enough
          people say yes and nobody says no.
        </div>
      </div>
      <input placeholder="Title — e.g. Dinner this week" value={title} onChange={(e) => setTitle(e.target.value)} style={fieldStyle} autoFocus />
      <div style={{ display: "flex", gap: 9 }}>
        <input type="date" value={date} onChange={(e) => setDate(e.target.value)} style={{ ...fieldStyle, flex: 1.4 }} />
        <input type="time" value={start} onChange={(e) => setStart(e.target.value)} style={fieldStyle} />
        <input type="time" value={end} onChange={(e) => setEnd(e.target.value)} style={fieldStyle} />
      </div>
      <input placeholder="Location — optional" value={where} onChange={(e) => setWhere(e.target.value)} style={fieldStyle} />
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <span style={{ ...fieldLabel, flex: 1 }}>Yes votes needed to lock</span>
        <div style={{ display: "flex", gap: 6 }}>
          {Array.from({ length: Math.max(1, members.length) }, (_, i) => i + 1).map((n) => (
            <div
              key={n}
              onClick={() => setMinYes(n)}
              style={{
                ...(minYes === n ? dpill(true) : gpill(true)),
                width: 34, height: 34, padding: 0, justifyContent: "center",
              }}
            >
              {n}
            </div>
          ))}
        </div>
      </div>
      {err && <div style={{ fontSize: 12, color: "#D95D39" }}>{err}</div>}
      <div className="hov-lift-sm" style={{ ...dpill(false), justifyContent: "center", opacity: busy ? 0.6 : 1 }} onClick={create}>
        {busy ? "Starting…" : "Start the poll"}
      </div>
    </div>
  );
}

// Sage free-window modal: everyone's clear — book it outright or poll first.
function FreeModal() {
  const { modal, setModal, createEvent, setPage, setView } = useApp();
  const slot = modal.slot;
  const [busy, setBusy] = useState(false);

  const range = `${fmtDayLong(slot.start)} · ${fmtRange(slot.start, slot.end)}`;

  const bookIt = async () => {
    if (busy) return;
    setBusy(true);
    try {
      await createEvent({
        kind: "event",
        title: "Group hangout",
        category: "Event",
        start_iso: slot.start.toISOString(),
        end_iso: slot.end.toISOString(),
        sync_google: true,
      });
      setModal(null);
      setPage("calendar");
      setView("week");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div style={card} onClick={stop}>
      <div style={{ alignSelf: "flex-start", fontSize: 9.5, fontWeight: 600, letterSpacing: ".05em", textTransform: "uppercase", padding: "4px 11px", borderRadius: 999, background: "rgba(42,157,143,.14)", color: "#2A9D8F" }}>
        Free window
      </div>
      <div style={{ fontSize: 20, fontWeight: 600 }}>Everyone free · {range}</div>
      <div style={{ fontSize: 13, color: "#8c8577", lineHeight: 1.5, marginTop: -6 }}>
        Every connected calendar is clear here. Book it straight onto everyone's
        calendar, or start a poll if you'd rather ask first.
      </div>
      <div style={{ display: "flex", gap: 9 }}>
        <div className="hov-lift-sm" style={{ ...dpill(true), opacity: busy ? 0.6 : 1 }} onClick={bookIt}>
          {busy ? "Booking…" : "Book it"}
        </div>
        <div
          className="hov-glass"
          style={gpill(true)}
          onClick={() => setModal({ type: "newPoll", prefill: { start: slot.start, end: slot.end } })}
        >
          Start poll
        </div>
      </div>
    </div>
  );
}

function InviteModal() {
  const { setModal, activeGroup } = useApp();
  const [copied, setCopied] = useState(false);
  const code = activeGroup?.invite_code || "";
  return (
    <div style={card} onClick={stop}>
      <div>
        <div style={{ fontSize: 19, fontWeight: 600 }}>Invite people</div>
        <div style={{ fontSize: 12, color: "#8c8577", marginTop: 3 }}>
          To {activeGroup?.name} — share the invite code, they join from the
          welcome screen after connecting their Google Calendar.
        </div>
      </div>
      <div style={{ ...fieldRead, textAlign: "center", fontSize: 22, fontWeight: 600, letterSpacing: ".25em" }}>
        {code}
      </div>
      <div
        className="hov-lift-sm"
        style={{ ...dpill(false), justifyContent: "center" }}
        onClick={() => {
          navigator.clipboard?.writeText(code);
          setCopied(true);
          setTimeout(() => setCopied(false), 1500);
        }}
      >
        {copied ? "Copied ✓" : "Copy invite code"}
      </div>
      <div style={{ fontSize: 12, color: "#a09889", lineHeight: 1.5 }}>
        Anyone with the code can join — no roles, no hierarchy. New members
        connect their own calendar so Orbi can plan around them.
      </div>
    </div>
  );
}

function NewGroupModal() {
  const { setModal, createGroup, joinGroup } = useApp();
  const [name, setName] = useState("");
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  const run = async (fn) => {
    setBusy(true);
    setErr("");
    try {
      await fn();
      setModal(null);
    } catch (e) {
      setErr(e.message || "That didn't work.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div style={card} onClick={stop}>
      <div style={{ fontSize: 19, fontWeight: 600 }}>New group</div>
      <div style={{ display: "flex", gap: 9 }}>
        <input placeholder="Group name — e.g. Beirut Crew" value={name} onChange={(e) => setName(e.target.value)} style={fieldStyle} />
        <div
          className="hov-lift-sm"
          style={{ ...dpill(true), opacity: busy || !name.trim() ? 0.6 : 1 }}
          onClick={() => name.trim() && !busy && run(() => createGroup(name.trim()))}
        >
          Create
        </div>
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <div style={{ flex: 1, height: 1, background: "rgba(150,142,128,.28)" }} />
        <span style={{ fontSize: 11, color: "#a09889" }}>or join one</span>
        <div style={{ flex: 1, height: 1, background: "rgba(150,142,128,.28)" }} />
      </div>
      <div style={{ display: "flex", gap: 9 }}>
        <input placeholder="Invite code — e.g. 4PYJU8" value={code} maxLength={6} onChange={(e) => setCode(e.target.value)} style={fieldStyle} />
        <div
          className="hov-lift-sm"
          style={{ ...dpill(true), opacity: busy || code.trim().length !== 6 ? 0.6 : 1 }}
          onClick={() => code.trim().length === 6 && !busy && run(() => joinGroup(code))}
        >
          Join
        </div>
      </div>
      {err && <div style={{ fontSize: 12, color: "#D95D39" }}>{err}</div>}
    </div>
  );
}
