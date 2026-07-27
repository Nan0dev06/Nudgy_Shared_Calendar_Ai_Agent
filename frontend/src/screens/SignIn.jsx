import { useEffect, useState } from "react";
import { heavy, gpill, dpill, fieldStyle, fieldLabel, SAGE } from "../theme.js";
import { OrbLogo } from "../components/OrbLogo.jsx";
import { api, loginUrl, msLoginUrl } from "../api.js";

// Identity is decoupled from calendars: a person can sign in with email +
// password (or a magic link) and use Nudgy with no external calendar, OR use
// "Continue with Google/Microsoft" which signs in AND connects a calendar in
// one OAuth step.
//
// Modes: signin · register · magic · forgot · reset. The reset form is reached
// from the emailed link, which lands on "/?mode=reset&token=…" (see
// auth_routes.request_password_reset — `/auth/reset` has no server route).

const COPY = {
  signin: { title: "Welcome back", sub: "Sign in to see what everyone's up to" },
  register: { title: "Create your account", sub: "Start coordinating with your groups" },
  magic: { title: "Email me a link", sub: "We'll send a one-click sign-in link — no password" },
  forgot: { title: "Reset your password", sub: "We'll email you a link to set a new one" },
  reset: { title: "Set a new password", sub: "Choose a new password for your account" },
};

function readResetToken() {
  const p = new URLSearchParams(window.location.search);
  return p.get("mode") === "reset" ? p.get("token") : null;
}

// The OAuth callback redirects here with ?auth_error=<provider> when a sign-in
// fails on the provider side (see auth_routes._auth_error_redirect).
function readAuthError() {
  const p = new URLSearchParams(window.location.search).get("auth_error");
  if (!p) return "";
  const name = p === "microsoft" ? "Microsoft" : "Google";
  return `We couldn't finish signing in with ${name}. Please try again.`;
}

// A labelled glass input matching the rest of the app's fields.
function Field({ label, ...props }) {
  return (
    <label style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      <span style={fieldLabel}>{label}</span>
      <input {...props} style={{ ...fieldStyle, flex: "none" }} />
    </label>
  );
}

const GoogleMark = () => (
  <svg width="16" height="16" viewBox="0 0 24 24">
    <path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92a5.06 5.06 0 0 1-2.2 3.32v2.77h3.57c2.08-1.92 3.27-4.74 3.27-8.1z" />
    <path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" />
    <path fill="#FBBC05" d="M5.84 14.1c-.22-.66-.35-1.36-.35-2.1s.13-1.44.35-2.1V7.06H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.94l3.66-2.84z" />
    <path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.06l3.66 2.84c.87-2.6 3.3-4.52 6.16-4.52z" />
  </svg>
);

const MicrosoftMark = () => (
  <svg width="15" height="15" viewBox="0 0 23 23">
    <path fill="#f25022" d="M1 1h10v10H1z" />
    <path fill="#7fba00" d="M12 1h10v10H12z" />
    <path fill="#00a4ef" d="M1 12h10v10H1z" />
    <path fill="#ffb900" d="M12 12h10v10H12z" />
  </svg>
);

