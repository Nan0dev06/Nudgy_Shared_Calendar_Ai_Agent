import { useEffect, useMemo, useState } from "react";
import { useApp } from "../ctx.js";
import {
  heavy, gpill, dpill, catChip, avatar, agentBox, fieldStyle, fieldRead,
  fieldLabel, toggleStyle, knobStyle,
} from "../theme.js";
import { ClockIcon, PinIcon, PlusIcon, XIcon, ChevronLeft } from "../Icons.jsx";
import { fmtDayLong, fmtRange } from "../dates.js";
import {
  PlacePicker, GlassDatePicker, GlassTimePicker, StarRow, rememberPlace,
} from "./Fields.jsx";

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
      {modal.type === "create" && <CreateChooser />}
      {modal.type === "newEvent" && <NewEventModal />}
      {modal.type === "newTask" && <NewTaskModal />}
      {modal.type === "newPoll" && <NewPollModal />}
      {modal.type === "review" && <ReviewModal />}
      {modal.type === "free" && <FreeModal />}
      {modal.type === "invite" && <InviteModal />}
      {modal.type === "newGroup" && <NewGroupModal />}
    </div>
  );
}

const stop = (e) => e.stopPropagation();

const errText = (msg) => (
  <div style={{ fontSize: 12, color: "#D95D39", lineHeight: 1.45 }}>{msg}</div>
);

// ---- pick what to create ----------------------------------------------------
function CreateChooser() {
  const { setModal } = useApp();
  const opt = (label, sub, type, color) => (
    <div
      className="hov-lift-sm"
      onClick={() => setModal({ type })}
      style={{
        display: "flex", alignItems: "center", gap: 12, padding: "14px 16px",
        borderRadius: 16, cursor: "pointer", background: "rgba(255,253,247,.5)",
        border: "1px solid rgba(255,255,255,.6)", transition: "all .2s",
      }}
    >
      <div
        style={{
          width: 34, height: 34, borderRadius: 12, flex: "none",
          background: `color-mix(in srgb, ${color} 18%, rgba(255,253,247,.8))`,
          color, display: "flex", alignItems: "center", justifyContent: "center",
        }}
      >
        <PlusIcon size={15} />
      </div>
      <div style={{ display: "flex", flexDirection: "column" }}>
        <span style={{ fontSize: 14, fontWeight: 600 }}>{label}</span>
        <span style={{ fontSize: 11.5, color: "#8c8577" }}>{sub}</span>
      </div>
    </div>
  );
  return (
    <div style={card} onClick={stop}>
      <div style={{ fontSize: 19, fontWeight: 600 }}>Create</div>
      {opt("Task", "Something someone needs to do", "newTask", "#DCA744")}
      {opt("Event", "A time that's already decided", "newEvent", "#2A9D8F")}
      {opt("Poll", "Ask who's in, or propose a time & place", "newPoll", "#D95D39")}
    </div>
  );
}

