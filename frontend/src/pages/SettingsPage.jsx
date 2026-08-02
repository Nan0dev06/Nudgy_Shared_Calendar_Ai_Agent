import { useEffect, useState } from "react";
import { useApp } from "../ctx.js";
import {
  glass, gpill, dpill, dashPill, avatar, fieldStyle, fieldRead, fieldLabel,
  prefCard, SAGE, SLATE, TERRACOTTA, ROSE, MUSTARD, LILAC, AMBER,
} from "../theme.js";
import { StarRow, PlacePicker } from "../components/Fields.jsx";
import { api, googleConnectUrl, msConnectUrl } from "../api.js";
import { relTime } from "../dates.js";

const TABS = ["Account", "Calendars", "Memory", "Reviews", "Groups"];

export default function SettingsPage() {
  const {
    me, displayName, saveProfile, memory, setMemory,
    groups, setModal, setSettingsTab, settingsTab, logout,
    reviews, removeReview, setPage,
  } = useApp();

  const [memInput, setMemInput] = useState("");
  const [editingName, setEditingName] = useState(false);
  const [nameDraft, setNameDraft] = useState(displayName);
  const [editingTz, setEditingTz] = useState(false);
  const [tzDraft, setTzDraft] = useState(me?.timezone || "");
  const [tzErr, setTzErr] = useState("");
  const [editIdx, setEditIdx] = useState(null);
  const [editDraft, setEditDraft] = useState("");

  const saveName = async () => {
    const name = nameDraft.trim();
    if (name && name !== displayName) await saveProfile({ display_name: name });
    setEditingName(false);
  };

  // Typing a timezone in is an explicit choice — the backend stops tracking the
  // device from here on, so the app can't silently undo it later.
  const saveTz = async () => {
    const tz = tzDraft.trim();
    setTzErr("");
    if (tz && tz !== me?.timezone) {
      try {
        await saveProfile({ timezone: tz });
      } catch (e) {
        setTzErr(e.message || "Unknown timezone.");
        return;
      }
    }
    setEditingTz(false);
  };

  // ...and this hands it back. One PATCH: the timezone_auto field is applied
  // after the timezone, so it re-enables detection instead of turning it off.
  const useDeviceTz = async () => {
    setTzErr("");
    let tz = "";
    try {
      tz = Intl.DateTimeFormat().resolvedOptions().timeZone || "";
    } catch { /* ancient browser: just flip the flag back on */ }
    try {
      await saveProfile(tz ? { timezone: tz, timezone_auto: true } : { timezone_auto: true });
    } catch (e) {
      setTzErr(e.message || "Couldn't read this device's timezone.");
    }
  };

  const addMemory = () => {
    const t = memInput.trim();
    if (!t) return;
    setMemory((m) => [...m, t]);
    setMemInput("");
  };

  return (
    <div style={{ flex: 1, minHeight: 0, display: "flex", gap: 16, animation: "fadeUp .35s cubic-bezier(.4,0,.2,1)" }}>
      <div style={{ ...glass(22), width: 200, flex: "none", padding: "14px 11px", display: "flex", flexDirection: "column", gap: 3 }}>
        {TABS.map((t) => (
          <div
            key={t}
            onClick={() => setSettingsTab(t)}
            style={{
              padding: "10px 12px", borderRadius: 12, fontSize: 13,
              fontWeight: settingsTab === t ? 600 : 500, cursor: "pointer",
              color: settingsTab === t ? "#2D2D2D" : "#8c8577",
              background: settingsTab === t ? "rgba(255,253,247,.66)" : "transparent",
              transition: "all .18s",
            }}
          >
            {t}
          </div>
        ))}
        <div
          style={{ marginTop: "auto", padding: "10px 12px", borderRadius: 12, fontSize: 13, fontWeight: 500, color: "#D95D39", cursor: "pointer" }}
          onClick={logout}
        >
          Log out
        </div>
      </div>

      <div style={{ ...glass(22), flex: 1, minWidth: 0, padding: "20px 22px", display: "flex", flexDirection: "column", gap: 16, overflow: "auto" }}>
        {settingsTab === "Account" && (
          <>
            <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
              <div
                style={{
                  width: 52, height: 52, borderRadius: "50%",
                  background: "linear-gradient(160deg, #2A9D8F, #237c72)",
                  border: "2.5px solid rgba(255,253,247,.85)",
                  display: "flex", alignItems: "center", justifyContent: "center",
                  color: "#fff", fontSize: 18, fontWeight: 600,
                }}
              >
                {displayName.charAt(0).toUpperCase()}
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
                <span style={{ fontSize: 16, fontWeight: 600 }}>{displayName}</span>
                <span style={{ fontSize: 12, color: "#a09889" }}>{me?.email}</span>
              </div>
            </div>

            <div style={{ display: "grid", gridTemplateColumns: "1.3fr 1fr", gap: 14 }}>
              <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                <span style={fieldLabel}>Display name</span>
                {editingName ? (
                  <div style={{ display: "flex", gap: 8 }}>
                    <input
                      autoFocus
                      value={nameDraft}
                      onChange={(e) => setNameDraft(e.target.value)}
                      onKeyDown={(e) => e.key === "Enter" && saveName()}
                      style={fieldStyle}
                    />
                    <div className="hov-lift-sm" style={dpill(true)} onClick={saveName}>
                      Save
                    </div>
                  </div>
                ) : (
                  <div
                    style={{ ...fieldRead, cursor: "pointer" }}
                    title="Click to edit"
                    onClick={() => { setNameDraft(displayName); setEditingName(true); }}
                  >
                    {displayName} <span style={{ color: "#a49c8c", fontSize: 11 }}>· edit</span>
                  </div>
                )}
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                <span style={fieldLabel}>Timezone</span>
                {editingTz ? (
                  <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                    <div style={{ display: "flex", gap: 8 }}>
                      <input
                        autoFocus
                        placeholder="e.g. Asia/Beirut"
                        value={tzDraft}
                        onChange={(e) => setTzDraft(e.target.value)}
                        onKeyDown={(e) => e.key === "Enter" && saveTz()}
                        style={fieldStyle}
                      />
                      <div className="hov-lift-sm" style={dpill(true)} onClick={saveTz}>
                        Save
                      </div>
                    </div>
                    {tzErr && <div style={{ fontSize: 12, color: "#D95D39" }}>{tzErr}</div>}
                  </div>
                ) : (
                  <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                    <div
                      style={{ ...fieldRead, cursor: "pointer" }}
                      title="Click to edit"
                      onClick={() => { setTzDraft(me?.timezone || ""); setEditingTz(true); }}
                    >
                      {me?.timezone} <span style={{ color: "#a49c8c", fontSize: 11 }}>· edit</span>
                    </div>
                    {me?.timezone_auto ? (
                      <span style={{ fontSize: 11, color: "#a09889" }}>
                        Following this device — times move with you.
                      </span>
                    ) : (
                      <span style={{ fontSize: 11, color: "#a09889" }}>
                        Set by you ·{" "}
                        <span
                          onClick={useDeviceTz}
                          style={{ color: "#2A9D8F", cursor: "pointer", fontWeight: 600 }}
                        >
                          follow this device
                        </span>
                      </span>
                    )}
                    {tzErr && <div style={{ fontSize: 12, color: "#D95D39" }}>{tzErr}</div>}
                  </div>
                )}
              </div>
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1.3fr", gap: 14 }}>
              <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                <span style={fieldLabel}>Email</span>
                <div style={fieldRead}>{me?.email}</div>
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                <span style={fieldLabel}>Calendar</span>
                <div style={fieldRead}>
                  {me?.calendar_connected ? "Google Calendar connected" : "Not connected"}
                </div>
              </div>
            </div>

            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              <span style={fieldLabel}>Privacy</span>
              <div style={fieldRead}>
                Groupmates only ever see that you're busy — never event titles,
                places, or who else is there.
              </div>
            </div>

            <div style={{ height: 1, background: "rgba(150,142,128,.22)" }} />
            <div style={{ display: "flex", flexDirection: "column", gap: 9 }}>
              <span style={fieldLabel}>What the agent remembers about you</span>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
                {memory.slice(0, 6).map((m, i) => (
                  <div key={i} style={gpill(true)}>{m}</div>
                ))}
                <div style={dashPill(true)} onClick={() => setSettingsTab("Memory")}>
                  + add
                </div>
              </div>
            </div>
          </>
        )}

        {settingsTab === "Calendars" && <CalendarsSection />}

        {settingsTab === "Reviews" && (
          <>
            <div style={{ fontSize: 13, color: "#8c8577", marginTop: -6, lineHeight: 1.5 }}>
              Rate the places you've been. Nudgy saves these as taste memory —
              next time someone types “bhi” it knows you mean BHive, and it can
              suggest spots you actually liked.{" "}
              <span
                style={{ color: "#2B5B84", fontWeight: 600, cursor: "pointer" }}
                onClick={() => setPage("places")}
              >
                See every place's profile →
              </span>
            </div>
            <AddReview />
            {reviews.map((r, i) => (
              <div key={i} style={{ ...prefCard, alignItems: "flex-start" }}>
                <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: 4 }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                    <span style={{ fontSize: 14, fontWeight: 600 }}>{r.place}</span>
                    <StarRow value={r.stars} size={13} />
                  </div>
                  {r.text && <span style={{ fontSize: 12.5, color: "#5c564b", lineHeight: 1.45 }}>{r.text}</span>}
                  <span style={{ fontSize: 10.5, color: "#a09889" }}>{relTime(r.ts)}</span>
                </div>
                <span
                  style={{ fontSize: 12, fontWeight: 600, color: "#b08a80", cursor: "pointer", flex: "none" }}
                  onClick={() => removeReview(r)}
                >
                  Remove
                </span>
              </div>
            ))}
            {reviews.length === 0 && (
              <div style={{ fontSize: 12.5, color: "#a09889" }}>
                No reviews yet — after an outing you'll get a nudge to rate the place.
              </div>
            )}
          </>
        )}

        {settingsTab === "Memory" && (
          <>
            <div style={{ fontSize: 13, color: "#8c8577", marginTop: -6 }}>
              What the agent has learned about your group — correct anything
              that's wrong. Nudgy reads this when planning.
            </div>
            {memory.map((m, i) => (
              <div key={i} style={prefCard}>
                {editIdx === i ? (
                  <input
                    autoFocus
                    value={editDraft}
                    onChange={(e) => setEditDraft(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") {
                        setMemory((ms) => ms.map((x, j) => (j === i ? editDraft.trim() || x : x)));
                        setEditIdx(null);
                      }
                    }}
                    onBlur={() => {
                      setMemory((ms) => ms.map((x, j) => (j === i ? editDraft.trim() || x : x)));
                      setEditIdx(null);
                    }}
                    style={{ ...fieldStyle, flex: 1 }}
                  />
                ) : (
                  <span style={{ flex: 1, fontSize: 13.5 }}>{m}</span>
                )}
                <span
                  style={{ fontSize: 12, fontWeight: 600, color: "#2B5B84", cursor: "pointer" }}
                  onClick={() => { setEditIdx(i); setEditDraft(m); }}
                >
                  Edit
                </span>
                <span
                  style={{ fontSize: 12, fontWeight: 600, color: "#b08a80", cursor: "pointer" }}
                  onClick={() => setMemory((ms) => ms.filter((_, j) => j !== i))}
                >
                  Remove
                </span>
              </div>
            ))}
            <div style={{ display: "flex", gap: 10 }}>
              <input
                placeholder="Teach it something — e.g. Aya can't do Fridays"
                value={memInput}
                onChange={(e) => setMemInput(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && addMemory()}
                style={fieldStyle}
              />
              <div className="hov-lift-sm" style={dpill(true)} onClick={addMemory}>
                Add
              </div>
            </div>
          </>
        )}

        {settingsTab === "Groups" && (
          <>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
              <span style={{ fontSize: 16, fontWeight: 600 }}>Your groups</span>
              <div className="hov-lift-sm" style={dpill(true)} onClick={() => setModal({ type: "newGroup" })}>
                + New group
              </div>
            </div>
            {groups.map((g) => (
              <GroupCard key={g.id} group={g} />
            ))}
          </>
        )}

      </div>
    </div>
  );
}

