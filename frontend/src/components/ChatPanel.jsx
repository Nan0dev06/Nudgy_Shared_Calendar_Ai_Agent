import { useEffect, useRef, useState } from "react";
import { useApp } from "../ctx.js";
import { glass, gpill, dpill, orbGradient } from "../theme.js";
import { XIcon, SendIcon, CheckIcon } from "../Icons.jsx";

const uBubble = {
  alignSelf: "flex-end", maxWidth: "85%",
  background: "linear-gradient(160deg, rgba(64,60,50,.92), rgba(45,45,45,.85))",
  color: "#F7F2EA", borderRadius: "16px 16px 6px 16px", padding: "10px 14px",
  fontSize: 13, lineHeight: 1.45, animation: "fadeUp .25s",
};

const oBubble = {
  alignSelf: "flex-start", maxWidth: "92%",
  background: "rgba(255,253,247,.6)", border: "1px solid rgba(255,255,255,.7)",
  backdropFilter: "blur(12px)", WebkitBackdropFilter: "blur(12px)",
  borderRadius: "16px 16px 16px 6px", padding: "10px 14px",
  fontSize: 13, lineHeight: 1.5, animation: "fadeUp .25s",
};

export default function ChatPanel() {
  const { chatOpen, setChatOpen, chatMsgs, chatTyping, doSend, displayName } = useApp();
  const [input, setInput] = useState("");
  const bodyRef = useRef(null);

  useEffect(() => {
    if (bodyRef.current) bodyRef.current.scrollTop = bodyRef.current.scrollHeight;
  }, [chatMsgs, chatTyping]);

  const send = () => {
    const t = input.trim();
    if (!t) return;
    setInput("");
    doSend(t);
  };

  return (
    <div
      style={{
        width: chatOpen ? 392 : 0, flex: "none", display: "flex", overflow: "hidden",
        transition: "width .35s cubic-bezier(.4,0,.2,1)", zIndex: 7,
      }}
    >
      <div
        style={{
          ...glass(26), width: 376, flex: "none", margin: "16px 16px 16px 0",
          display: "flex", flexDirection: "column", minHeight: 0,
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "16px 16px 12px", borderBottom: "1px solid rgba(150,142,128,.18)" }}>
          <div style={{ width: 30, height: 30, borderRadius: "50%", background: orbGradient(18) }} />
          <div style={{ display: "flex", flexDirection: "column", minWidth: 0 }}>
            <span style={{ fontSize: 14, fontWeight: 600 }}>Orbi</span>
            <span style={{ fontSize: 10.5, color: "#a09889", whiteSpace: "nowrap" }}>
              Sees only busy/free — never details
            </span>
          </div>
          <div
            className="hov-icon"
            style={{ marginLeft: "auto", width: 26, height: 26, display: "flex", alignItems: "center", justifyContent: "center", borderRadius: 9, cursor: "pointer", color: "#a49c8c" }}
            onClick={() => setChatOpen(false)}
          >
            <XIcon />
          </div>
        </div>

        <div
          ref={bodyRef}
          style={{ flex: 1, minHeight: 0, overflow: "auto", padding: "14px 16px", display: "flex", flexDirection: "column", gap: 10 }}
        >
          {chatMsgs.length === 0 && (
            <>
              <div style={oBubble}>
                Hi {displayName} — I can find times everyone's free, and places
                that suit the whole group. What are we planning?
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: 8, alignItems: "flex-start" }}>
                <div className="hov-glass" style={gpill(true)} onClick={() => doSend("Find a time this week when everyone's free")}>
                  Find a time this week
                </div>
                <div className="hov-glass" style={gpill(true)} onClick={() => doSend("Find us a dinner spot everyone can reach")}>
                  Find us a dinner spot
                </div>
              </div>
            </>
          )}

          {chatMsgs.map((m, i) => {
            if (m.u) return <div key={i} style={uBubble}>{m.u}</div>;
            if (m.step)
              return (
                <div key={i} style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 11.5, fontStyle: "italic", color: "#8c8577", animation: "fadeUp .3s" }}>
                  <CheckIcon size={12} color="#2A9D8F" />
                  {m.step}
                </div>
              );
            if (m.o) return <div key={i} style={oBubble}>{m.o}</div>;
            if (m.acts)
              return (
                <div key={i} style={{ display: "flex", gap: 8, flexWrap: "wrap", animation: "fadeUp .3s" }}>
                  {m.acts.map((a, j) => (
                    <div key={j} className="hov-lift-sm" style={a.dark ? dpill(true) : gpill(true)} onClick={a.go}>
                      {a.label}
                    </div>
                  ))}
                </div>
              );
            return null;
          })}

          {chatTyping && (
            <div style={{ fontSize: 12, color: "#a09889", animation: "blink 1.1s ease-in-out infinite" }}>
              Checking calendars…
            </div>
          )}
        </div>

        <div style={{ padding: "12px 16px 16px", display: "flex", gap: 9, alignItems: "center" }}>
          <input
            placeholder="Ask about times or places…"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && send()}
            style={{
              flex: 1,
              background: "linear-gradient(160deg, rgba(255,255,255,.55), rgba(255,255,255,.3))",
              border: "1px solid rgba(255,255,255,.65)", borderRadius: 999,
              padding: "10px 16px", fontSize: 13, outline: "none", minWidth: 0,
            }}
          />
          <div
            className="hov-lift-sm"
            onClick={send}
            style={{
              width: 40, height: 40, borderRadius: "50%", flex: "none", cursor: "pointer",
              display: "flex", alignItems: "center", justifyContent: "center",
              background: "linear-gradient(160deg, rgba(64,60,50,.92), rgba(45,45,45,.85))",
              boxShadow: "0 8px 18px rgba(45,38,28,.26)", transition: "all .18s",
            }}
          >
            <SendIcon />
          </div>
        </div>
      </div>
    </div>
  );
}
