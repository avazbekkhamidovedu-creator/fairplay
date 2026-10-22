// 2026-10-23 (exam tools): pdf.js needs its character maps (CJK PDFs) and standard fonts as
// files on our own server (no CDN): copied to dist/pdfjs/ at build. In dev they are served
// from node_modules (src/lib/pdf.ts picks the path).
import { cpSync, existsSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");

export function pdfjsAssets() {
  return {
    name: "fairplay-pdfjs-assets",
    apply: "build",
    writeBundle(options) {
      const out = options.dir ?? resolve(root, "dist");
      for (const dir of ["cmaps", "standard_fonts"]) {
        const from = resolve(root, "node_modules/pdfjs-dist", dir);
        if (existsSync(from)) cpSync(from, resolve(out, "pdfjs", dir), { recursive: true });
      }
    },
  };
}