// ---- connected calendars ---------------------------------------------------
const SWATCHES = [SAGE, SLATE, TERRACOTTA, ROSE, MUSTARD, LILAC, AMBER];
// A trust ladder, shown in that order. "One-way" is the stored value but never
// the label: it doesn't say WHICH way, and the whole reason the tier exists is
// that people want Nudgy to understand their calendar before they let it write
// to one. "Read only" says that; "One-way" makes them guess.
const SYNC_MODES = [
  { key: "two_way", label: "Two-way", hint: "Read this calendar and write to it" },
  { key: "one_way", label: "Read only", hint: "Read this calendar, never write to it" },
  { key: "none", label: "Off", hint: "Neither — free/busy only" },
];
const PROVIDER_LABEL = { google: "Google Calendar", microsoft: "Outlook / Microsoft" };

function ProviderMark({ provider }) {
  if (provider === "microsoft")
    return (
      <svg width="15" height="15" viewBox="0 0 23 23" style={{ flex: "none" }}>
        <path fill="#f25022" d="M1 1h10v10H1z" />
        <path fill="#7fba00" d="M12 1h10v10H12z" />
        <path fill="#00a4ef" d="M1 12h10v10H1z" />
        <path fill="#ffb900" d="M12 12h10v10H12z" />
      </svg>
    );
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" style={{ flex: "none" }}>
      <path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92a5.06 5.06 0 0 1-2.2 3.32v2.77h3.57c2.08-1.92 3.27-4.74 3.27-8.1z" />
      <path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" />
      <path fill="#FBBC05" d="M5.84 14.1c-.22-.66-.35-1.36-.35-2.1s.13-1.44.35-2.1V7.06H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.94l3.66-2.84z" />
      <path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.06l3.66 2.84c.87-2.6 3.3-4.52 6.16-4.52z" />
    </svg>
  );
}

