import { defineConfig } from "vitest/config";

export default defineConfig({
  server: {
    // Under utveckling skickas API-anrop vidare till kärnan.
    proxy: {
      "/api": { target: "http://127.0.0.1:8000", ws: true },
      "/health": "http://127.0.0.1:8000",
    },
  },
  test: {
    environment: "jsdom",
  },
});
