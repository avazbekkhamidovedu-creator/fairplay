// perf 0.6.2: the asset pack. The page takes the FairPlay app's copy only for this build's
// exact version, falls back to the server's on any failure (for the rest of the session),
// and the build writes the same manifest for the same files.
import { mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  APP_ASSET_BASES,
  EXPECTED_ASSETS_VERSION,
  SERVER_ASSET_BASE,
  appAssetBase,
  assetBase,
  fetchAsset,
  markAppAssetsFailed,
  resetAppAssetsForTests,
  withAssetBase,
} from "../lib/appAssets";
import { PythonRunner, PYODIDE_BASE } from "../lib/code/pythonRunner";
import { startPdfWorker } from "../lib/pdf";
import { buildManifest, contentType, manifestJson, packSources, packVersion, writePack } from "../../scripts/app-assets.js";

const MAC = "fairplay-asset://bundle/";
const WIN = "https://fairplay-assets.example/";
const w = window as unknown as { fairplayAssets?: unknown };

beforeEach(() => {
  resetAppAssetsForTests();
  delete w.fairplayAssets;
  vi.spyOn(console, "warn").mockImplementation(() => {});
});
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  delete w.fairplayAssets;
});

describe("version gating", () => {
  it("the build bakes in the pack's version", () => {
    expect(EXPECTED_ASSETS_VERSION).toMatch(/^[0-9a-f]{16}$/);
    expect(EXPECTED_ASSETS_VERSION).toBe(buildManifest(packSources()).version);
  });

  it("uses the app's base when its version matches", () => {
    for (const base of [MAC, WIN]) {
      w.fairplayAssets = { base, version: EXPECTED_ASSETS_VERSION };
      expect(assetBase()).toBe(base);
    }
    expect(APP_ASSET_BASES).toEqual([MAC, WIN]);
  });

  it("uses the server's copy when the app is missing, has another version, or names another base", () => {
    expect(assetBase()).toBe(SERVER_ASSET_BASE);
    w.fairplayAssets = { base: MAC, version: "0000000000000000" };
    expect(assetBase()).toBe(SERVER_ASSET_BASE);
    w.fairplayAssets = { base: "https://evil.example/", version: EXPECTED_ASSETS_VERSION };
    expect(assetBase()).toBe(SERVER_ASSET_BASE);
    w.fairplayAssets = { base: MAC };
    expect(assetBase()).toBe(SERVER_ASSET_BASE);
    w.fairplayAssets = "fairplay-asset://bundle/";
    expect(assetBase()).toBe(SERVER_ASSET_BASE);
    expect(appAssetBase({ fairplayAssets: { base: WIN, version: "v1" } }, "v1")).toBe(WIN);
    expect(appAssetBase({ fairplayAssets: { base: WIN, version: "v1" } }, "v2")).toBeNull();
  });

  it("after a failure the server's copy is used for the rest of the session", () => {
    w.fairplayAssets = { base: MAC, version: EXPECTED_ASSETS_VERSION };
    markAppAssetsFailed(new Error("x"));
    expect(assetBase()).toBe(SERVER_ASSET_BASE);
    expect(sessionStorage.getItem("fairplay.appAssetsFailed")).toBe(EXPECTED_ASSETS_VERSION);
  });
});

describe("fallback", () => {
  it("withAssetBase retries once with the server's copy when the app's fails", async () => {
    w.fairplayAssets = { base: WIN, version: EXPECTED_ASSETS_VERSION };
    const seen: string[] = [];
    const r = await withAssetBase(async (base) => {
      seen.push(base);
      if (base === WIN) throw new Error("not there");
      return "ok";
    });
    expect(r).toBe("ok");
    expect(seen).toEqual([WIN, SERVER_ASSET_BASE]);
    expect(assetBase()).toBe(SERVER_ASSET_BASE);
  });

  it("a failure from the server's copy is not retried", async () => {
    const load = vi.fn().mockRejectedValue(new Error("down"));
    await expect(withAssetBase(load)).rejects.toThrow("down");
    expect(load).toHaveBeenCalledTimes(1);
  });

  it("fetchAsset falls back on an HTTP error or a network error", async () => {
    w.fairplayAssets = { base: MAC, version: EXPECTED_ASSETS_VERSION };
    const fetchMock = vi.fn(async (url: string) =>
      url.startsWith(MAC) ? new Response("", { status: 404 }) : new Response(new Uint8Array([1, 2, 3])),
    );
    vi.stubGlobal("fetch", fetchMock);
    expect(Array.from(await fetchAsset("pdfjs/cmaps/UniGB-UCS2-H.bcmap"))).toEqual([1, 2, 3]);
    expect(fetchMock.mock.calls.map((c) => c[0])).toEqual([
      `${MAC}pdfjs/cmaps/UniGB-UCS2-H.bcmap`,
      `${SERVER_ASSET_BASE}pdfjs/cmaps/UniGB-UCS2-H.bcmap`,
    ]);
    // remembered: the next file goes straight to the server
    await fetchAsset("pdfjs/standard_fonts/FoxitSans.pfb");
    expect(fetchMock.mock.calls[2]?.[0]).toBe(`${SERVER_ASSET_BASE}pdfjs/standard_fonts/FoxitSans.pfb`);
  });
});