function CalendarsSection() {
  const [cals, setCals] = useState(null); // null = loading
  const [err, setErr] = useState("");
  // Which calendar is mid-question, and which question. Both destructive
  // choices here (stop reading titles, disconnect) leave data behind that is
  // the user's to keep or bin, so neither happens on a single click.
  const [ask, setAsk] = useState(null);   // { id, kind, ...payload }
  const [syncing, setSyncing] = useState(null);

  const load = () =>
    api.calendars().then(setCals).catch((e) => setErr(e.message || "Couldn't load calendars."));

  useEffect(() => {
    load();
    // strip the ?tab=calendars&connected=… the connect redirect left on the URL
    if (window.location.search) window.history.replaceState({}, "", window.location.pathname);
  }, []);

  // optimistic patch: reflect locally, persist, reconcile with the server row
  const patch = async (id, body) => {
    setCals((cs) => cs.map((c) => (c.id === id ? { ...c, ...body } : c)));
    try {
      const updated = await api.patchCalendar(id, body);
      setCals((cs) => cs.map((c) => (c.id === id ? updated : c)));
    } catch {
      load();
    }
  };

  const makePrimary = async (id) => {
    setCals((cs) => cs.map((c) => ({ ...c, is_primary: c.id === id })));
    try {
      await api.patchCalendar(id, { is_primary: true });
    } catch {
      load();
    }
  };

  // Turning titles ON is not destructive, so it just happens. Turning them OFF
  // raises "what about the ones already here?", which only the user can answer.
  const toggleTitles = (c) => {
    if (c.read_titles) setAsk({ id: c.id, kind: "titles" });
    else patch(c.id, { read_titles: true });
  };

  const stopTitles = (id, keep) => {
    setAsk(null);
    patch(id, { read_titles: false, keep_titles: keep });
  };

  // Everything except Off reads the calendar, so only Off stops inbound — and
  // only that raises the keep-or-bin question. Two-way -> Read only withdraws
  // WRITE permission and nothing else, so it applies straight away.
  const setSyncMode = (c, mode) => {
    if (c.syncs_in && mode === "none" && c.synced_events > 0)
      setAsk({ id: c.id, kind: "syncMode", mode, count: c.synced_events });
    else patch(c.id, { sync_setting: mode });
  };

  const applySyncMode = (id, mode, keep) => {
    setAsk(null);
    patch(id, { sync_setting: mode, keep_events: keep });
  };

  const disconnect = async (id, keepEvents) => {
    setAsk(null);
    const prev = cals;
    setCals((cs) => cs.filter((c) => c.id !== id));
    try {
      await api.disconnectCalendar(id, keepEvents);
      load(); // a disconnect can promote a new primary — resync to see it
    } catch {
      setCals(prev);
    }
  };

  const syncNow = async (id) => {
    setSyncing(id);
    try {
      const res = await api.syncCalendar(id);
      setCals((cs) => cs.map((c) => (c.id === id ? res.calendar : c)));
    } catch {
      load();
    } finally {
      setSyncing(null);
    }
  };

  return (
    <>
      <div style={{ fontSize: 13, color: "#8c8577", marginTop: -6, lineHeight: 1.5 }}>
        Calendars you've connected. Nudgy reads free/busy across all of them so it
        never double-books you; new events and bookings are written to your{" "}
        <b>primary</b> one. Colors tell them apart on your calendar.
        <br />
        <b>Sync</b> sets how far Nudgy goes: <b>Two-way</b> reads that calendar
        and writes to it, <b>Read only</b> reads it and never writes — pick this
        if you want Nudgy to understand your week without touching your calendar
        — and <b>Off</b> does neither. Free/busy is read whatever you pick; it's
        how Nudgy avoids double-booking you and it carries no detail. On anything
        but Off, switch <b>Titles</b> on and your own busy blocks say what they
        are — <b>only to you</b>; everyone else keeps seeing plain busy time.
      </div>

      {err && <div style={{ fontSize: 12.5, color: "#D95D39" }}>{err}</div>}
      {cals === null && !err && (
        <div style={{ fontSize: 12.5, color: "#a09889" }}>Loading…</div>
      )}
      {cals && cals.length === 0 && (
        <div style={{ fontSize: 12.5, color: "#a09889" }}>
          No calendars connected yet. You can use Nudgy without one, or connect
          your Google/Outlook calendar below so it can see your availability.
        </div>
      )}

      {(cals || []).map((c) => (
        <div key={c.id} style={{ ...prefCard, flexDirection: "column", alignItems: "stretch", gap: 12 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 11 }}>
            <ColorSwatch value={c.color} onPick={(color) => patch(c.id, { color })} />
            <ProviderMark provider={c.provider} />
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontSize: 13.5, fontWeight: 600, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {c.external_email}
              </div>
              <div style={{ fontSize: 11, color: "#a09889" }}>{PROVIDER_LABEL[c.provider] || c.provider}</div>
            </div>
            {c.is_primary ? (
              <span style={{ ...gpill(true), background: "rgba(42,157,143,.14)", color: SAGE, cursor: "default", boxShadow: "none", border: "none" }}>
                Primary
              </span>
            ) : (
              <span
                className="hov-glass"
                style={{ ...gpill(true) }}
                onClick={() => makePrimary(c.id)}
              >
                Make primary
              </span>
            )}
          </div>

          <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
            <span style={fieldLabel}>Sync</span>
            <div style={{ display: "flex", gap: 6 }}>
              {SYNC_MODES.map((m) => {
                const on = c.sync_setting === m.key;
                return (
                  <span
                    key={m.key}
                    title={m.hint}
                    onClick={() => !on && setSyncMode(c, m.key)}
                    style={{
                      ...gpill(true),
                      cursor: on ? "default" : "pointer",
                      background: on ? "linear-gradient(160deg, #2A9D8F, #237c72)" : undefined,
                      color: on ? "#F7F2EA" : "#2D2D2D",
                      border: on ? "1px solid rgba(255,255,255,.2)" : undefined,
                    }}
                  >
                    {m.label}
                  </span>
                );
              })}
            </div>
            <span
              style={{ marginLeft: "auto", fontSize: 12, fontWeight: 600, color: "#b08a80", cursor: "pointer" }}
              onClick={() => setAsk({ id: c.id, kind: "disconnect" })}
            >
              Disconnect
            </span>
          </div>

          {/* Inbound sync: see what's actually in your busy blocks. Only a
              two-way calendar sends anything back, so with any other mode this
              row says so rather than offering a switch that does nothing. */}
          <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap", opacity: c.syncs_in ? 1 : 0.55 }}>
            <span style={fieldLabel}>Titles</span>
            <Toggle
              on={!!c.read_titles && c.syncs_in}
              disabled={!c.syncs_in}
              onClick={() => c.syncs_in && toggleTitles(c)}
            />
            <span style={{ fontSize: 11.5, color: "#a09889", flex: 1, minWidth: 180, lineHeight: 1.45 }}>
              {!c.syncs_in
                ? "Sync is off, so nothing is read from this calendar."
                : c.read_titles
                  ? "Your busy blocks show what they are — to you only. Groupmates still see plain busy time."
                  : "Off: this calendar's events show as unlabelled busy blocks, even to you."}
            </span>
            {c.syncs_in && (
              <span
                className="hov-glass"
                style={{ ...gpill(true), opacity: syncing === c.id ? 0.55 : 1 }}
                onClick={() => syncing !== c.id && syncNow(c.id)}
              >
                {syncing === c.id ? "Syncing…" : "Sync now"}
              </span>
            )}
          </div>

          {c.syncs_in && <SyncStatus calendars={c.calendars} />}

          {ask?.id === c.id && ask.kind === "titles" && (
            <ChoicePrompt
              question="Stop reading titles from this calendar?"
              detail="New events will come in unlabelled. What should happen to the titles already pulled in? The times stay either way — they're what makes a busy block."
              options={[
                { label: "Remove them", tone: "danger", onPick: () => stopTitles(c.id, false) },
                { label: "Keep them", onPick: () => stopTitles(c.id, true) },
              ]}
              onCancel={() => setAsk(null)}
            />
          )}
          {ask?.id === c.id && ask.kind === "syncMode" && (
            <ChoicePrompt
              question="Turn sync off for this calendar?"
              detail={
                "Nudgy stops reading this calendar and stops writing to it. " +
                `The ${ask.count} event${ask.count === 1 ? "" : "s"} already synced can stay as a frozen copy. ` +
                "Either way it still counts as busy time, so nobody double-books you."
              }
              options={[
                { label: "Delete them", tone: "danger", onPick: () => applySyncMode(c.id, ask.mode, false) },
                { label: "Keep them", onPick: () => applySyncMode(c.id, ask.mode, true) },
              ]}
              onCancel={() => setAsk(null)}
            />
          )}
          {ask?.id === c.id && ask.kind === "disconnect" && (
            <ChoicePrompt
              question={`Disconnect ${c.external_email}?`}
              detail="Nudgy stops reading this calendar. Events already synced from it can stay as a frozen copy — they'll show on your calendar but won't update any more."
              options={[
                { label: "Delete them too", tone: "danger", onPick: () => disconnect(c.id, false) },
                { label: "Keep the events", onPick: () => disconnect(c.id, true) },
              ]}
              onCancel={() => setAsk(null)}
            />
          )}
        </div>
      ))}

      <div style={{ height: 1, background: "rgba(150,142,128,.22)" }} />
      <span style={fieldLabel}>Connect another calendar</span>
      <div style={{ display: "flex", gap: 10 }}>
        <div className="hov-glass" style={{ ...gpill(false) }} onClick={() => (window.location.href = googleConnectUrl)}>
          <ProviderMark provider="google" /> Google
        </div>
        <div className="hov-glass" style={{ ...gpill(false) }} onClick={() => (window.location.href = msConnectUrl)}>
          <ProviderMark provider="microsoft" /> Outlook
        </div>
      </div>
    </>
  );
}

