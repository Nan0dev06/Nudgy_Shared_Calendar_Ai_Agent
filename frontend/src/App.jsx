import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AppCtx } from "./ctx.js";
import { api } from "./api.js";
import { decorateMembers, nameFromEmail } from "./people.js";
import Blobs from "./components/Blobs.jsx";
import SignIn from "./screens/SignIn.jsx";
import GroupGate from "./screens/GroupGate.jsx";
import Shell from "./components/Shell.jsx";
import { orbGradient } from "./theme.js";

// ---- localStorage-backed state --------------------------------------------
function readStored(key, initial) {
  try {
    const raw = localStorage.getItem(key);
    return raw != null ? JSON.parse(raw) : initial;
  } catch {
    return initial;
  }
}

function useStored(key, initial) {
  const [val, setVal] = useState(() => readStored(key, initial));
  // when the key changes (e.g. per-group keys after a group switch), reload
  // the new key's value instead of writing the old group's state into it
  const keyRef = useRef(key);
  if (keyRef.current !== key) {
    keyRef.current = key;
    setVal(readStored(key, initial));
  }
  useEffect(() => {
    if (keyRef.current !== key) return; // mid-switch render — don't cross-write
    try {
      localStorage.setItem(key, JSON.stringify(val));
    } catch {
      /* storage full/blocked — state still works in memory */
    }
  }, [key, val]);
  return [val, setVal];
}

const BG = {
  height: "100vh",
  display: "flex",
  position: "relative",
  overflow: "hidden",
  background:
    "radial-gradient(120% 100% at 15% 0%, #F2E9DA, transparent 60%), radial-gradient(110% 90% at 100% 100%, #EEE2D2, transparent 55%), linear-gradient(140deg, #EFE7D9, #E8DECD 55%, #EDE2D3)",
};

