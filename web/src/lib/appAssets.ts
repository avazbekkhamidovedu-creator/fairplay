// 2026-10-23 (perf 0.6.2): where the big static files come from (Pyodide, the pdf.js worker,
// its fonts and character maps). The FairPlay apps ship the asset pack inside the app and
// say so before the page's scripts run:
//   window.fairplayAssets = { base: "fairplay-asset://bundle/" | "https://fairplay-assets.example/",
//                             version: "<manifest version>" }
// The page takes the app's copy only when its version is exactly the one this build was made
// with (the pack is versioned by the hash of its files); otherwise, and for the rest of the
// session after anything fails to load from the app, the server's copy at
// /app-assets/v/<version>/.
// See web/scripts/APP_ASSETS.md.

declare const __FAIRPLAY_ASSETS_VERSION__: string | undefined;

/** The pack version this build expects ("dev" when not built through Vite). */
export const EXPECTED_ASSETS_VERSION: string =
  typeof __FAIRPLAY_ASSETS_VERSION__ === "string" ? __FAIRPLAY_ASSETS_VERSION__ : "dev";
/** The server's copy of the pack (same files as the apps'), under this build's pack version:
 *  the server marks it immutable, and a release that upgrades Pyodide or pdf.js changes the
 *  URL, so a cached old file is never mixed with new page code (0.6.2 review). */
export const SERVER_ASSET_BASE = `/app-assets/v/${EXPECTED_ASSETS_VERSION}/`;
/** The bases the apps use (Mac/iPad custom scheme, Windows WebView2 virtual host). */
export const APP_ASSET_BASES: readonly string[] = ["fairplay-asset://bundle/", "https://fairplay-assets.example/"];

const FAILED_KEY = "fairplay.appAssetsFailed";
let failed = false;

interface AssetHost {
  fairplayAssets?: unknown;
  sessionStorage?: Storage;
}

function host(): AssetHost | undefined {
  return typeof self === "undefined" ? undefined : (self as unknown as AssetHost);
}

function failedThisSession(h: AssetHost | undefined): boolean {
  if (failed) return true;
  try {
    return h?.sessionStorage?.getItem(FAILED_KEY) === EXPECTED_ASSETS_VERSION;
  } catch {
    return false;
  }
}

/** The app's base when the app has the expected pack and nothing failed from it, else null. */
export function appAssetBase(h: AssetHost | undefined = host(), expected: string = EXPECTED_ASSETS_VERSION): string | null {
  const a = h?.fairplayAssets;
  if (!a || typeof a !== "object") return null;
  const { base, version } = a as { base?: unknown; version?: unknown };
  if (typeof base !== "string" || typeof version !== "string") return null;
  if (!APP_ASSET_BASES.includes(base) || version !== expected) return null;
  if (failedThisSession(h)) return null;
  return base;
}

/** Base URL (ending with "/") to load pack files from right now. */
export function assetBase(): string {
  return appAssetBase() ?? SERVER_ASSET_BASE;
}

export function isAppBase(base: string): boolean {
  return base !== SERVER_ASSET_BASE;
}

/** Something did not load from the app's copy: use the server's for the rest of the session. */
export function markAppAssetsFailed(reason?: unknown): void {
  if (!failed && reason !== undefined) console.warn("FairPlay assets: using the server copy", reason);
  failed = true;
  try {
    host()?.sessionStorage?.setItem(FAILED_KEY, EXPECTED_ASSETS_VERSION);
  } catch {
    /* storage blocked: the flag above still holds for this page */
  }
}

/** Runs `load` with the current base; if that was the app's and it fails, marks the app's copy
 *  failed and runs it once more with the server's. */
export async function withAssetBase<T>(load: (base: string) => Promise<T>): Promise<T> {
  const base = assetBase();
  if (!isAppBase(base)) return load(base);
  try {
    return await load(base);
  } catch (e) {
    markAppAssetsFailed(e);
    return load(SERVER_ASSET_BASE);
  }
}

/** Bytes of one pack file (`path` relative to the pack root), with the same fallback. */
export function fetchAsset(path: string): Promise<Uint8Array> {
  return withAssetBase(async (base) => {
    const r = await fetch(base + path);
    if (!r.ok) throw new Error(`${path}: HTTP ${r.status}`);
    return new Uint8Array(await r.arrayBuffer());
  });
}

/** Tests only. */
export function resetAppAssetsForTests(): void {
  failed = false;
  try {
    host()?.sessionStorage?.removeItem(FAILED_KEY);
  } catch {
    /* ignore */
  }
}