// ---- view an event ----------------------------------------------------------
function EventModal() {
  const { modal, setModal, rsvp, setRsvp, members, reviews } = useApp();
  const e = modal.event;
  const my = rsvp[e.id];
  const past = e.end < new Date();
  const canReview =
    past && e.where && e.where !== "—" && !reviews.some((r) => r.place === e.where);
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
        <div style={{ display: "flex", gap: 9, flexWrap: "wrap" }}>
          {e.link && (
            <a href={e.link} target="_blank" rel="noreferrer" style={{ ...gpill(true), textDecoration: "none" }}>
              Open in Google Calendar
            </a>
          )}
          {canReview && (
            <div
              className="hov-lift-sm"
              style={{ ...gpill(true), color: "#DCA744" }}
              onClick={() => setModal({ type: "review", place: e.where })}
            >
              ★ Rate this place
            </div>
          )}
        </div>
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
  const {
    modal, setModal, createEvent, members, me, setPage, setView,
    reviews, addDraft, removeDraft,
  } = useApp();
  const draft = modal.draft || {};
  const [title, setTitle] = useState(draft.title || "");
  const [date, setDate] = useState(draft.date || "");
  const [start, setStart] = useState(draft.start || "");
  const [end, setEnd] = useState(draft.end || "");
  const [where, setWhere] = useState(draft.where || "");
  const [cat, setCat] = useState(draft.cat || "Event");
  const [inv, setInv] = useState(members.filter((m) => m.email !== me?.email).map((m) => m.email));
  const [sync, setSync] = useState(!!me?.calendar_connected);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  const hasTime = date && start && end;

  const create = async () => {
    if (busy) return;
    if (!title.trim()) {
      setErr("Give the event a title first.");
      return;
    }
    if (!hasTime) {
      setErr("Pick a day and both times — or save it for later below.");
      return;
    }
    setBusy(true);
    setErr("");
    try {
      await createEvent({
        kind: "event",
        title: title.trim(),
        category: cat,
        location: where.trim() || null,
        start_iso: new Date(`${date}T${start}`).toISOString(),
        end_iso: new Date(`${date}T${end}`).toISOString(),
        invite_emails: inv,
        sync_google: sync,
      });
      if (where.trim()) rememberPlace(where.trim());
      if (draft.id) removeDraft(draft.id);
      setModal(null);
      setPage("calendar");
      setView("week");
    } catch (e) {
      setErr(e.message || "That didn't work — try again.");
    } finally {
      setBusy(false);
    }
  };

  const saveDraft = () => {
    if (!title.trim()) {
      setErr("Even a draft needs a title.");
      return;
    }
    if (draft.id) removeDraft(draft.id);
    addDraft({ kind: "event", title: title.trim(), where, date, start, end, cat });
    setModal(null);
  };

  return (
    <div style={{ ...card, maxHeight: "88vh", overflow: "visible" }} onClick={stop}>
      <div style={{ fontSize: 19, fontWeight: 600 }}>New event</div>
      <input placeholder="Title — required" value={title} onChange={(e) => setTitle(e.target.value)} style={fieldStyle} autoFocus />
      <div style={{ display: "flex", gap: 9 }}>
        <GlassDatePicker value={date} onChange={setDate} />
        <GlassTimePicker value={start} onChange={setStart} placeholder="From" />
        <GlassTimePicker value={end} onChange={setEnd} placeholder="To" />
      </div>
      <PlacePicker
        value={where}
        onChange={setWhere}
        placeholder="Add a location — optional"
        reviewedPlaces={reviews.map((r) => r.place)}
      />
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
      {err && errText(err)}
      <div style={{ display: "flex", gap: 9 }}>
        <div
          className="hov-lift-sm"
          style={{ ...dpill(false), flex: 1, justifyContent: "center", opacity: busy || !title.trim() || !hasTime ? 0.55 : 1 }}
          onClick={create}
        >
          {busy ? "Creating…" : "Create event"}
        </div>
        {!hasTime && (
          <div className="hov-glass" style={{ ...gpill(false), justifyContent: "center" }} onClick={saveDraft} title="No time yet — park it in the sidebar">
            Save for later
          </div>
        )}
      </div>
    </div>
  );
}

