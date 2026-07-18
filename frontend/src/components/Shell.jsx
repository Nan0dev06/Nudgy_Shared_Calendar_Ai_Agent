import { useApp } from "../ctx.js";
import Sidebar from "./Sidebar.jsx";
import TopBar from "./TopBar.jsx";
import ChatPanel from "./ChatPanel.jsx";
import Modals from "./Modals.jsx";
import HomePage from "../pages/HomePage.jsx";
import CalendarPage from "../pages/CalendarPage.jsx";
import ActivityPage from "../pages/ActivityPage.jsx";
import PollsPage from "../pages/PollsPage.jsx";
import PlacesPage from "../pages/PlacesPage.jsx";
import SettingsPage from "../pages/SettingsPage.jsx";
import { orbGradient } from "../theme.js";

export default function Shell() {
  const { page, chatOpen, setChatOpen } = useApp();

  return (
    <div style={{ flex: 1, display: "flex", minWidth: 0, position: "relative", zIndex: 1 }}>
      <Sidebar />

      <main
        style={{
          flex: 1, minWidth: 0, display: "flex", flexDirection: "column",
          padding: "24px 28px 22px 14px", gap: 16, position: "relative",
        }}
      >
        <TopBar />
        {page === "home" && <HomePage />}
        {page === "calendar" && <CalendarPage />}
        {page === "activity" && <ActivityPage />}
        {page === "polls" && <PollsPage />}
        {page === "places" && <PlacesPage />}
        {page === "settings" && <SettingsPage />}

        {/* floating orb — hidden while chat is open */}
        <div
          title="Ask the orb"
          onClick={() => setChatOpen(true)}
          style={{
            position: "absolute", right: 28, bottom: 26, width: 54, height: 54,
            borderRadius: "50%", cursor: "pointer", zIndex: 30,
            background: orbGradient(30),
            boxShadow: "0 12px 30px rgba(45,45,45,.25)",
            display: chatOpen ? "none" : "flex",
            alignItems: "center", justifyContent: "center",
            animation: "ofloat 3.4s ease-in-out infinite",
          }}
        >
          <span style={{ fontSize: 20, color: "#fff" }}>✦</span>
        </div>
      </main>

      <ChatPanel />
      <Modals />
    </div>
  );
}