export default function App() {
  // auth + data
  const [me, setMe] = useState(null);
  const [authChecked, setAuthChecked] = useState(false);
  const [groups, setGroups] = useState([]);
  const [activeGroupId, setActiveGroupId] = useStored("ov.activeGroup", null);
  const [members, setMembers] = useState([]);
  const [plans, setPlans] = useState([]);
  const [gateDone, setGateDone] = useState(
    () => sessionStorage.getItem("ov.gateDone") === "1"
  );

  // ui
  const [page, setPage] = useState("home");
  const [view, setView] = useState("week");
  const [calAnchor, setCalAnchor] = useState(() => new Date());
  const [collapsed, setCollapsed] = useState(false);
  const [focusId, setFocusId] = useState(null);
  const [hoverKey, setHoverKey] = useState(null);
  const [modal, setModal] = useState(null);
  const [groupOpen, setGroupOpen] = useState(false);
  const [notifOpen, setNotifOpen] = useState(false);
  const [settingsTab, setSettingsTab] = useState("Account");

  // chat
  const [chatOpen, setChatOpen] = useState(false);
  const [chatMsgs, setChatMsgs] = useState([]);
  const [chatTyping, setChatTyping] = useState(false);

  // group events/tasks + live availability (both from the backend)
  const [groupEvents, setGroupEvents] = useState([]);
  const [avail, setAvail] = useState({ members_busy: [], common_slots: [] });

  // persisted user-local state
  const gk = activeGroupId ? `.g${activeGroupId}` : "";
  const [activity, setActivity] = useStored(`ov.activity${gk}`, []);
  const [rsvp, setRsvp] = useStored("ov.rsvp", {});
  // v2: auto-decline + share-busy-only now default OFF (user opts in),
  // quiet hours are editable, and conflict priority is a choice.
  const [prefs, setPrefs] = useStored("ov.prefs2", {
    push: true,
    digest: false,
    auto: false,
    busy: false,
    prio: true,
    nvote: true,
    nrsvp: true,
    nment: true,
    quiet: false,
    quietStart: "22:00",
    quietEnd: "08:00",
  });
  const [memory, setMemory] = useStored("ov.memory", []);
  const [profile, setProfile] = useStored("ov.profile", {});
  const [readNotifs, setReadNotifs] = useStored("ov.readNotifs", []);
  // place reviews: [{place, stars, text, ts}] — the agent's taste memory
  const [reviews, setReviews] = useStored("ov.reviews", []);
  // unfinished things (event/poll started without a time) — sidebar "Drafts"
  const [drafts, setDrafts] = useStored(`ov.drafts${gk}`, []);

  const activeGroup = groups.find((g) => g.id === activeGroupId) || null;

  // ---- data loading --------------------------------------------------------
  const loadGroups = useCallback(async () => {
    const gs = await api.groups();
    setGroups(gs);
    return gs;
  }, []);

  useEffect(() => {
    (async () => {
      try {
        const m = await api.me();
        setMe(m);
        const gs = await loadGroups();
        if (gs.length && !gs.some((g) => g.id === activeGroupId)) {
          setActiveGroupId(gs[0].id);
        }
      } catch {
        setMe(null);
      } finally {
        setAuthChecked(true);
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const refreshGroupData = useCallback(async () => {
    if (!activeGroupId) {
      setMembers([]);
      setPlans([]);
      setGroupEvents([]);
      setAvail({ members_busy: [], common_slots: [] });
      return;
    }
    // fetch independently: one failing call (old server, dropped request)
    // must never blank the others — losing members because /plans 404'd is
    // exactly the bug this replaces
    const [ms, ps, evs] = await Promise.allSettled([
      api.members(activeGroupId),
      api.plans(activeGroupId),
      api.events(activeGroupId),
    ]);
    setMembers(
      ms.status === "fulfilled"
        ? decorateMembers(ms.value, meRef.current?.email)
        : []
    );
    if (ps.status === "fulfilled") setPlans(ps.value);
    else setPlans([]);
    if (evs.status === "fulfilled") setGroupEvents(evs.value);
    else setGroupEvents([]);
    // availability hits Google live for every member — slow, so don't block
    // the rest of the data on it
    api
      .availability(activeGroupId)
      .then((a) => setAvail(a))
      .catch(() => setAvail({ members_busy: [], common_slots: [] }));
  }, [activeGroupId]);

  const meRef = useRef(null);
  meRef.current = me;

  useEffect(() => {
    if (me && activeGroupId) refreshGroupData();
  }, [me, activeGroupId, refreshGroupData]);

  // ---- actions -------------------------------------------------------------
  const pushActivity = useCallback(
    (entry) => {
      setActivity((a) => [{ ...entry, ts: Date.now() }, ...a].slice(0, 60));
    },
    [setActivity]
  );

  // Stage 1 of the cascade: "are you in for this plan at all?"
  const voteInterest = useCallback(
    async (planId, yes) => {
      const out = await api.voteInterest(planId, yes);
      setPlans((ps) => ps.map((p) => (p.id === planId ? out : p)));
      pushActivity({
        dot: yes ? "#2A9D8F" : "#D95D39",
        pre: yes ? "You're in for " : "You passed on ",
        bold: out.title,
        post: "",
      });
      return out;
    },
    [pushActivity]
  );

  // Stage 2: "does this specific time work?"
  const voteTime = useCallback(
    async (planId, yes, roundId) => {
      const out = await api.voteTime(planId, yes, roundId);
      setPlans((ps) => ps.map((p) => (p.id === planId ? out : p)));
      pushActivity({
        dot: yes ? "#2A9D8F" : "#D95D39",
        pre: `You said the time ${yes ? "works" : "doesn't work"} for `,
        bold: out.title,
        post: "",
      });
      return out;
    },
    [pushActivity]
  );

  const createGroup = useCallback(
    async (name) => {
      const g = await api.createGroup(name);
      await loadGroups();
      setActiveGroupId(g.id);
      pushActivity({ dot: "#2B5B84", pre: "You created ", bold: g.name, post: "" });
      return g;
    },
    [loadGroups, pushActivity, setActiveGroupId]
  );

  const joinGroup = useCallback(
    async (code) => {
      const g = await api.joinGroup(code.trim().toUpperCase());
      await loadGroups();
      setActiveGroupId(g.id);
      pushActivity({ dot: "#2B5B84", pre: "You joined ", bold: g.name, post: "" });
      return g;
    },
    [loadGroups, pushActivity, setActiveGroupId]
  );

  const createEvent = useCallback(
    async (body) => {
      const out = await api.createEvent(activeGroupId, body);
      await refreshGroupData();
      pushActivity({
        dot: body.kind === "task" ? "#DCA744" : "#2A9D8F",
        pre: body.kind === "task" ? "You created task " : "You added ",
        bold: body.title,
        post: out.synced ? " (synced to Google Calendar)" : "",
      });
      return out;
    },
    [activeGroupId, refreshGroupData, pushActivity]
  );

  const setTaskDone = useCallback(
    async (eventId, done) => {
      await api.patchEvent(eventId, { done });
      setGroupEvents((evs) =>
        evs.map((e) => (e.id === eventId ? { ...e, done } : e))
      );
    },
    []
  );

  const removeEvent = useCallback(
    async (eventId) => {
      await api.deleteEvent(eventId);
      setGroupEvents((evs) => evs.filter((e) => e.id !== eventId));
    },
    []
  );

  const createPlanDirect = useCallback(
    async (body) => {
      const plan = await api.createPlan(activeGroupId, body);
      setPlans((ps) => [plan, ...ps]);
      pushActivity({
        dot: "#D95D39",
        pre: body.slots?.length ? "You started poll " : "You asked who's in for ",
        bold: plan.title,
        post: "",
      });
      return plan;
    },
    [activeGroupId, pushActivity]
  );

  const saveProfile = useCallback(async (patch) => {
    const updated = await api.patchMe(patch);
    setMe(updated);
    return updated;
  }, []);

  const addReview = useCallback(
    (rev) => {
      setReviews((rs) => [
        { ...rev, ts: Date.now() },
        ...rs.filter((r) => r.place !== rev.place),
      ]);
      pushActivity({
        dot: "#DCA744",
        pre: `You rated ${"★".repeat(rev.stars)} `,
        bold: rev.place,
        post: "",
      });
    },
    [setReviews, pushActivity]
  );

  const addDraft = useCallback(
    (d) => {
      setDrafts((ds) => [{ ...d, id: Date.now() }, ...ds].slice(0, 10));
    },
    [setDrafts]
  );

  const removeDraft = useCallback(
    (id) => setDrafts((ds) => ds.filter((d) => d.id !== id)),
    [setDrafts]
  );

  const logout = useCallback(async () => {
    try {
      await api.logout();
    } finally {
      sessionStorage.removeItem("ov.gateDone");
      window.location.reload();
    }
  }, []);

  const finishGate = useCallback(() => {
    sessionStorage.setItem("ov.gateDone", "1");
    setGateDone(true);
  }, []);

  // ---- chat (real agent) ---------------------------------------------------
  const STEP_LABELS = {
    get_group_members: "Checked who's in the group",
    find_meeting_slots: "Fetched live free/busy for every calendar",
    suggest_venues: "Searched for real venues near the group",
    create_plan: "Proposed a plan to the group",
    get_plan_status: "Read the plan's tally",
    use_next_time: "Moved to the next candidate time",
    lock_in_time: "Locked the time in and booked it",
  };

  const chatMsgsRef = useRef(chatMsgs);
  chatMsgsRef.current = chatMsgs;

  const doSend = useCallback(
    async (text) => {
      const t = (text || "").trim();
      if (!t || chatTyping) return;
      setChatOpen(true);
      setChatTyping(true);
      const history = chatMsgsRef.current
        .filter((m) => m.u || m.o)
        .slice(-20)
        .map((m) => ({
          role: m.u ? "user" : "assistant",
          content: m.u || m.o,
        }));
      setChatMsgs((ms) => [...ms, { u: t }]);
      try {
        const res = await api.chat(activeGroupId, t, history);
        const steps = (res.trace || [])
          .filter((s) => s.kind === "tool_call")
          .map((s) => STEP_LABELS[s.name] || `Ran ${s.name}`);
        // reasoning steps appear one by one (~650ms), then the answer
        steps.forEach((st, i) =>
          setTimeout(() => setChatMsgs((ms) => [...ms, { step: st }]), 350 + i * 650)
        );
        setTimeout(() => {
          setChatTyping(false);
          setChatMsgs((ms) => [...ms, { o: res.reply }]);
          const touchedPlans = (res.trace || []).some((s) =>
            ["create_plan", "lock_in_time", "use_next_time", "get_plan_status"].includes(s.name)
          );
          if (touchedPlans) {
            refreshGroupData();
            setChatMsgs((ms) => [
              ...ms,
              { acts: [{ label: "View polls", dark: true, go: () => setPage("polls") }] },
            ]);
          }
        }, 350 + steps.length * 650 + 300);
      } catch (e) {
        setChatTyping(false);
        setChatMsgs((ms) => [
          ...ms,
          { o: e.message || "Something went wrong — try again." },
        ]);
      }
    },
    [activeGroupId, chatTyping, refreshGroupData]
  );

  // ---- derived: calendar events -------------------------------------------
  const events = useMemo(() => {
    const fromPlans = plans.flatMap((p) =>
      (p.times || [])
        .filter((t) => t.booked && t.start_iso)
        .map((t) => ({
          id: `plan${p.id}r${t.round_id}`,
          title: p.title,
          cat: "Event",
          start: new Date(t.start_iso),
          end: new Date(t.end_iso),
          where: p.location || "—",
          link: t.event_link,
          agent: `Locked in by ${p.is_host ? "you" : nameFromEmail(p.host || "")} after the group voted.`,
          emails: members.map((m) => m.email),
          booked: true,
        }))
    );
    const fromBackend = groupEvents
      .filter((e) => e.kind === "event" && e.start_iso)
      .map((e) => ({
        id: "ev" + e.id,
        backendId: e.id,
        title: e.title,
        cat: e.category || "Event",
        start: new Date(e.start_iso),
        end: new Date(e.end_iso),
        where: e.location || "—",
        link: e.gcal_link,
        synced: e.synced,
        emails: members.map((m) => m.email),
      }));
    return [...fromPlans, ...fromBackend].sort((a, b) => a.start - b.start);
  }, [plans, groupEvents, members]);

  const tasks = useMemo(
    () =>
      groupEvents
        .filter((e) => e.kind === "task")
        .map((t) => ({
          id: t.id,
          title: t.title,
          cat: t.category || "Task",
          due: t.start_iso ? t.start_iso.slice(0, 10) : "anytime",
          dueAt: t.start_iso ? new Date(t.start_iso) : null,
          where: t.location,
          done: t.done,
        })),
    [groupEvents]
  );

  const openPlans = useMemo(
    () => plans.filter((p) => p.status === "open"),
    [plans]
  );

  // favorite place = your best-rated spot; falls back to the place you go most
  const favoritePlace = useMemo(() => {
    if (reviews.length) {
      const best = [...reviews].sort((a, b) => b.stars - a.stars || b.ts - a.ts)[0];
      return { name: best.place, note: `${"★".repeat(best.stars)} from your review` };
    }
    const counts = {};
    for (const e of events) if (e.where && e.where !== "—") counts[e.where] = (counts[e.where] || 0) + 1;
    const top = Object.entries(counts).sort((a, b) => b[1] - a[1])[0];
    return top
      ? { name: top[0], note: `${top[1]} outing${top[1] > 1 ? "s" : ""} there` }
      : null;
  }, [reviews, events]);

  // ---- notifications (derived + read state) --------------------------------
  const notifs = useMemo(() => {
    const out = [];
    for (const p of openPlans) {
      if (p.ballot?.stage === "interest")
        out.push({
          id: `in${p.id}`,
          dot: "#D95D39",
          pre: "Are you in for ",
          bold: p.title,
          post: "?",
          go: { page: "polls" },
        });
      else if (p.ballot?.stage === "time")
        out.push({
          id: `time${p.id}`,
          dot: "#DCA744",
          pre: "Does the time work for ",
          bold: p.title,
          post: "?",
          go: { page: "polls" },
        });
    }
    for (const p of plans) {
      for (const t of p.times || []) {
        if (t.booked)
          out.push({
            id: `lock${p.id}r${t.round_id}`,
            dot: "#2A9D8F",
            pre: "Locked in — ",
            bold: `${p.title} · ${t.label}`,
            post: "",
            go: { page: "calendar" },
          });
      }
    }
    // review nudges: outings that ended in the past week, place known, unreviewed
    const now = Date.now();
    for (const e of events) {
      if (
        e.where && e.where !== "—" &&
        e.end < now && now - e.end < 7 * 86400000 &&
        !reviews.some((r) => r.place === e.where)
      )
        out.push({
          id: `rev${e.id}`,
          dot: "#DCA744",
          pre: "How was ",
          bold: e.where,
          post: "? Rate it so Nudgy learns your taste.",
          go: { modal: { type: "review", place: e.where } },
        });
    }
    return out;
  }, [plans, openPlans, events, reviews]);

  const unread = notifs.some((n) => !readNotifs.includes(n.id));

  const ctx = {
    me, groups, activeGroup, activeGroupId, setActiveGroupId, members, plans,
    openPlans, events, tasks, avail,
    page, setPage, view, setView, calAnchor, setCalAnchor,
    collapsed, setCollapsed, focusId, setFocusId, hoverKey, setHoverKey,
    modal, setModal, groupOpen, setGroupOpen, notifOpen, setNotifOpen,
    settingsTab, setSettingsTab,
    chatOpen, setChatOpen, chatMsgs, chatTyping, doSend,
    activity, pushActivity, rsvp, setRsvp, prefs, setPrefs,
    memory, setMemory, profile, setProfile,
    notifs, unread, readNotifs, setReadNotifs,
    reviews, addReview, setReviews, favoritePlace,
    drafts, addDraft, removeDraft,
    voteInterest, voteTime, createGroup, joinGroup, logout, refreshGroupData,
    createEvent, setTaskDone, removeEvent, createPlanDirect, saveProfile,
    displayName:
      me?.display_name || profile.name || (me ? nameFromEmail(me.email) : ""),
  };

  // ---- screens -------------------------------------------------------------
  if (!authChecked)
    return (
      <div style={{ ...BG, alignItems: "center", justifyContent: "center" }}>
        <div
          style={{
            width: 58, height: 58, borderRadius: "50%",
            background: orbGradient(34),
            boxShadow: "0 12px 30px rgba(45,45,45,.22)",
            animation: "ofloat 3.4s ease-in-out infinite",
          }}
        />
      </div>
    );

  let screen;
  if (!me) screen = <SignIn />;
  else if (!gateDone || groups.length === 0)
    screen = <GroupGate onDone={finishGate} />;
  else screen = <Shell />;

  const blobPage = !me ? "signin" : !gateDone || groups.length === 0 ? "connect" : page;

  return (
    <AppCtx.Provider value={ctx}>
      <div style={BG}>
        <Blobs page={blobPage} />
        {screen}
      </div>
    </AppCtx.Provider>
  );
}