function Toggle({ on, onClick, disabled = false }) {
  return (
    <div
      onClick={disabled ? undefined : onClick}
      role="switch"
      aria-checked={on}
      aria-disabled={disabled}
      style={{
        width: 38, height: 22, borderRadius: 999, flex: "none",
        cursor: disabled ? "not-allowed" : "pointer",
        padding: 2, transition: "all .2s",
        background: on ? "linear-gradient(160deg, #2A9D8F, #237c72)" : "rgba(150,142,128,.3)",
      }}
    >
      <div style={{
        width: 18, height: 18, borderRadius: "50%", background: "#FFFDF7",
        transform: `translateX(${on ? 16 : 0}px)`, transition: "transform .2s",
        boxShadow: "0 1px 3px rgba(96,78,54,.25)",
      }} />
    </div>
  );
}

// Per-calendar sync state. One connected account can carry several calendars
// (personal, uni, work) that fail independently, so a stale token on one says so
// by name instead of making the whole connection look broken.
function SyncStatus({ calendars }) {
  if (!calendars || calendars.length === 0) return null;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      {calendars.map((s) => (
        <div key={s.calendar_id} style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 11.5 }}>
          <div style={{
            width: 6, height: 6, borderRadius: "50%", flex: "none",
            background: s.error ? "#D95D39" : s.synced_at ? SAGE : "rgba(150,142,128,.5)",
          }} />
          <span style={{ color: "#8c8577", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            {s.name || s.calendar_id || "Default calendar"}
          </span>
          <span style={{ marginLeft: "auto", color: s.error ? "#D95D39" : "#a09889", textAlign: "right" }}>
            {s.error
              ? "Couldn't sync — try reconnecting"
              : s.synced_at
                ? `synced ${relTime(new Date(s.synced_at).getTime())} ago`
                : "waiting for first sync"}
          </span>
        </div>
      ))}
    </div>
  );
}

