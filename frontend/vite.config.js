import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev: `npm run dev` proxies API calls to the FastAPI backend on :8000.
// Build: output goes straight into backend/app/static, which FastAPI already
// serves at "/" — so the deployed app is the built frontend, no extra config.
export default defineConfig({
  plugins: [react()],
  // FastAPI serves the bundle from its /static mount; "/" returns index.html.
  base: "/static/",
  build: {
    outDir: "../backend/app/static",
    emptyOutDir: true,
  },
  server: {
    // Every backend prefix the frontend actually calls must be proxied, or the
    // request hits Vite (which serves index.html) and the JSON parse fails.
    // `/polls` was the pre-rename name and is dead — the API is `/plans` now.
    proxy: {
      "/auth": "http://localhost:8000",
      "/groups": "http://localhost:8000",
      "/plans": "http://localhost:8000",
      "/events": "http://localhost:8000",
      "/reviews": "http://localhost:8000",
      "/chat": "http://localhost:8000",
    },
  },
});
