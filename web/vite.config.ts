/// <reference types="vitest" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { appAssets } from "./scripts/app-assets.js";

export default defineConfig({
  base: "/",
  // 2026-10-23 (perf 0.6.2): appAssets writes the asset pack (Pyodide, pdf.js worker, fonts,
  // character maps) to dist/app-assets/ with manifest.json, and bakes its version into the
  // page; the FairPlay apps ship the same pack (scripts/APP_ASSETS.md). No CDN.
  plugins: [react(), appAssets()],
  // module workers (the Python runner loads <asset base>pyodide/pyodide.mjs with import())
  worker: { format: "es" },
  server: {
    proxy: {
      "/api": "http://127.0.0.1:8000",
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    css: false,
  },
});