// A two-way question asked in place, rather than a confirm() that only offers
// yes/no. Both uses here are "this leaves data behind — keep it or bin it?",
// which has no safe default the app is entitled to pick on the user's behalf.
function ChoicePrompt({ question, detail, options, onCancel }) {
  return (
    <div style={{
      borderRadius: 14, padding: "12px 14px", display: "flex",
      flexDirection: "column", gap: 9,
      background: "rgba(220,167,68,.1)", border: "1px solid rgba(220,167,68,.4)",
    }}>
      <div style={{ fontSize: 13, fontWeight: 600 }}>{question}</div>
      <div style={{ fontSize: 11.5, color: "#8c8577", lineHeight: 1.5 }}>{detail}</div>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        {options.map((o) => (
          <span
            key={o.label}
            className="hov-lift-sm"
            onClick={o.onPick}
            style={{
              ...gpill(true),
              color: o.tone === "danger" ? "#b08a80" : "#2D2D2D",
              fontWeight: 600,
            }}
          >
            {o.label}
          </span>
        ))}
        <span
          onClick={onCancel}
          style={{ marginLeft: "auto", alignSelf: "center", fontSize: 12, color: "#a09889", cursor: "pointer" }}
        >
          Cancel
        </span>
      </div>
    </div>
  );
}

