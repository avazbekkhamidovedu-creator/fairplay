# FairPlay asset pack

The exam page's big static files ship inside the FairPlay apps, so starting an exam never
waits for them to download. The server keeps serving the identical files, for browsers and
as the fallback.

## What and where

- Built by `npm run build` (plugin `web/scripts/app-assets.js`) into `web/dist/app-assets/`,
  then deployed to `server/static/app-assets/` (served at `/app-assets/...`).
- Contents:
  - `pyodide/` - Pyodide runtime (`pyodide.mjs`, `pyodide.asm.js`, `pyodide.asm.wasm`,
    `python_stdlib.zip`, `pyodide-lock.json`, `pyodide.js`)
  - `pdfjs/pdf.worker.min.mjs`, `pdfjs/cmaps/*.bcmap`, `pdfjs/standard_fonts/*`
  - `manifest.json` = `{"version": "<16 hex>", "files": {"<relative path>": "<sha256 hex>", ...}}`
- `version` = first 16 hex chars of sha256 over `"<path>\0<sha256>\n"` for every file in
  path order. Same files -> same version; any change -> new version. The page bakes in the
  version it was built with (`__FAIRPLAY_ASSETS_VERSION__`).

## How the native builds get it (CI)

1. Build the web (`cd web && npm ci && npm run build`), or take `server/static/app-assets/`
   from the commit being released (it is the same output).
2. Copy the whole `app-assets/` directory, unchanged, into the app bundle:
   - Mac / iPad: as a folder resource (e.g. `Resources/app-assets/`).
   - Windows: next to the exe (e.g. `app-assets\`), mapped by WebView2.
3. Read `manifest.json` at runtime for `version`. Optionally verify files against the
   hashes in CI. Never edit files in the pack: a changed file must change the version.
4. Ship the pack from the same commit as the server it will talk to. An app with an older
   or newer pack still works (the page then uses the server copy); it just is not faster.

## Contract the apps implement

- Inject before any page script, only into exam-server pages:
  `window.fairplayAssets = {base: "<base ending with />", version: "<manifest version>"}`
  - Mac / iPad: `base = "fairplay-asset://bundle/"` (custom URL scheme handler)
  - Windows: `base = "https://fairplay-assets.example/"` (WebView2 virtual host mapping)
- Serve `<base><relative path>` from the pack with:
  - Content-Type: `.js`/`.mjs` `text/javascript`; `.wasm` `application/wasm`;
    `.json` `application/json`; `.zip` `application/zip`; `.bcmap`/`.pfb`
    `application/octet-stream`; `.ttf` `font/ttf`
  - `Access-Control-Allow-Origin: *` (the page imports and fetches cross-origin)
  - a long cache lifetime (the version pins the content)
  - 404 for anything not in the pack (no directory listing, nothing outside the pack)
- The page uses the app's base only when `version` equals the version it was built with,
  and only those two exact base strings. Otherwise it uses `/app-assets/`.
- If anything fails to load from the app's base (worker error, fetch error, WebAssembly
  compile error, a 30 s Pyodide / 20 s pdf.js start timeout), the page switches to
  `/app-assets/` at once and stays on it for the rest of the browser session
  (`sessionStorage["fairplay.appAssetsFailed"]`).
- The page's CSP allows exactly `fairplay-asset:` and `https://fairplay-assets.example` in
  `script-src`, `connect-src` and `worker-src` (`server/app/http_guard.py`, mirrored in
  `web/index.html`).

## Old paths

Until 0.6.1 the same files were served at `/pyodide/` and `/pdfjs/`. Those directories stay
in `server/static/` for one release (for pages opened before the update) and can then be
deleted.
