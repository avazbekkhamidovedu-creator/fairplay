// 2026-10-23 (perf 0.6.2): the asset pack. The big static files the exam page needs
// (Pyodide for Python Run, pdf.js worker + fonts + character maps for PDF materials) are
// written to dist/app-assets/ with a manifest.json; the server serves them at
// /app-assets/... and the FairPlay apps ship the same directory inside the app, so starting
// an exam does not wait for these downloads. See APP_ASSETS.md for the contract.
//
// The manifest version is a short hash of every file's hash: it is baked into the page
// (define __FAIRPLAY_ASSETS_VERSION__), and the page uses the app's copy only when the app
// reports exactly that version.
import { createHash } from "node:crypto";
import { copyFileSync, createReadStream, existsSync, mkdirSync, readdirSync, readFileSync, statSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");

export const PYODIDE_FILES = [
  "pyodide.js",
  "pyodide.mjs",
  "pyodide.asm.js",
  "pyodide.asm.wasm",
  "python_stdlib.zip",
  "pyodide-lock.json",
];

// SQL questions: sqlite-wasm's browser build (glue module + wasm), loaded by the SQL worker.
export const SQLITE_FILES = {
  "index.mjs": "index.mjs",
  "sqlite3.wasm": "sqlite3.wasm",
};

export const TYPES = {
  ".js": "text/javascript",
  ".mjs": "text/javascript",
  ".wasm": "application/wasm",
  ".zip": "application/zip",
  ".json": "application/json",
  ".bcmap": "application/octet-stream",
  ".pfb": "application/octet-stream",
  ".ttf": "font/ttf",
};

export function contentType(name) {
  const i = name.lastIndexOf(".");
  return (i >= 0 && TYPES[name.slice(i)]) || "application/octet-stream";
}

function listDir(dir) {
  return readdirSync(dir, { withFileTypes: true })
    .filter((e) => e.isFile())
    .map((e) => e.name)
    .sort();
}

/** Pack path -> source file. Sorted by pack path. `nodeModules` is for tests. */
export function packSources(nodeModules = resolve(root, "node_modules")) {
  const out = {};
  const py = join(nodeModules, "pyodide");
  for (const name of PYODIDE_FILES) out[`pyodide/${name}`] = join(py, name);
  const sqlite = join(nodeModules, "@sqlite.org", "sqlite-wasm", "dist");
  for (const [name, file] of Object.entries(SQLITE_FILES)) out[`sqlite/${name}`] = join(sqlite, file);
  const pdf = join(nodeModules, "pdfjs-dist");
  out["pdfjs/pdf.worker.min.mjs"] = join(pdf, "legacy/build/pdf.worker.min.mjs");
  for (const dir of ["cmaps", "standard_fonts"]) {
    // license files and the like ship too: they are part of the fonts' distribution
    for (const name of listDir(join(pdf, dir))) out[`pdfjs/${dir}/${name}`] = join(pdf, dir, name);
  }
  for (const [path, src] of Object.entries(out)) {
    if (!existsSync(src)) throw new Error(`asset pack: ${path} missing at ${src} (run npm install)`);
  }
  return Object.fromEntries(Object.entries(out).sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0)));
}

export function sha256(buf) {
  return createHash("sha256").update(buf).digest("hex");
}

/** Version = first 16 hex of sha256 over "path\0hash\n" lines in path order. */
export function packVersion(files) {
  const h = createHash("sha256");
  for (const path of Object.keys(files).sort()) h.update(`${path}\0${files[path]}\n`);
  return h.digest("hex").slice(0, 16);
}

/** {version, files} from pack path -> source file. Same input files, same manifest. */
export function buildManifest(sources) {
  const files = {};
  for (const path of Object.keys(sources).sort()) files[path] = sha256(readFileSync(sources[path]));
  return { version: packVersion(files), files };
}

export function manifestJson(manifest) {
  const files = {};
  for (const path of Object.keys(manifest.files).sort()) files[path] = manifest.files[path];
  return JSON.stringify({ version: manifest.version, files }, null, 2) + "\n";
}

/** Writes <outDir>/<path> for every file plus manifest.json. */
export function writePack(outDir, sources = packSources(), manifest = buildManifest(sources)) {
  for (const [path, src] of Object.entries(sources)) {
    const dest = join(outDir, path);
    mkdirSync(dirname(dest), { recursive: true });
    copyFileSync(src, dest);
  }
  writeFileSync(join(outDir, "manifest.json"), manifestJson(manifest));
  return manifest;
}

let cached = null;
function current() {
  cached ??= (() => {
    const sources = packSources();
    return { sources, manifest: buildManifest(sources) };
  })();
  return cached;
}

/** Vite plugin: bakes the version, serves the pack in dev, writes it at build. */
export function appAssets() {
  return {
    name: "fairplay-app-assets",
    config() {
      return { define: { __FAIRPLAY_ASSETS_VERSION__: JSON.stringify(current().manifest.version) } };
    },
    configureServer(server) {
      server.middlewares.use("/app-assets/", (req, res, next) => {
        // the page asks for /app-assets/v/<version>/<file> (the server's immutable URL)
        const name = decodeURIComponent((req.url ?? "").split("?")[0].replace(/^\//, "").replace(/^v\/[^/]+\//, ""));
        const { sources, manifest } = current();
        if (name === "manifest.json") {
          res.setHeader("Content-Type", "application/json");
          res.end(manifestJson(manifest));
          return;
        }
        const file = sources[name];
        if (!file) return next();
        res.setHeader("Content-Type", contentType(name));
        res.setHeader("Content-Length", String(statSync(file).size));
        createReadStream(file).pipe(res);
      });
    },
    writeBundle(options) {
      const out = options.dir ?? resolve(root, "dist");
      const { sources, manifest } = current();
      writePack(join(out, "app-assets"), sources, manifest);
    },
  };
}