function ColorSwatch({ value, onPick }) {
  const [open, setOpen] = useState(false);
  return (
    <div style={{ position: "relative", flex: "none" }}>
      <div
        onClick={() => setOpen((o) => !o)}
        title="Pick a color"
        style={{
          width: 20, height: 20, borderRadius: 7, cursor: "pointer",
          background: value || "rgba(150,142,128,.35)",
          border: "2px solid rgba(255,253,247,.9)",
          boxShadow: "0 1px 3px rgba(96,78,54,.25)",
        }}
      />
      {open && (
        <>
          <div style={{ position: "fixed", inset: 0, zIndex: 30 }} onClick={() => setOpen(false)} />
          <div
            style={{
              position: "absolute", top: "calc(100% + 6px)", left: 0, zIndex: 40,
              display: "flex", gap: 6, padding: 8, borderRadius: 12,
              background: "rgba(255,253,247,.92)", backdropFilter: "blur(20px)",
              border: "1px solid rgba(255,255,255,.8)", boxShadow: "0 12px 30px rgba(45,45,45,.18)",
            }}
          >
            {SWATCHES.map((s) => (
              <div
                key={s}
                onClick={() => { onPick(s); setOpen(false); }}
                style={{
                  width: 20, height: 20, borderRadius: 6, background: s, cursor: "pointer",
                  border: value === s ? "2px solid #2D2D2D" : "2px solid transparent",
                }}
              />
            ))}
          </div>
        </>
      )}
    </div>
  );
}

