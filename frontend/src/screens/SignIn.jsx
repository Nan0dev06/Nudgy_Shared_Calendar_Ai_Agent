import { heavy, gpill, orbGradient } from "../theme.js";
import { loginUrl } from "../api.js";

// Single sign-in path: Google Calendar IS the login (backend OAuth flow).
export default function SignIn() {
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
          ...heavy(28), width: 400, padding: 28, display: "flex",
          flexDirection: "column", gap: 18, position: "relative", zIndex: 1,
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <div style={{ width: 32, height: 32, borderRadius: "50%", background: orbGradient(20) }} />
          <span style={{ fontSize: 18, fontWeight: 600 }}>Nudgy</span>
        </div>
        <div>
          <div style={{ fontSize: 22, fontWeight: 600 }}>Welcome</div>
          <div style={{ fontSize: 13, color: "#8c8577", marginTop: 3 }}>
            Sign in with your Google Calendar to see what everyone's up to
          </div>
        </div>
        <div
          className="hov-glass"
          style={{ ...gpill(false), justifyContent: "center" }}
          onClick={() => (window.location.href = loginUrl)}
        >
          <svg width="16" height="16" viewBox="0 0 24 24">
            <path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92a5.06 5.06 0 0 1-2.2 3.32v2.77h3.57c2.08-1.92 3.27-4.74 3.27-8.1z"/>
            <path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"/>
            <path fill="#FBBC05" d="M5.84 14.1c-.22-.66-.35-1.36-.35-2.1s.13-1.44.35-2.1V7.06H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.94l3.66-2.84z"/>
            <path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.06l3.66 2.84c.87-2.6 3.3-4.52 6.16-4.52z"/>
          </svg>
          Connect with Google Calendar
        </div>
        <div style={{ fontSize: 12, lineHeight: 1.5, color: "#a09889" }}>
          We only read busy/free times — never your event titles, guests, or
          locations.
        </div>
      </div>
    </div>
  );
}