export default function SignIn() {
  const initialToken = readResetToken();
  const [mode, setMode] = useState(initialToken ? "reset" : "signin");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(readAuthError);
  const [notice, setNotice] = useState(""); // generic success/info banner

  // drop ?auth_error= from the URL so a refresh doesn't keep showing the banner
  useEffect(() => {
    if (new URLSearchParams(window.location.search).has("auth_error")) {
      window.history.replaceState({}, "", window.location.pathname);
    }
  }, []);

  const go = (m) => {
    setMode(m);
    setErr("");
    setNotice("");
    setPassword("");
  };

  async function submit(e) {
    e?.preventDefault();
    if (busy) return;
    setErr("");
    setNotice("");
    setBusy(true);
    try {
      if (mode === "signin") {
        await api.login(email.trim(), password);
        // cookie is set — a full reload boots the app in its logged-in state,
        // same landing as the OAuth redirect.
        window.location.reload();
        return;
      }
      if (mode === "register") {
        const r = await api.register(email.trim(), password);
        setNotice(r.message || "Check your inbox to verify your email.");
        setPassword("");
      } else if (mode === "magic") {
        const r = await api.magicLink(email.trim());
        setNotice(r.message || "If that email has an account, a link is on its way.");
      } else if (mode === "forgot") {
        const r = await api.requestReset(email.trim());
        setNotice(r.message || "If that email has an account, a reset link is on its way.");
      } else if (mode === "reset") {
        await api.resetPassword(initialToken, password);
        // drop the token from the URL so a refresh can't replay it, then send
        // them to sign in with the new password.
        window.history.replaceState({}, "", window.location.pathname);
        go("signin");
        setNotice("Password updated — sign in with your new password.");
      }
    } catch (e2) {
      setErr(e2.message || "Something went wrong — try again.");
    } finally {
      setBusy(false);
    }
  }

  const c = COPY[mode];
  const showEmail = mode !== "reset";
  const showPassword = mode === "signin" || mode === "register" || mode === "reset";
  const showSocial = mode === "signin" || mode === "register";
  const submitLabel = {
    signin: "Sign in",
    register: "Create account",
    magic: "Send link",
    forgot: "Send reset link",
    reset: "Update password",
  }[mode];

  return (
    <div
      style={{
        flex: 1, display: "flex", alignItems: "center", justifyContent: "center",
        position: "relative", zIndex: 1,
        animation: "fadeUp .35s cubic-bezier(.4,0,.2,1)",
      }}
    >
      <div
        style={{
          ...heavy(28), width: 400, maxWidth: "calc(100vw - 32px)", padding: 28,
          display: "flex", flexDirection: "column", gap: 18,
          position: "relative", zIndex: 1,
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <div style={{ width: 32, height: 32, flex: "none", display: "flex" }}>
            <OrbLogo size={32} />
          </div>
          <span style={{ fontSize: 18, fontWeight: 600 }}>Nudgy</span>
        </div>

        <div>
          <div style={{ fontSize: 22, fontWeight: 600 }}>{c.title}</div>
          <div style={{ fontSize: 13, color: "#8c8577", marginTop: 3 }}>{c.sub}</div>
        </div>

        {notice && (
          <div
            style={{
              fontSize: 12.5, lineHeight: 1.5, color: "#237c72",
              background: "rgba(42,157,143,.12)", borderRadius: 12, padding: "10px 13px",
            }}
          >
            {notice}
          </div>
        )}
        {err && (
          <div
            style={{
              fontSize: 12.5, lineHeight: 1.5, color: "#b5432a",
              background: "rgba(217,93,57,.1)", borderRadius: 12, padding: "10px 13px",
            }}
          >
            {err}
          </div>
        )}

        <form onSubmit={submit} style={{ display: "flex", flexDirection: "column", gap: 14 }}>
          {showEmail && (
            <Field
              label="Email"
              type="email"
              autoComplete="email"
              placeholder="you@example.com"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
            />
          )}
          {showPassword && (
            <Field
              label={mode === "reset" ? "New password" : "Password"}
              type="password"
              autoComplete={mode === "signin" ? "current-password" : "new-password"}
              placeholder={mode === "signin" ? "Your password" : "At least 8 characters"}
              minLength={mode === "signin" ? undefined : 8}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
            />
          )}
          <button
            type="submit"
            className="hov-lift-sm"
            disabled={busy}
            style={{
              ...dpill(false), justifyContent: "center", width: "100%",
              opacity: busy ? 0.65 : 1, cursor: busy ? "default" : "pointer",
              border: "none",
            }}
          >
            {busy ? "…" : submitLabel}
          </button>
        </form>

        {mode === "signin" && (
          <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12.5 }}>
            <button onClick={() => go("magic")} style={linkBtn}>Email me a link instead</button>
            <button onClick={() => go("forgot")} style={linkBtn}>Forgot password?</button>
          </div>
        )}

        {showSocial && (
          <>
            <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
              <div style={divider} />
              <span style={{ fontSize: 11, color: "#a09889", whiteSpace: "nowrap" }}>or continue with</span>
              <div style={divider} />
            </div>
            <div style={{ display: "flex", gap: 10 }}>
              <div
                className="hov-glass"
                style={{ ...gpill(false), flex: 1, justifyContent: "center" }}
                onClick={() => (window.location.href = loginUrl)}
              >
                <GoogleMark /> Google
              </div>
              <div
                className="hov-glass"
                style={{ ...gpill(false), flex: 1, justifyContent: "center" }}
                onClick={() => (window.location.href = msLoginUrl)}
              >
                <MicrosoftMark /> Microsoft
              </div>
            </div>
            <div style={{ fontSize: 11.5, lineHeight: 1.5, color: "#a09889" }}>
              We only read busy/free times — never your event titles, guests, or
              locations.
            </div>
          </>
        )}

        {/* mode switcher */}
        <div style={{ fontSize: 12.5, color: "#8c8577", textAlign: "center" }}>
          {mode === "signin" && (
            <>New to Nudgy? <button onClick={() => go("register")} style={linkBtn}>Create an account</button></>
          )}
          {(mode === "register" || mode === "magic" || mode === "forgot" || mode === "reset") && (
            <>Already have an account? <button onClick={() => go("signin")} style={linkBtn}>Sign in</button></>
          )}
        </div>
      </div>
    </div>
  );
}

const linkBtn = {
  background: "none",
  border: "none",
  padding: 0,
  cursor: "pointer",
  color: SAGE,
  fontWeight: 600,
  fontSize: "inherit",
};

const divider = { flex: 1, height: 1, background: "rgba(200,182,155,.4)" };