/** A Worker that answers like the real ones, by script and base. */
class FakeWorker {
  static made: FakeWorker[] = [];
  static behave: (w: FakeWorker, msg: unknown) => void = () => {};
  onmessage: ((ev: MessageEvent) => void) | null = null;
  onerror: ((ev: Event) => void) | null = null;
  terminated = false;
  listeners: Record<string, ((ev: Event) => void)[]> = {};
  constructor(public url: string | URL) {
    FakeWorker.made.push(this);
  }
  postMessage(msg: unknown) {
    queueMicrotask(() => FakeWorker.behave(this, msg));
  }
  emit(data: unknown) {
    const ev = new MessageEvent("message", { data });
    this.onmessage?.(ev);
    for (const l of this.listeners.message ?? []) l(ev);
  }
  fail() {
    const ev = new Event("error");
    this.onerror?.(ev);
    for (const l of this.listeners.error ?? []) l(ev);
  }
  addEventListener(t: string, l: (ev: Event) => void) {
    (this.listeners[t] ??= []).push(l);
  }
  removeEventListener(t: string, l: (ev: Event) => void) {
    this.listeners[t] = (this.listeners[t] ?? []).filter((x) => x !== l);
  }
  terminate() {
    this.terminated = true;
  }
}

describe("Python runner", () => {
  beforeEach(() => {
    FakeWorker.made = [];
    vi.stubGlobal("Worker", FakeWorker);
  });

  it("loads from the server's copy in a browser without the app", async () => {
    const bases: string[] = [];
    FakeWorker.behave = (wk, msg) => {
      const m = msg as { type: string; base?: string };
      if (m.type === "load") {
        bases.push(m.base!);
        wk.emit({ type: "ready" });
      }
    };
    const r = new PythonRunner();
    await r.prepare();
    expect(bases).toEqual([PYODIDE_BASE]);
    expect(PYODIDE_BASE).toBe("/app-assets/pyodide/");
    r.dispose();
  });

  it("loads from the app's copy, and falls back to the server's when it fails", async () => {
    w.fairplayAssets = { base: MAC, version: EXPECTED_ASSETS_VERSION };
    const bases: string[] = [];
    FakeWorker.behave = (wk, msg) => {
      const m = msg as { type: string; base?: string };
      if (m.type !== "load") return;
      bases.push(m.base!);
      wk.emit(m.base!.startsWith(MAC) ? { type: "loadError", message: "wasm compile" } : { type: "ready" });
    };
    const r = new PythonRunner();
    await r.prepare();
    expect(bases).toEqual([`${MAC}pyodide/`, "/app-assets/pyodide/"]);
    expect(FakeWorker.made[0]?.terminated).toBe(true);
    // a fresh runner (grading makes one per answer) skips the app's copy now
    const r2 = new PythonRunner();
    await r2.prepare();
    expect(bases[2]).toBe("/app-assets/pyodide/");
    r.dispose();
    r2.dispose();
  });

  it("uses the app's copy when it loads", async () => {
    w.fairplayAssets = { base: WIN, version: EXPECTED_ASSETS_VERSION };
    const bases: string[] = [];
    FakeWorker.behave = (wk, msg) => {
      const m = msg as { type: string; base?: string };
      if (m.type === "load") {
        bases.push(m.base!);
        wk.emit({ type: "ready" });
      }
    };
    const r = new PythonRunner();
    await r.prepare();
    expect(bases).toEqual([`${WIN}pyodide/`]);
    r.dispose();
  });
});