// ---- one group's card: rename, invite code, members, leave/delete ----------
function GroupCard({ group }) {
  const {
    members, me, activeGroupId, setActiveGroupId, setModal,
    renameGroup, regenerateCode, deleteGroup, leaveGroup, removeMember,
  } = useApp();
  const [editing, setEditing] = useState(false);
  const [nameDraft, setNameDraft] = useState(group.name);
  const [busy, setBusy] = useState(false);
  const isActive = group.id === activeGroupId;
  const owner = group.is_owner;

  const saveName = async () => {
    const n = nameDraft.trim();
    if (n && n !== group.name) await renameGroup(group.id, n);
    setEditing(false);
  };
  const roll = async () => {
    if (window.confirm("Roll a new invite code? The current link stops working."))
      await regenerateCode(group.id);
  };
  const guarded = (fn) => async () => {
    setBusy(true);
    try { await fn(); } finally { setBusy(false); }
  };
  const del = guarded(async () => {
    if (window.confirm(`Delete “${group.name}”? Its plans and events are removed for everyone. This can't be undone.`))
      await deleteGroup(group.id);
  });
  const leave = guarded(async () => {
    if (window.confirm(`Leave “${group.name}”?`)) await leaveGroup(group.id);
  });

  return (
    <div style={{ ...prefCard, flexDirection: "column", alignItems: "stretch", gap: 10 }}>
      <div style={{ display: "flex", alignItems: "flex-start", gap: 10 }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          {editing ? (
            <div style={{ display: "flex", gap: 8 }}>
              <input
                autoFocus value={nameDraft}
                onChange={(e) => setNameDraft(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && saveName()}
                style={fieldStyle}
              />
              <div className="hov-lift-sm" style={dpill(true)} onClick={saveName}>Save</div>
            </div>
          ) : (
            <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
              <span style={{ fontSize: 14.5, fontWeight: 600 }}>{group.name}</span>
              {isActive && <span style={{ fontSize: 10.5, fontWeight: 600, color: SAGE }}>active</span>}
              {owner && (
                <span
                  style={{ fontSize: 11, color: "#a49c8c", cursor: "pointer" }}
                  onClick={() => { setNameDraft(group.name); setEditing(true); }}
                >
                  · rename
                </span>
              )}
            </div>
          )}
          <div style={{ fontSize: 12, color: "#8c8577", marginTop: 3 }}>
            Invite code: <b style={{ letterSpacing: ".08em" }}>{group.invite_code}</b>
            {owner && (
              <span
                style={{ marginLeft: 8, color: "#2B5B84", fontWeight: 600, cursor: "pointer" }}
                onClick={roll}
              >
                regenerate
              </span>
            )}
          </div>
        </div>
        <CopyChip text={group.invite_code} />
      </div>

      {isActive && members.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 5 }}>
          {members.map((m) => (
            <div key={m.email} style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12.5 }}>
              <div style={avatar(m.color, 18)} />
              <span style={{ flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {m.name || m.email}
                {m.email === me?.email ? " (you)" : ""}
                {m.isOwner ? " · owner" : ""}
              </span>
              {owner && !m.isOwner && m.email !== me?.email && m.id != null && (
                <span
                  style={{ fontSize: 11.5, color: "#b08a80", fontWeight: 600, cursor: "pointer", flex: "none" }}
                  onClick={() =>
                    window.confirm(`Remove ${m.name || m.email} from ${group.name}?`) &&
                    removeMember(group.id, m.id)
                  }
                >
                  remove
                </span>
              )}
            </div>
          ))}
        </div>
      )}

      <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
        {isActive ? (
          <span className="hov-glass" style={gpill(true)} onClick={() => setModal({ type: "invite" })}>
            Invite people
          </span>
        ) : (
          <span className="hov-glass" style={gpill(true)} onClick={() => setActiveGroupId(group.id)}>
            Switch to this
          </span>
        )}
        <span
          style={{ marginLeft: "auto", fontSize: 12, fontWeight: 600, color: "#b08a80", cursor: busy ? "default" : "pointer", opacity: busy ? 0.5 : 1 }}
          onClick={leave}
        >
          Leave
        </span>
        {owner && (
          <span
            style={{ fontSize: 12, fontWeight: 600, color: "#c0392b", cursor: busy ? "default" : "pointer", opacity: busy ? 0.5 : 1 }}
            onClick={del}
          >
            Delete
          </span>
        )}
      </div>
    </div>
  );
}

