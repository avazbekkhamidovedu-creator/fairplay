/// <reference types="vitest" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { appAssets } from "./scripts/app-assets.js";

const STUDENT_MODULE = /\/src\/(pages\/student|components\/student|api\/student|pages\/ClassLoginSetup)[./]/;

// Vite names a lazy chunk's stylesheet after the chunk
const STUDENT_CSS = /^(routes|student|ClassLoginSetup)\.css$/;

export default defineConfig({
  base: "/",
  // 2026-10-23 (perf 0.6.2): appAssets writes the asset pack (Pyodide, pdf.js worker, fonts,
  // character maps) to dist/app-assets/ with manifest.json, and bakes its version into the
  // page; the FairPlay apps ship the same pack (scripts/APP_ASSETS.md). No CDN.
  plugins: [react(), appAssets()],
  // module workers (the Python runner loads <asset base>pyodide/pyodide.mjs with import())
  worker: { format: "es" },
  build: {
    rollupOptions: {
      output: {
        chunkFileNames: (chunk) =>
          chunk.moduleIds.some((id) => STUDENT_MODULE.test(id))
            ? "student/assets/[name]-[hash].js"
            : "assets/[name]-[hash].js",
        assetFileNames: (asset) =>
          STUDENT_CSS.test(asset.name ?? "") ? "student/assets/[name]-[hash][extname]" : "assets/[name]-[hash][extname]",
      },
    },
  },
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
