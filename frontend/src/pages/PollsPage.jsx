import { useState } from "react";
import { useApp } from "../ctx.js";
import { glass, gpill, dpill, sagePill, agentBox } from "../theme.js";
import { CheckIcon, PinIcon, ClockIcon } from "../Icons.jsx";
import { nameFromEmail } from "../people.js";

// The poll surface, rewritten for the 2026-08-01 engine
// (docs/poll-edit-redesign.md §1). What changed, and why the UI looks different:
//
// EVERY CANDIDATE TIME IS ANSWERABLE AT ONCE. There is no active round and no
// queue, so the card shows a GRID of times rather than one question at a time.
// A member answers them in any order, and a new time appearing disturbs nothing
// they already said.
//
// THREE ANSWERS, not two. "If needed" is the one that decides most polls — it
// separates "impossible" from "inconvenient" — so it gets equal billing rather
// than hiding behind a menu.
//
// THE SPOTLIGHT replaces "try the next time". It says "we're leaning toward
// this", breaks ties at the deadline, and RESETS NOTHING. The old move made
// every vote already cast irrelevant, which is why hosts avoided it; this one
// is safe enough to say so on the button.
//
// THE BAR is a rule by default ("all 4 of you") and a number only when a human
// typed one ("3 people"). Those are different sentences and the UI must not
// blur them: "3 of 4" would misdescribe a rule that needs those specific four.

const ANSWERS = [
  ["yes", "Yes", "#2A9D8F"],
  ["if_needed", "If needed", "#DCA744"],
  ["no", "Can't", "#D95D39"],
];

// host_box fields are lists of emails — show a count plus the names
const tallyLine = (list, word) => {
  const l = list || [];
  return l.length
    ? `${l.length} ${word} (${l.map(nameFromEmail).join(", ")})`
    : `0 ${word}`;
};

// "closes in 4 hours" — coarse on purpose. A live-ticking countdown would just
// be noise for something measured in days, and the list already refreshes.
const closesLabel = (iso) => {
  if (!iso) return null;
  const ms = new Date(iso).getTime() - Date.now();
  if (ms <= 0) return "voting closed";
  const mins = Math.round(ms / 60000);
  if (mins < 60) return `closes in ${mins} min`;
  const hrs = Math.round(mins / 60);
  if (hrs < 48) return `closes in ${hrs} ${hrs === 1 ? "hour" : "hours"}`;
  return `closes in ${Math.round(hrs / 24)} days`;
};

// How this poll can finish without anybody pressing a button. Two regimes, and
// they read differently on purpose (see the header comment).
const barLabel = (p) =>
  p.requires_all_members
    ? `all ${p.minimum} of you`
    : `${p.minimum} ${p.minimum === 1 ? "person" : "people"}`;

const MODE_BLURB = {
  quick: "One time on the table — does it work for you?",
  pick_a_time: "Answer every time you can. “If needed” still counts.",
  float: "Say if you're in; times get added as they come up.",
};

// how long from now the host is giving the group, in hours
const EXTENSIONS = [
  [24, "a day"],
  [72, "3 days"],
  [168, "a week"],
];

