// Phase 4: Pyodide (Python in the browser, for Run in coding questions) is served from our
// own server, never a CDN: its runtime files are copied to dist/pyodide/ at build time
// (then to server/static/pyodide/). In dev they are served from node_modules.
import { copyFileSync, createReadStream, existsSync, mkdirSync, statSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const from = resolve(root, "node_modules/pyodide");

export const PYODIDE_FILES = [
  "pyodide.js",
  "pyodide.mjs",
  "pyodide.asm.js",
  "pyodide.asm.wasm",
  "python_stdlib.zip",
  "pyodide-lock.json",
];

const TYPES = {
  ".js": "text/javascript",
  ".mjs": "text/javascript",
  ".wasm": "application/wasm",
  ".zip": "application/zip",
  ".json": "application/json",
};

export function pyodideAssets() {
  return {
    name: "fairplay-pyodide-assets",
    configureServer(server) {
      server.middlewares.use("/pyodide/", (req, res, next) => {
        const name = (req.url ?? "").split("?")[0].replace(/^\//, "");
        if (!PYODIDE_FILES.includes(name)) return next();
        const file = resolve(from, name);
        if (!existsSync(file)) return next();
        const ext = name.slice(name.lastIndexOf("."));
        res.setHeader("Content-Type", TYPES[ext] ?? "application/octet-stream");
        res.setHeader("Content-Length", String(statSync(file).size));
        createReadStream(file).pipe(res);
      });
    },
    writeBundle(options) {
      const out = options.dir ?? resolve(root, "dist");
      mkdirSync(resolve(out, "pyodide"), { recursive: true });
      for (const name of PYODIDE_FILES) {
        const src = resolve(from, name);
        if (!existsSync(src)) throw new Error(`pyodide file missing: ${src} (run npm install)`);
        copyFileSync(src, resolve(out, "pyodide", name));
      }
    },
  };
}
