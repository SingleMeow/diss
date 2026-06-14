import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Proxies /api to the FastAPI backend so the UI can be served from the same
// origin during development without fighting CORS.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
});
