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
    proxy: {
      "/auth": "http://localhost:8000",
      "/groups": "http://localhost:8000",
      "/polls": "http://localhost:8000",
      "/chat": "http://localhost:8000",
    },
  },
});
