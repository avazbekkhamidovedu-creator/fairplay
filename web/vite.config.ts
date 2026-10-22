/// <reference types="vitest" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { pdfjsAssets } from "./scripts/pdfjs-assets.js";

export default defineConfig({
  base: "/",
  // 2026-10-23: pdfjsAssets copies pdf.js fonts / character maps to dist/pdfjs/
  plugins: [react(), pdfjsAssets()],
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