function AddReview() {
  const { addReview, reviews } = useApp();
  const [place, setPlace] = useState("");
  const [stars, setStars] = useState(0);
  const [text, setText] = useState("");

  const save = () => {
    if (!place.trim() || !stars) return;
    addReview({ place: place.trim(), stars, text: text.trim() });
    setPlace("");
    setStars(0);
    setText("");
  };

  return (
    <div style={{ ...prefCard, flexDirection: "column", alignItems: "stretch", gap: 10 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
        <div style={{ flex: 1 }}>
          <PlacePicker
            value={place}
            onChange={setPlace}
            placeholder="Which place?"
            reviewedPlaces={reviews.map((r) => r.place)}
          />
        </div>
        <StarRow value={stars} onChange={setStars} size={20} />
      </div>
      <div style={{ display: "flex", gap: 10 }}>
        <input
          placeholder="A few words — what was it like?"
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && save()}
          style={fieldStyle}
        />
        <div
          className="hov-lift-sm"
          style={{ ...dpill(true), opacity: place.trim() && stars ? 1 : 0.5 }}
          onClick={save}
        >
          Save review
        </div>
      </div>
    </div>
  );
}

function CopyChip({ text }) {
  const [copied, setCopied] = useState(false);
  return (
    <div
      className="hov-glass"
      style={gpill(true)}
      onClick={() => {
        navigator.clipboard?.writeText(text);
        setCopied(true);
        setTimeout(() => setCopied(false), 1500);
      }}
    >
      {copied ? "Copied ✓" : "Copy code"}
    </div>
  );
}
