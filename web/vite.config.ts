/// <reference types="vitest" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { pdfjsAssets } from "./scripts/pdfjs-assets.js";
import { pyodideAssets } from "./scripts/pyodide-assets.js";

export default defineConfig({
  base: "/",
  // 2026-10-23: pdfjsAssets copies pdf.js fonts / character maps to dist/pdfjs/
  // Phase 4: pyodideAssets copies Python-in-the-browser to dist/pyodide/ (no CDN)
  plugins: [react(), pdfjsAssets(), pyodideAssets()],
  // module workers (the Python runner loads /pyodide/pyodide.mjs with import())
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
