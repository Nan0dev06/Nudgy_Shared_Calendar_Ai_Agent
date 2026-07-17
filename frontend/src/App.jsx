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
function useStored(key, initial) {
  const [val, setVal] = useState(() => {
    try {
      const raw = localStorage.getItem(key);
      return raw != null ? JSON.parse(raw) : initial;
    } catch {
      return initial;
    }
  });
  useEffect(() => {
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
  const [polls, setPolls] = useState([]);
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
  const [prefs, setPrefs] = useStored("ov.prefs", {
    push: true,
    digest: false,
    auto: true,
    busy: true,
    nvote: true,
    nrsvp: true,
    nment: true,
    ndigest: false,
  });
  const [memory, setMemory] = useStored("ov.memory", []);
  const [profile, setProfile] = useStored("ov.profile", {});
  const [readNotifs, setReadNotifs] = useStored("ov.readNotifs", []);
  const [myVotes, setMyVotes] = useStored("ov.myVotes", {});

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
      setPolls([]);
      setGroupEvents([]);
      setAvail({ members_busy: [], common_slots: [] });
      return;
    }
    try {
      const [ms, ps, evs] = await Promise.all([
        api.members(activeGroupId),
        api.polls(activeGroupId),
        api.events(activeGroupId),
      ]);
      setMembers(decorateMembers(ms, meRef.current?.email));
      setPolls(ps);
      setGroupEvents(evs);
    } catch {
      // group may have been left/removed — fall back gracefully
      setMembers([]);
      setPolls([]);
      setGroupEvents([]);
    }
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

  const vote = useCallback(
    async (pollId, yes) => {
      const out = await api.vote(pollId, yes);
      setPolls((ps) => ps.map((p) => (p.id === pollId ? { ...p, ...out } : p)));
      setMyVotes((v) => ({ ...v, [pollId]: yes ? "yes" : "no" }));
      const poll = out;
      pushActivity({
        dot: yes ? "#2A9D8F" : "#D95D39",
        pre: "You voted " + (yes ? "yes" : "no") + " on ",
        bold: poll.title,
        post: "",
      });
      if (out.auto?.action === "booked") {
        pushActivity({
          dot: "#2A9D8F",
          pre: "Poll locked — ",
          bold: `${poll.title} · ${poll.start_local}`,
          post: " booked to everyone's calendar",
        });
      }
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

  const createPollDirect = useCallback(
    async (body) => {
      const poll = await api.createPoll(activeGroupId, body);
      setPolls((ps) => [poll, ...ps]);
      pushActivity({ dot: "#D95D39", pre: "You started poll ", bold: poll.title, post: "" });
      return poll;
    },
    [activeGroupId, pushActivity]
  );

  const saveProfile = useCallback(async (patch) => {
    const updated = await api.patchMe(patch);
    setMe(updated);
    return updated;
  }, []);

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
    create_poll: "Created a poll for the group",
    get_poll_status: "Checked the poll tally against the rule",
    book_meeting: "Booked it on everyone's calendar",
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
          const touchedPolls = (res.trace || []).some((s) =>
            ["create_poll", "book_meeting", "get_poll_status"].includes(s.name)
          );
          if (touchedPolls) {
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
    const fromPolls = polls
      .filter((p) => (p.status === "approved" || p.booked) && p.start_iso)
      .map((p) => ({
        id: "poll" + p.id,
        title: p.title,
        cat: "Event",
        start: new Date(p.start_iso),
        end: new Date(p.end_iso),
        where: p.location || "—",
        link: p.event_link,
        agent: `Approved by the group — ${p.yes} yes, ${p.no} no (needed ${p.min_yes}).`,
        emails: members.map((m) => m.email),
        booked: p.booked,
      }));
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
    return [...fromPolls, ...fromBackend].sort((a, b) => a.start - b.start);
  }, [polls, groupEvents, members]);

  const tasks = useMemo(
    () =>
      groupEvents
        .filter((e) => e.kind === "task")
        .map((t) => ({
          id: t.id,
          title: t.title,
          cat: t.category || "Task",
          due: t.start_iso ? t.start_iso.slice(0, 10) : "anytime",
          where: t.location,
          done: t.done,
        })),
    [groupEvents]
  );

  const openPolls = useMemo(
    () => polls.filter((p) => p.status === "open"),
    [polls]
  );

  // ---- notifications (derived + read state) --------------------------------
  const notifs = useMemo(() => {
    const out = [];
    for (const p of openPolls) {
      if ((p.waiting_on || []).includes(me?.email))
        out.push({
          id: `vote${p.id}`,
          dot: "#D95D39",
          pre: "Your vote is needed on ",
          bold: p.title,
          post: "",
        });
    }
    for (const p of polls) {
      if (p.booked)
        out.push({
          id: `lock${p.id}`,
          dot: "#2A9D8F",
          pre: "Poll locked — ",
          bold: `${p.title} · ${p.start_local}`,
          post: "",
        });
    }
    return out;
  }, [polls, openPolls, me]);

  const unread = notifs.some((n) => !readNotifs.includes(n.id));

  const ctx = {
    me, groups, activeGroup, activeGroupId, setActiveGroupId, members, polls,
    openPolls, events, tasks, avail,
    page, setPage, view, setView, calAnchor, setCalAnchor,
    collapsed, setCollapsed, focusId, setFocusId, hoverKey, setHoverKey,
    modal, setModal, groupOpen, setGroupOpen, notifOpen, setNotifOpen,
    settingsTab, setSettingsTab,
    chatOpen, setChatOpen, chatMsgs, chatTyping, doSend,
    activity, pushActivity, rsvp, setRsvp, prefs, setPrefs, myVotes,
    memory, setMemory, profile, setProfile,
    notifs, unread, readNotifs, setReadNotifs,
    vote, createGroup, joinGroup, logout, refreshGroupData,
    createEvent, setTaskDone, removeEvent, createPollDirect, saveProfile,
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