export default function PollsPage() {
  const {
    plans, activeGroup, members, voteInterest, voteTime, setModal, setPage, setView,
    removePlan, updatePlanSettings, sharePlanLink, unsharePlanLink,
    lockInPlan, spotlightTime, removePlanTime,
  } = useApp();

  // How many people the all-members rule actually means. `plan.minimum` already
  // equals this under the rule, but once a host types a count the plan stops
  // carrying the group size — and the bar picker still has to offer it.
  const memberCount = members.length;

  // A host move can legitimately fail — nobody said yes (400), or the calendar
  // refused (502). Both are things the host must SEE; the old chat route buried
  // them in the agent's reply. { [planId]: message }
  const [hostErr, setHostErr] = useState({});
  const [hostBusy, setHostBusy] = useState(null);
  // which round is mid-request, so one card's buttons dim without freezing the
  // whole page: `${planId}:${roundId}`
  const [voting, setVoting] = useState(null);

  const hostMove = async (planId, fn) => {
    if (hostBusy) return;
    setHostBusy(planId);
    setHostErr((e) => ({ ...e, [planId]: null }));
    try {
      await fn();
    } catch (e) {
      setHostErr((prev) => ({ ...prev, [planId]: e.message || "That didn't go through." }));
    } finally {
      setHostBusy(null);
    }
  };

  const answer = async (planId, roundId, value) => {
    const key = `${planId}:${roundId}`;
    if (voting) return;
    setVoting(key);
    try {
      await voteTime(planId, roundId, value);
    } catch {
      /* voteTime resyncs and leaves an activity trail of its own */
    } finally {
      setVoting((v) => (v === key ? null : v));
    }
  };

  // which poll (if any) is showing its "Delete? Yes / Cancel" inline confirm —
  // delete is permanent, so never one-click
  const [confirmingDelete, setConfirmingDelete] = useState(null);
  // per-poll "Copied!" flash after the share link goes to the clipboard
  const [copied, setCopied] = useState(null);

  const copyShareLink = async (planId, url) => {
    const link = url || (await sharePlanLink(planId));
    try {
      await navigator.clipboard.writeText(link);
      setCopied(planId);
      setTimeout(() => setCopied((c) => (c === planId ? null : c)), 1800);
    } catch {
      // clipboard blocked (insecure origin, denied permission) — the link is
      // live either way, so show it rather than failing silently
      window.prompt("Copy this link:", link);
    }
  };

  const choice = (label, sub, color, onClick, selected) => (
    <div
      className="hov-lift-sm"
      onClick={onClick}
      style={{
        flex: 1, borderRadius: 16, padding: "12px 15px", display: "flex",
        flexDirection: "column", gap: 3, cursor: "pointer", transition: "all .2s",
        background: selected
          ? `linear-gradient(135deg, color-mix(in srgb, ${color} 16%, rgba(255,251,244,.6)), rgba(250,242,231,.4))`
          : "rgba(255,253,247,.45)",
        border: selected ? `1.6px solid ${color}` : "1px solid rgba(255,255,255,.6)",
      }}
    >
      <span style={{ fontSize: 14, fontWeight: 600, color: selected ? color : "#2D2D2D" }}>{label}</span>
      {sub && <span style={{ fontSize: 11.5, color: "#a09889" }}>{sub}</span>}
    </div>
  );

  const statusChip = (p) => {
    const map = {
      open: ["#D95D39", "Poll · open"],
      booked: ["#2A9D8F", "Locked in"],
      expired: ["#b8968c", "Voting closed"],
    };
    const [c, label] = map[p.status] || ["#a09889", p.status];
    return (
      <span style={{ fontSize: 10.5, fontWeight: 600, letterSpacing: ".07em", textTransform: "uppercase", color: c }}>
        {label}
      </span>
    );
  };

  // ---- one candidate time, with its standing and this member's answer ------
  const timeRow = (p, t) => {
    const mine = t.my_answer;
    const canAnswer = p.voting_open && p.ballot?.stage !== "out";
    const busy = voting === `${p.id}:${t.round_id}`;
    return (
      <div
        key={t.round_id}
        style={{
          borderRadius: 16, padding: "12px 14px",
          display: "flex", flexDirection: "column", gap: 9,
          background: t.spotlit ? "rgba(42,157,143,.07)" : "rgba(255,253,247,.45)",
          border: t.spotlit
            ? "1.4px solid rgba(42,157,143,.45)"
            : "1px solid rgba(255,255,255,.6)",
          opacity: busy ? 0.6 : 1, transition: "opacity .15s",
        }}
      >
        <div style={{ display: "flex", alignItems: "baseline", gap: 9, flexWrap: "wrap" }}>
          <span style={{ fontSize: 14, fontWeight: 600 }}>{t.label}</span>
          {t.spotlit && (
            <span
              title="The host is leaning toward this one. It breaks a tie — it doesn't outrank a count, and it reset nobody's vote."
              style={{ fontSize: 10.5, fontWeight: 600, letterSpacing: ".06em", textTransform: "uppercase", color: "#2A9D8F" }}
            >
              ★ leaning toward
            </span>
          )}
          {t.qualifies && !t.spotlit && (
            <span
              title={`Enough people can make this — it can book itself once everyone has answered.`}
              style={{ fontSize: 10.5, fontWeight: 600, letterSpacing: ".06em", textTransform: "uppercase", color: "#2A9D8F" }}
            >
              ✓ clears the bar
            </span>
          )}
          <span style={{ marginLeft: "auto", display: "inline-flex", gap: 9, alignItems: "center" }}>
            {t.suggested_by && (
              <span style={{ fontSize: 11, color: "#a09889" }}>
                by {nameFromEmail(t.suggested_by)}
              </span>
            )}
            {t.can_remove && (
              <span
                className="hov-lift-sm"
                title="Take back the time you suggested"
                style={{ cursor: "pointer", fontSize: 11, color: "#b8968c" }}
                onClick={() => hostMove(p.id, () => removePlanTime(p.id, t.round_id))}
              >
                Remove
              </span>
            )}
          </span>
        </div>

        <div style={{ fontSize: 11.5, color: "#8c8577" }}>
          <span style={{ color: "#2A9D8F", fontWeight: 600 }}>{t.yes} yes</span>
          {t.if_needed > 0 && <> · <span style={{ color: "#DCA744", fontWeight: 600 }}>{t.if_needed} if needed</span></>}
          {t.no > 0 && <> · {t.no} can't</>}
          {t.waiting > 0 && <> · {t.waiting} yet to answer</>}
          {t.guest_yes > 0 && <> · +{t.guest_yes} guest{t.guest_yes === 1 ? "" : "s"}</>}
        </div>

        {canAnswer && (
          <div style={{ display: "flex", gap: 7 }}>
            {ANSWERS.map(([value, label, color]) => (
              <div
                key={value}
                className="hov-lift-sm"
                onClick={() => answer(p.id, t.round_id, value)}
                style={{
                  flex: 1, textAlign: "center", borderRadius: 12, padding: "7px 10px",
                  fontSize: 12, fontWeight: 600, cursor: "pointer", transition: "all .2s",
                  color: mine === value ? "#fff" : color,
                  background: mine === value ? color : "rgba(255,253,247,.6)",
                  border: mine === value ? `1.4px solid ${color}`
                    : `1px solid color-mix(in srgb, ${color} 30%, transparent)`,
                }}
              >
                {label}
              </div>
            ))}
          </div>
        )}

        {/* host moves live on the time they act on, not in a separate panel —
            "lock in" has to name a time, and naming it here is unambiguous */}
        {p.is_host && (p.status === "open" || p.status === "expired") && !t.booked && (
          <div style={{ display: "flex", gap: 7, flexWrap: "wrap" }}>
            <div
              className="hov-lift-sm"
              style={{ ...sagePill(true), padding: "4px 11px", fontSize: 11, opacity: hostBusy === p.id ? 0.55 : 1 }}
              onClick={() => hostMove(p.id, () => lockInPlan(p.id, t.round_id))}
            >
              {hostBusy === p.id ? "Booking…" : "Lock this in"}
            </div>
            {p.voting_open && (
              <div
                className="hov-glass"
                style={{ ...gpill(true), padding: "4px 11px", fontSize: 11, opacity: hostBusy === p.id ? 0.55 : 1 }}
                title="Say the group's leaning this way. Nobody's vote is reset and you can move it back."
                onClick={() =>
                  hostMove(p.id, () => spotlightTime(p.id, t.spotlit ? null : t.round_id))
                }
              >
                {t.spotlit ? "Stop leaning" : "Lean toward this"}
              </div>
            )}
          </div>
        )}
      </div>
    );
  };

  return (
    <div style={{ flex: 1, minHeight: 0, overflow: "auto", display: "flex", flexDirection: "column", gap: 18, animation: "fadeUp .35s cubic-bezier(.4,0,.2,1)" }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", width: "min(640px, 100%)" }}>
        <span style={{ fontSize: 13, color: "#8c8577" }}>
          {plans.length
            ? "Answer every time that works — the best one books itself."
            : "No polls yet — ask who's in, or propose a full plan."}
        </span>
        <div className="hov-lift-sm" style={dpill(true)} onClick={() => setModal({ type: "newPoll" })}>
          + New poll
        </div>
      </div>

      {plans.map((p) => {
        const b = p.ballot || {};
        const times = p.times || [];
        const bookedRound = times.find((t) => t.booked);
        const hb = p.host_box;
        // any member may put a time up, so this is not gated on is_host
        const suggestTimes = () =>
          setModal({
            type: "newPoll",
            proposeChange: {
              planId: p.id, title: p.title, location: p.location || "",
              skipToTimes: true,
            },
          });
        return (
          <div
            key={p.id}
            id={`plan-${p.id}`}
            style={{
              ...glass(24), width: "min(640px, 100%)", padding: "20px 22px",
              display: "flex", flexDirection: "column", gap: 14, flex: "none",
            }}
          >
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
              {statusChip(p)}
              <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
                <span style={{ fontSize: 12, color: "#a09889" }}>
                  by {p.is_host ? "you" : nameFromEmail(p.host || "")}
                </span>
                {p.is_host && (confirmingDelete === p.id ? (
                  <span style={{ display: "inline-flex", alignItems: "center", gap: 9, fontSize: 12 }}>
                    <span style={{ color: "#a09889" }}>Delete?</span>
                    <span
                      className="hov-lift-sm"
                      style={{ cursor: "pointer", color: "#D95D39", fontWeight: 600 }}
                      onClick={async () => {
                        try { await removePlan(p.id); } catch { /* stays on screen */ }
                        setConfirmingDelete(null);
                      }}
                    >
                      Yes
                    </span>
                    <span
                      className="hov-lift-sm"
                      style={{ cursor: "pointer", color: "#8c8577" }}
                      onClick={() => setConfirmingDelete(null)}
                    >
                      Cancel
                    </span>
                  </span>
                ) : (
                  <span
                    className="hov-lift-sm"
                    title="Delete this poll"
                    style={{ cursor: "pointer", fontSize: 12, color: "#b8968c" }}
                    onClick={() => setConfirmingDelete(p.id)}
                  >
                    Delete
                  </span>
                ))}
              </div>
            </div>

            <div>
              <div style={{ fontSize: 18, fontWeight: 600 }}>{p.title}</div>
              <div style={{ display: "flex", alignItems: "center", gap: 14, marginTop: 5, fontSize: 12.5, color: "#8c8577", flexWrap: "wrap" }}>
                {p.location && (
                  <span style={{ display: "inline-flex", alignItems: "center", gap: 5 }}>
                    <PinIcon size={13} /> {p.location}
                  </span>
                )}
                <span style={{ display: "inline-flex", alignItems: "center", gap: 5 }}>
                  <ClockIcon size={13} /> {p.day}
                </span>
                {p.status === "open" && (
                  <span
                    title="This poll books itself when a time reaches this — and only ever for the people who said they can make it."
                    style={{ fontSize: 11.5, fontWeight: 600, color: "#2B5B84" }}
                  >
                    books at {barLabel(p)}
                  </span>
                )}
                {p.guest_count > 0 && (
                  <span style={{ fontSize: 11.5, color: "#a09889" }}>
                    +{p.guest_count} by link
                  </span>
                )}
                {p.deadline_iso && p.status === "open" && (
                  <span style={{ fontSize: 11.5, fontWeight: 600, color: "#D95D39" }}>
                    {closesLabel(p.deadline_iso)}
                  </span>
                )}
              </div>
            </div>

            {/* ---- my ballot -------------------------------------------- */}
            {p.status === "open" && b.stage === "interest" && (
              <>
                <div style={{ fontSize: 13, fontWeight: 600 }}>Are you in?</div>
                <div style={{ display: "flex", gap: 11 }}>
                  {choice("I'm in", "you'll be asked about times as they go up", "#2A9D8F", () => voteInterest(p.id, true).catch(() => {}))}
                  {choice("Not this time", null, "#D95D39", () => voteInterest(p.id, false).catch(() => {}))}
                </div>
              </>
            )}

            {p.status === "open" && b.stage === "out" && (
              <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
                <span style={{ fontSize: 12.5, color: "#8c8577", lineHeight: 1.5 }}>{b.note}</span>
                <span
                  className="hov-row"
                  style={{ fontSize: 12, fontWeight: 600, color: "#2B5B84", cursor: "pointer" }}
                  onClick={() => voteInterest(p.id, true).catch(() => {})}
                >
                  Actually, I'm in →
                </span>
              </div>
            )}

            {b.note && b.stage !== "out" && (
              <div style={{ fontSize: 12.5, color: "#8c8577", lineHeight: 1.5 }}>
                {b.note}
                {b.stage === "time" && times.length > 1 && (
                  <span style={{ color: "#a09889" }}> {MODE_BLURB[p.mode] || ""}</span>
                )}
              </div>
            )}

            {/* ---- the candidate times, all answerable at once ---------- */}
            {times.length > 0 && p.status !== "booked" && (
              <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                <span style={{ fontSize: 10.5, fontWeight: 600, letterSpacing: ".07em", textTransform: "uppercase", color: "#a49c8c" }}>
                  {times.length === 1 ? "The time" : `${times.length} times on the table`}
                </span>
                {times.map((t) => timeRow(p, t))}
              </div>
            )}

            {/* Members propose, the host decides — putting a time up is open to
                everyone, and it never disturbs a vote already cast. Gone once
                voting closes: a new time nobody can answer helps no one. */}
            {p.voting_open && b.stage !== "out" && times.length < 8 && (
              <div
                className="hov-glass"
                style={{ ...gpill(true), alignSelf: "flex-start", padding: "5px 13px", fontSize: 11.5 }}
                onClick={suggestTimes}
              >
                {times.length ? "+ Suggest another time" : "+ Put a time up"}
              </div>
            )}

            {hostErr[p.id] && (
              <div style={{ fontSize: 12, color: "#D95D39", lineHeight: 1.5 }}>
                {hostErr[p.id]}
              </div>
            )}

            {/* ---- host's decision box ---------------------------------- */}
            {/* stays up on an expired poll: the deadline closed VOTING, the
                host still decides what to do with the votes that landed */}
            {hb && (p.status === "open" || p.status === "expired") && (
              <div style={agentBox}>
                <span style={{ fontSize: 12, fontWeight: 600, color: "#2A9D8F" }}>
                  Your host box
                </span>
                {p.asks_interest && (
                  <span style={{ fontSize: 12.5, lineHeight: 1.5, color: "#5c564b" }}>
                    {tallyLine(hb.interested, "in")} · {tallyLine(hb.not_interested, "out")} ·{" "}
                    {(hb.no_answer || []).length
                      ? `waiting on ${(hb.no_answer || []).map(nameFromEmail).join(", ")}`
                      : "everyone answered"}
                  </span>
                )}
                {hb.note && (
                  <span style={{ fontSize: 12, lineHeight: 1.5, color: "#8c8577" }}>{hb.note}</span>
                )}

                {/* ---- inviting people who aren't in the app ------------ */}
                <div style={{ display: "flex", flexDirection: "column", gap: 7, marginTop: 8, paddingTop: 10, borderTop: "1px solid rgba(160,152,137,.16)" }}>
                  <span style={{ fontSize: 11, color: "#a09889", lineHeight: 1.5 }}>
                    {p.share_url
                      ? "Anyone with the link can vote — no account needed. Their answers show up here as guests."
                      : "Want someone outside the group in on this? Share a link they can vote on without signing up."}
                  </span>
                  <div style={{ display: "flex", gap: 7, flexWrap: "wrap", alignItems: "center" }}>
                    <div
                      className="hov-glass"
                      style={{ ...gpill(true), padding: "4px 11px", fontSize: 11 }}
                      onClick={() => copyShareLink(p.id, p.share_url)}
                    >
                      {copied === p.id ? "Copied!" : p.share_url ? "Copy vote link" : "Get a vote link"}
                    </div>
                    {p.share_url && (
                      <>
                        <div
                          className="hov-glass"
                          style={{ ...gpill(true), padding: "4px 11px", fontSize: 11 }}
                          title="Mint a new link — every copy of the old one stops working"
                          onClick={() => sharePlanLink(p.id, true).catch(() => {})}
                        >
                          New link
                        </div>
                        <div
                          className="hov-glass"
                          style={{ ...gpill(true), padding: "4px 11px", fontSize: 11 }}
                          title="Turn the link off. Votes already cast stay."
                          onClick={() => unsharePlanLink(p.id).catch(() => {})}
                        >
                          Turn off
                        </div>
                      </>
                    )}
                  </div>
                </div>

                {/* ---- how this poll finishes on its own ---------------- */}
                <div style={{ display: "flex", flexDirection: "column", gap: 7, marginTop: 8, paddingTop: 10, borderTop: "1px solid rgba(160,152,137,.16)" }}>
                  <span style={{ fontSize: 11, color: "#a09889", lineHeight: 1.5 }}>
                    {p.status === "expired"
                      ? "Voting closed and nothing reached the bar. Give the group more time, drop the bar, or lock in what you have."
                      : p.deadline_iso
                        ? `Voting ${closesLabel(p.deadline_iso)} — Nudgy nudges whoever hasn't answered, then books the best time that reaches ${barLabel(p)}.`
                        : `No deadline — this poll waits for you. It still books itself the moment everyone has answered and a time reaches ${barLabel(p)}.`}
                  </span>
                  <div style={{ display: "flex", gap: 7, flexWrap: "wrap", alignItems: "center" }}>
                    {EXTENSIONS.map(([hours, label]) => (
                      <div
                        key={hours}
                        className="hov-glass"
                        style={{ ...gpill(true), padding: "4px 11px", fontSize: 11 }}
                        onClick={() =>
                          updatePlanSettings(p.id, {
                            // extend from the existing deadline when it's still
                            // ahead, so "+ a day" adds a day rather than
                            // silently shortening a longer window
                            deadline_iso: new Date(
                              Math.max(Date.now(), new Date(p.deadline_iso || 0).getTime())
                              + hours * 3600e3
                            ).toISOString(),
                          }).catch(() => {})
                        }
                      >
                        {p.deadline_iso ? `+ ${label}` : `Close in ${label}`}
                      </div>
                    ))}
                    {p.deadline_iso && p.status !== "expired" && (
                      <div
                        className="hov-glass"
                        style={{ ...gpill(true), padding: "4px 11px", fontSize: 11 }}
                        onClick={() => updatePlanSettings(p.id, { deadline_iso: null }).catch(() => {})}
                      >
                        No deadline
                      </div>
                    )}
                  </div>
                  {/* Lowering the bar is the other way back from expired, and
                      the server re-checks convergence the moment it changes.
                      Note there is no way back UP to the all-members RULE: the
                      API only takes a number, and a count of N is not the same
                      promise as "those specific N people". Say so rather than
                      offering a button that would quietly weaken the bar. */}
                  <div style={{ display: "flex", gap: 7, flexWrap: "wrap", alignItems: "center" }}>
                    <span style={{ fontSize: 11, color: "#a09889" }}>Books at</span>
                    <span
                      title="Everyone with an account in this group — and guests can't stand in for a member under this rule."
                      style={{
                        ...(p.requires_all_members ? dpill(true) : gpill(true)),
                        padding: "4px 11px", fontSize: 11,
                        cursor: "default", opacity: p.requires_all_members ? 1 : 0.6,
                      }}
                    >
                      {p.requires_all_members ? `✓ everyone (${memberCount})` : `everyone (${memberCount})`}
                    </span>
                    {Array.from({ length: Math.max(0, memberCount - 1) }, (_, i) => i + 1).map((n) => (
                      <div
                        key={n}
                        className="hov-glass"
                        style={{
                          ...(!p.requires_all_members && p.minimum === n ? dpill(true) : gpill(true)),
                          padding: "4px 11px", fontSize: 11,
                        }}
                        title={`Book once ${n} ${n === 1 ? "person" : "people"} can make a time — guests included, since you named a number.`}
                        onClick={() => updatePlanSettings(p.id, { minimum: n }).catch(() => {})}
                      >
                        {n}
                      </div>
                    ))}
                  </div>
                  {!p.requires_all_members && (
                    <span style={{ fontSize: 11, color: "#a09889", lineHeight: 1.5 }}>
                      You've set a count, so people voting by link count toward it too.
                      Going back to “everyone” means starting a new poll.
                    </span>
                  )}
                </div>
              </div>
            )}

            {/* ---- booked ---------------------------------------------- */}
            {p.status === "booked" && bookedRound && (
              <>
                <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                  <div style={{ width: 30, height: 30, borderRadius: "50%", background: "#2A9D8F", color: "#fff", display: "flex", alignItems: "center", justifyContent: "center" }}>
                    <CheckIcon size={15} color="#fff" sw={2.6} />
                  </div>
                  <span style={{ fontSize: 15, fontWeight: 600 }}>{bookedRound.label}</span>
                </div>
                <div style={{ fontSize: 12.5, color: "#8c8577", lineHeight: 1.5 }}>
                  It's on the calendar for everyone who said they could make it.
                </div>
                <div style={{ height: 56, borderRadius: 14, background: "linear-gradient(120deg, #F3C9A8, #A9CBB6, #C9B6D4)" }} />
                <div style={{ display: "flex", gap: 9, flexWrap: "wrap" }}>
                  <div className="hov-glass" style={gpill(true)} onClick={() => { setPage("calendar"); setView("week"); }}>
                    View on calendar
                  </div>
                  {bookedRound.event_link && (
                    <a href={bookedRound.event_link} target="_blank" rel="noreferrer" style={{ ...gpill(true), textDecoration: "none" }}>
                      Open in your calendar
                    </a>
                  )}
                </div>
              </>
            )}

            {/* voting closed on time, nothing reached the bar — say what
                happens next instead of leaving a card that stopped responding */}
            {p.status === "expired" && !p.is_host && (
              <div style={{ fontSize: 12.5, color: "#a09889", lineHeight: 1.5 }}>
                {nameFromEmail(p.host || "")} can still lock in a time from the
                answers that came in — or reopen it if the group needs longer.
                Your votes were kept.
              </div>
            )}
          </div>
        );
      })}

      {plans.length === 0 && activeGroup && (
        <div style={{ ...glass(24), width: "min(640px, 100%)", padding: "26px 22px", textAlign: "center", color: "#8c8577", fontSize: 13.5 }}>
          That's it for now!
        </div>
      )}
    </div>
  );
}