describe("pdf.js worker", () => {
  beforeEach(() => {
    FakeWorker.made = [];
    vi.stubGlobal("Worker", FakeWorker);
    URL.createObjectURL ??= () => "";
    URL.revokeObjectURL ??= () => {};
    vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:x");
    vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => {});
  });

  it("the server's worker is started directly; ready resolves", async () => {
    const p = startPdfWorker(SERVER_ASSET_BASE);
    const wk = FakeWorker.made[0]!;
    expect(String(wk.url)).toBe("/app-assets/pdfjs/pdf.worker.min.mjs");
    wk.emit({ sourceName: "worker", targetName: "main", action: "ready", data: null });
    await expect(p).resolves.toBe(wk);
  });

  it("the app's worker goes through a same-origin blob: module that imports it", async () => {
    let src = "";
    vi.spyOn(URL, "createObjectURL").mockImplementation((b) => {
      const fr = new FileReader();
      fr.onload = () => (src = String(fr.result));
      fr.readAsText(b as Blob);
      return "blob:x";
    });
    const p = startPdfWorker(MAC);
    const wk = FakeWorker.made[0]!;
    expect(wk.url).toBe("blob:x");
    wk.emit({ action: "ready" });
    await p;
    await vi.waitFor(() => expect(src).not.toBe(""));
    expect(src).toContain(`import("${MAC}pdfjs/pdf.worker.min.mjs")`);
  });

  it("rejects (so the server's copy is tried) on an import error, a worker error, or no answer", async () => {
    const a = startPdfWorker(MAC);
    FakeWorker.made[0]!.emit({ fairplayAssetError: "TypeError: Failed to fetch" });
    await expect(a).rejects.toThrow(/Failed to fetch/);
    expect(FakeWorker.made[0]!.terminated).toBe(true);

    const b = startPdfWorker(WIN);
    FakeWorker.made[1]!.fail();
    await expect(b).rejects.toThrow(/failed to load/);

    const c = startPdfWorker(MAC, 10);
    await expect(c).rejects.toThrow(/did not start/);
  });
});

describe("manifest", () => {
  it("is the same for the same files, and covers Pyodide and pdf.js", () => {
    const sources = packSources();
    const a = buildManifest(sources);
    const b = buildManifest(sources);
    expect(manifestJson(a)).toBe(manifestJson(b));
    expect(a.version).toMatch(/^[0-9a-f]{16}$/);
    for (const f of ["pyodide/pyodide.mjs", "pyodide/pyodide.asm.wasm", "pyodide/python_stdlib.zip", "pdfjs/pdf.worker.min.mjs"]) {
      expect(a.files[f]).toMatch(/^[0-9a-f]{64}$/);
    }
    expect(Object.keys(a.files).some((f) => f.startsWith("pdfjs/cmaps/") && f.endsWith(".bcmap"))).toBe(true);
    expect(Object.keys(a.files).some((f) => f.startsWith("pdfjs/standard_fonts/"))).toBe(true);
    expect(Object.keys(a.files)).toEqual([...Object.keys(a.files)].sort());
  });

  it("the version ignores key order and changes when any file changes", () => {
    const files = { "a/x.js": "11", "b/y.wasm": "22" };
    expect(packVersion(files)).toBe(packVersion({ "b/y.wasm": "22", "a/x.js": "11" }));
    expect(packVersion(files)).not.toBe(packVersion({ ...files, "b/y.wasm": "23" }));
    expect(packVersion(files)).not.toBe(packVersion({ ...files, "c/z.json": "33" }));
  });

  it("writes the files and a manifest that matches them", () => {
    const src = mkdtempSync(join(tmpdir(), "fp-pack-src-"));
    writeFileSync(join(src, "one.mjs"), "export {}");
    writeFileSync(join(src, "two.wasm"), new Uint8Array([0, 97, 115, 109]));
    const sources = { "pyodide/two.wasm": join(src, "two.wasm"), "pdfjs/one.mjs": join(src, "one.mjs") };
    const out = mkdtempSync(join(tmpdir(), "fp-pack-out-"));
    const m = writePack(out, sources);
    const disk = JSON.parse(readFileSync(join(out, "manifest.json"), "utf8"));
    expect(disk).toEqual(m);
    expect(Object.keys(disk.files)).toEqual(["pdfjs/one.mjs", "pyodide/two.wasm"]);
    expect(Array.from(readFileSync(join(out, "pyodide/two.wasm")))).toEqual([0, 97, 115, 109]);
    expect(writePack(mkdtempSync(join(tmpdir(), "fp-pack-out-")), sources).version).toBe(m.version);
  });

  it("names the types the apps must send", () => {
    expect(contentType("pyodide/pyodide.asm.wasm")).toBe("application/wasm");
    expect(contentType("pdfjs/pdf.worker.min.mjs")).toBe("text/javascript");
    expect(contentType("pyodide/python_stdlib.zip")).toBe("application/zip");
    expect(contentType("pdfjs/cmaps/Adobe-GB1-0.bcmap")).toBe("application/octet-stream");
    expect(contentType("pdfjs/standard_fonts/LiberationSans-Regular.ttf")).toBe("font/ttf");
  });
});