function NewTaskModal() {
  const { modal, setModal, createEvent, reviews, addDraft, removeDraft } = useApp();
  const draft = modal.draft || {};
  const [title, setTitle] = useState(draft.title || "");
  const [cat, setCat] = useState(draft.cat || "Task");
  const [due, setDue] = useState(draft.date || "");
  const [where, setWhere] = useState(draft.where || "");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  const create = async () => {
    if (busy) return;
    // a task that's only a place makes no sense — it needs a title, and if a
    // place is set it needs a due date too
    if (!title.trim()) {
      setErr("Give the task a title first.");
      return;
    }
    if (where.trim() && !due) {
      setErr("A place with no due date doesn't help anyone — add when it's needed by, or drop the place.");
      return;
    }
    setBusy(true);
    setErr("");
    try {
      await createEvent({
        kind: "task",
        title: title.trim(),
        category: cat,
        location: where.trim() || null,
        start_iso: due ? new Date(`${due}T12:00`).toISOString() : null,
      });
      if (where.trim()) rememberPlace(where.trim());
      if (draft.id) removeDraft(draft.id);
      setModal(null);
    } catch (e) {
      setErr(e.message || "That didn't work — try again.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div style={{ ...card, overflow: "visible" }} onClick={stop}>
      <div style={{ fontSize: 19, fontWeight: 600 }}>New task</div>
      <input placeholder="Title — required, e.g. Pick up the cake" value={title} onChange={(e) => setTitle(e.target.value)} style={fieldStyle} autoFocus />
      {catPills(["Task", "Errand", "Prep"], cat, setCat)}
      <GlassDatePicker value={due} onChange={setDue} placeholder="Due date" />
      <PlacePicker
        value={where}
        onChange={setWhere}
        placeholder="Add a location — optional"
        reviewedPlaces={reviews.map((r) => r.place)}
      />
      <div style={agentBox}>
        <span style={{ fontSize: 12, fontWeight: 600, color: "#2A9D8F" }}>Closest for the group</span>
        <span style={{ fontSize: 12.5, lineHeight: 1.5, color: "#5c564b" }}>
          Once you add a place, the agent can pick spots near whoever's doing it.
        </span>
      </div>
      {err && errText(err)}
      <div style={{ display: "flex", gap: 9 }}>
        <div
          className="hov-lift-sm"
          style={{ ...dpill(false), flex: 1, justifyContent: "center", opacity: busy || !title.trim() ? 0.55 : 1 }}
          onClick={create}
        >
          {busy ? "Creating…" : "Create task"}
        </div>
        {!due && title.trim() && (
          <div
            className="hov-glass"
            style={{ ...gpill(false), justifyContent: "center" }}
            onClick={() => {
              if (draft.id) removeDraft(draft.id);
              addDraft({ kind: "task", title: title.trim(), where, cat });
              setModal(null);
            }}
          >
            Save for later
          </div>
        )}
      </div>
    </div>
  );
}

// ---- the two-stage poll composer -------------------------------------------
// Panel 1: what + who ("check who's in" can submit right here, timeless).
// Panel 2: place + day + candidate times — slides in inside the same glass.
function NewPollModal() {
  const {
    modal, setModal, createPlanDirect, members, plans, setPage,
    reviews, addDraft, removeDraft,
  } = useApp();
  const pre = modal.prefill || {};
  const change = modal.proposeChange || null;
  const draft = modal.draft || {};

  const [stage, setStage] = useState(change?.skipToTimes ? 1 : 0);
  const [title, setTitle] = useState(change?.title || draft.title || pre.title || "");
  const [where, setWhere] = useState(change?.location || draft.where || "");
  const [date, setDate] = useState(
    draft.date || (pre.start ? isoDate(new Date(pre.start)) : "")
  );
  const [slots, setSlots] = useState(() => {
    if (pre.start)
      return [{ start: hhmm(new Date(pre.start)), end: hhmm(new Date(pre.end)) }];
    if (change?.slots?.length)
      return change.slots.map((s) => ({
        start: hhmm(new Date(s.start_iso)),
        end: hhmm(new Date(s.end_iso)),
      }));
    return [{ start: "", end: "" }];
  });
  const [expected, setExpected] = useState(draft.expected || null); // people count
  const [customN, setCustomN] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [dupLink, setDupLink] = useState(null);

  const total = Math.max(1, members.length);

  const buildSlots = () =>
    slots
      .filter((s) => s.start && s.end && date)
      .map((s) => ({
        start_iso: new Date(`${date}T${s.start}`).toISOString(),
        end_iso: new Date(`${date}T${s.end}`).toISOString(),
      }));

  // exact-duplicate guard: same title + place + candidate times as an existing
  // poll → don't create it twice; link to the live one, or explain the failure
  const findDuplicate = (bodySlots) => {
    const norm = (s) => (s || "").trim().toLowerCase();
    // compare slots as instants — our ISO strings end in "Z", the backend's
    // in "+00:00", so string equality would never fire
    const key = (t, l, ss) =>
      `${norm(t)}|${norm(l)}|${ss.map((s) => `${new Date(s.start_iso).getTime()}-${new Date(s.end_iso).getTime()}`).sort().join(",")}`;
    const mine = key(title, where, bodySlots);
    return plans.find(
      (p) =>
        key(p.title, p.location, (p.times || []).map((t) => ({ start_iso: t.start_iso, end_iso: t.end_iso }))) === mine
    );
  };

  const submit = async (interestOnly) => {
    if (busy) return;
    if (!title.trim()) {
      setErr("A poll needs a title — the rest can be voted on.");
      return;
    }
    // times without a day would be silently dropped by buildSlots — say so
    if (!interestOnly && !date && slots.some((s) => s.start && s.end)) {
      setErr("Pick which day those times are on.");
      return;
    }
    const bodySlots = interestOnly ? [] : buildSlots();
    if (!interestOnly && bodySlots.length === 0 && !where.trim()) {
      setErr("Add a place or at least one candidate time — or go back and just check who's in.");
      return;
    }
    const dup = findDuplicate(bodySlots);
    if (dup) {
      setDupLink(dup);
      setErr(
        dup.status === "open"
          ? "This exact poll is already open."
          : dup.status === "dead"
            ? "This exact poll already ran and didn't work out — change the time or place before proposing it again."
            : "This exact poll already exists."
      );
      return;
    }
    setBusy(true);
    setErr("");
    try {
      const plan = await createPlanDirect({
        title: title.trim(),
        location: where.trim() || null,
        slots: bodySlots,
      });
      const n = expected === "custom" ? parseInt(customN, 10) : expected;
      if (n) {
        try {
          const m = JSON.parse(localStorage.getItem("ov.expected") || "{}");
          m[plan.id] = n;
          localStorage.setItem("ov.expected", JSON.stringify(m));
        } catch { /* fine */ }
      }
      if (where.trim()) rememberPlace(where.trim());
      if (draft.id) removeDraft(draft.id);
      setModal(null);
      setPage("polls");
    } catch (e) {
      setErr(e.message || "That didn't work — try again.");
    } finally {
      setBusy(false);
    }
  };

  const nChip = (n, label) => (
    <div
      key={label}
      onClick={() => setExpected(expected === n ? null : n)}
      style={{
        ...(expected === n ? dpill(true) : gpill(true)),
        padding: "6px 13px", fontSize: 12,
      }}
    >
      {label}
    </div>
  );

  const setSlot = (i, k, v) =>
    setSlots((ss) => ss.map((s, j) => (j === i ? { ...s, [k]: v } : s)));

  return (
    <div style={{ ...card, width: 440, padding: 0, overflow: "hidden" }} onClick={stop}>
      <div className="slide-track" style={{ transform: stage === 0 ? "translateX(0)" : "translateX(-50%)" }}>
        {/* ---------- panel 1: what + who ---------- */}
        <div className="slide-panel" style={{ padding: 22 }}>
          <div>
            <div style={{ fontSize: 19, fontWeight: 600 }}>
              {change ? "Suggest a change" : "New poll"}
            </div>
            <div style={{ fontSize: 12, color: "#8c8577", marginTop: 3, lineHeight: 1.5 }}>
              {change
                ? "Same plan, new proposal — it starts pre-filled, change what didn't work."
                : "Only the title is required. Time and place can be voted on — or left open until you know who's in."}
            </div>
          </div>
          <input
            placeholder="What are we doing? — required"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            style={fieldStyle}
            autoFocus
          />
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            <span style={fieldLabel}>How many people should this be?</span>
            <div style={{ display: "flex", gap: 7, flexWrap: "wrap", alignItems: "center" }}>
              {nChip(2, "2")}
              {nChip(3, "3")}
              {nChip(4, "4")}
              {nChip(total, `Everyone (${total})`)}
              <div
                onClick={() => setExpected("custom")}
                style={{ ...(expected === "custom" ? dpill(true) : gpill(true)), padding: "6px 13px", fontSize: 12 }}
              >
                Custom
              </div>
              {expected === "custom" && (
                <input
                  type="number"
                  min={1}
                  value={customN}
                  onChange={(e) => setCustomN(e.target.value)}
                  style={{ ...fieldStyle, flex: "none", width: 64, padding: "7px 10px" }}
                  placeholder="n"
                  autoFocus
                />
              )}
            </div>
            <span style={{ fontSize: 11, color: "#a09889" }}>
              Optional — helps Nudgy know when enough people are in.
            </span>
          </div>
          {err && stage === 0 && errText(err)}
          {dupLink && stage === 0 && dupJump(dupLink, setModal, setPage)}
          <div style={{ display: "flex", gap: 9, marginTop: "auto" }}>
            <div
              className="hov-lift-sm"
              style={{ ...gpill(false), flex: 1, justifyContent: "center", opacity: busy || !title.trim() ? 0.55 : 1 }}
              onClick={() => submit(true)}
              title="No time or place yet — just find out who wants to"
            >
              {busy ? "Asking…" : "Just check who's in"}
            </div>
            <div
              className="hov-lift-sm"
              style={{ ...dpill(false), flex: 1, justifyContent: "center", opacity: !title.trim() ? 0.55 : 1 }}
              onClick={() => title.trim() && (setErr(""), setStage(1))}
            >
              Time & place →
            </div>
          </div>
        </div>

        {/* ---------- panel 2: place + day + times ---------- */}
        <div className="slide-panel" style={{ padding: 22 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <div
              className="hov-icon"
              style={{ width: 26, height: 26, borderRadius: 9, display: "flex", alignItems: "center", justifyContent: "center", cursor: "pointer", color: "#8c8577", flex: "none" }}
              onClick={() => setStage(0)}
            >
              <ChevronLeft size={14} />
            </div>
            <div>
              <div style={{ fontSize: 17, fontWeight: 600 }}>{title || "Time & place"}</div>
              <div style={{ fontSize: 11.5, color: "#8c8577" }}>
                Everything here is a proposal — the group votes on it.
              </div>
            </div>
          </div>
          <PlacePicker
            value={where}
            onChange={setWhere}
            placeholder="Where? — search or type a place"
            reviewedPlaces={reviews.map((r) => r.place)}
          />
          <GlassDatePicker value={date} onChange={setDate} placeholder="Which day?" />
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            <span style={fieldLabel}>Candidate times — asked one at a time</span>
            {slots.map((s, i) => (
              <div key={i} style={{ display: "flex", gap: 8, alignItems: "center" }}>
                <GlassTimePicker value={s.start} onChange={(v) => setSlot(i, "start", v)} placeholder="From" />
                <GlassTimePicker value={s.end} onChange={(v) => setSlot(i, "end", v)} placeholder="To" />
                {slots.length > 1 && (
                  <div
                    className="hov-icon"
                    style={{ width: 24, height: 24, borderRadius: 8, display: "flex", alignItems: "center", justifyContent: "center", cursor: "pointer", color: "#a49c8c", flex: "none" }}
                    onClick={() => setSlots((ss) => ss.filter((_, j) => j !== i))}
                  >
                    <XIcon size={12} />
                  </div>
                )}
              </div>
            ))}
            {slots.length < 3 && (
              <div
                className="hov-row"
                style={{ fontSize: 12, fontWeight: 600, color: "#2B5B84", cursor: "pointer", padding: "4px 2px", alignSelf: "flex-start", borderRadius: 8 }}
                onClick={() => setSlots((ss) => [...ss, { start: "", end: "" }])}
              >
                + another time
              </div>
            )}
          </div>
          {err && stage === 1 && errText(err)}
          {dupLink && stage === 1 && dupJump(dupLink, setModal, setPage)}
          <div style={{ display: "flex", gap: 9, marginTop: "auto" }}>
            <div
              className="hov-lift-sm"
              style={{ ...dpill(false), flex: 1, justifyContent: "center", opacity: busy ? 0.55 : 1 }}
              onClick={() => submit(false)}
            >
              {busy ? "Starting…" : change ? "Propose the change" : "Start the poll"}
            </div>
            <div
              className="hov-glass"
              style={{ ...gpill(false), justifyContent: "center" }}
              onClick={() => {
                if (!title.trim()) return setErr("Even a draft needs a title.");
                if (draft.id) removeDraft(draft.id);
                addDraft({ kind: "plan", title: title.trim(), where, date, expected });
                setModal(null);
              }}
              title="Park it in the sidebar until you know more"
            >
              Save for later
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function dupJump(dup, setModal, setPage) {
  return (
    <div
      style={{ fontSize: 12.5, fontWeight: 600, color: "#2B5B84", cursor: "pointer" }}
      onClick={() => {
        setModal(null);
        setPage("polls");
        setTimeout(() => {
          document.getElementById(`plan-${dup.id}`)?.scrollIntoView({ behavior: "smooth", block: "center" });
        }, 120);
      }}
    >
      {dup.status === "dead" ? "See how it went →" : "Jump to that poll →"}
    </div>
  );
}

const hhmm = (d) =>
  `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
const isoDate = (d) =>
  `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;

// ---- rate a place -----------------------------------------------------------
function ReviewModal() {
  const { modal, setModal, addReview, reviews } = useApp();
  const [place, setPlace] = useState(modal.place || "");
  const [stars, setStars] = useState(0);
  const [text, setText] = useState("");

  return (
    <div style={{ ...card, overflow: "visible" }} onClick={stop}>
      <div>
        <div style={{ fontSize: 19, fontWeight: 600 }}>How was it?</div>
        <div style={{ fontSize: 12, color: "#8c8577", marginTop: 3, lineHeight: 1.5 }}>
          Nudgy remembers what you liked and suggests better spots next time.
        </div>
      </div>
      <PlacePicker
        value={place}
        onChange={setPlace}
        placeholder="Which place?"
        reviewedPlaces={reviews.map((r) => r.place)}
      />
      <div style={{ display: "flex", justifyContent: "center", padding: "4px 0" }}>
        <StarRow value={stars} onChange={setStars} size={28} />
      </div>
      <input
        placeholder="A few words — great coffee, too loud, …"
        value={text}
        onChange={(e) => setText(e.target.value)}
        style={fieldStyle}
      />
      <div
        className="hov-lift-sm"
        style={{ ...dpill(false), justifyContent: "center", opacity: place.trim() && stars ? 1 : 0.55 }}
        onClick={() => {
          if (!place.trim() || !stars) return;
          addReview({ place: place.trim(), stars, text: text.trim() });
          rememberPlace(place.trim());
          setModal(null);
        }}
      >
        Save review
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
  const { activeGroup } = useApp();
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
        connect their own calendar so Nudgy can plan around them.
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
      {err && errText(err)}
    </div>
  );
}
