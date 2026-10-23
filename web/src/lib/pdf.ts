// 2026-10-23 (exam tools): PDF materials are drawn with pdf.js, bundled from npm (no CDN:
// exams run in China and inside locked apps). Loaded only when a PDF is opened.
// isEvalSupported is off (and the page's CSP has no unsafe-eval anyway).
// 2026-10-23 (perf 0.6.2): the worker, fonts and character maps are files of the asset pack
// (appAssets.ts): from the FairPlay app's own copy when it has this build's pack, else from
// /app-assets/ on the server. A worker from the app's origin cannot be started directly, so a
// same-origin blob: module worker imports it; pdf.js then gets that Worker as its port. One
// worker serves every PDF of the page. Fonts and character maps are fetched by the page
// (useWorkerFetch off) through fetchAsset, which falls back to the server the same way.
import { assetBase, fetchAsset, isAppBase, withAssetBase } from "./appAssets";

export interface PdfPage {
  getViewport(o: { scale: number }): { width: number; height: number };
  render(o: { canvasContext: CanvasRenderingContext2D; viewport: unknown }): { promise: Promise<void>; cancel(): void };
  getTextContent(): Promise<{ items: unknown[] }>;
  cleanup(): void;
}

export interface PdfDoc {
  numPages: number;
  getPage(n: number): Promise<PdfPage>;
  destroy(): Promise<void>;
}

/** How long the worker may take to start before the other copy is tried. */
export const WORKER_START_MS = 20_000;
export const PDF_WORKER_PATH = "pdfjs/pdf.worker.min.mjs";

type PdfJs = typeof import("pdfjs-dist/legacy/build/pdf.mjs");
type PdfWorker = InstanceType<PdfJs["PDFWorker"]>;

let shared: Promise<PdfWorker> | null = null;

/** Starts the pdf.js worker from `base` and resolves once it says "ready". */
export function startPdfWorker(base: string, timeoutMs = WORKER_START_MS): Promise<Worker> {
  const url = base + PDF_WORKER_PATH;
  let w: Worker;
  let blobUrl: string | null = null;
  if (isAppBase(base)) {
    const src = `import(${JSON.stringify(url)}).catch((e) => postMessage({ fairplayAssetError: String(e) }));\n`;
    blobUrl = URL.createObjectURL(new Blob([src], { type: "text/javascript" }));
    w = new Worker(blobUrl, { type: "module" });
  } else {
    w = new Worker(url, { type: "module" });
  }
  return new Promise<Worker>((resolve, reject) => {
    const done = (err: Error | null) => {
      clearTimeout(timer);
      w.removeEventListener("message", onMessage);
      w.removeEventListener("error", onError);
      if (blobUrl) URL.revokeObjectURL(blobUrl);
      if (err) {
        w.terminate();
        reject(err);
      } else {
        resolve(w);
      }
    };
    const onMessage = (ev: MessageEvent) => {
      const d = ev.data as { action?: unknown; fairplayAssetError?: unknown } | null;
      if (d && typeof d === "object" && "fairplayAssetError" in d) done(new Error(`pdf.js worker: ${String(d.fairplayAssetError)}`));
      else if (d && typeof d === "object" && d.action === "ready") done(null);
    };
    const onError = (ev: Event) => {
      ev.preventDefault?.();
      done(new Error(`pdf.js worker failed to load from ${url}`));
    };
    const timer = setTimeout(() => done(new Error(`pdf.js worker did not start from ${url}`)), timeoutMs);
    w.addEventListener("message", onMessage);
    w.addEventListener("error", onError);
  });
}

// (the d.ts types PDFWorker's port as null; any Worker works)
function pdfWorker(pdfjs: PdfJs): Promise<PdfWorker> {
  shared ??= withAssetBase((base) => startPdfWorker(base))
    .then((port) => new pdfjs.PDFWorker({ port: port as never }))
    .catch((e) => {
      shared = null;
      throw e;
    });
  return shared;
}

/** pdf.js asks the page for character maps and fonts (useWorkerFetch off). */
class PackCMapFactory {
  constructor(_o: unknown) {}
  async fetch({ name }: { name: string }) {
    return { cMapData: await fetchAsset(`pdfjs/cmaps/${name}.bcmap`), isCompressed: true };
  }
}

class PackFontFactory {
  constructor(_o: unknown) {}
  fetch({ filename }: { filename: string }) {
    return fetchAsset(`pdfjs/standard_fonts/${filename}`);
  }
}

export async function openPdf(data: Uint8Array): Promise<PdfDoc> {
  const pdfjs = await import("pdfjs-dist/legacy/build/pdf.mjs");
  const worker = await pdfWorker(pdfjs);
  const base = assetBase();
  const task = pdfjs.getDocument({
    data,
    worker,
    isEvalSupported: false,
    enableXfa: false,
    disableAutoFetch: true,
    disableStream: true,
    useWorkerFetch: false,
    cMapUrl: `${base}pdfjs/cmaps/`,
    cMapPacked: true,
    standardFontDataUrl: `${base}pdfjs/standard_fonts/`,
    CMapReaderFactory: PackCMapFactory as never,
    StandardFontDataFactory: PackFontFactory as never,
  });
  return (await task.promise) as unknown as PdfDoc;
}

/** 2026-10-23 (review): pdf.js rejects a PDF that needs a password with a PasswordException
 *  (no onPassword handler is set, so it never asks). */
export function isPasswordError(e: unknown): boolean {
  return !!e && typeof e === "object" && (e as { name?: unknown }).name === "PasswordException";
}

/** 2026-10-23 (review): does this PDF need a password to open? Cheap first: "/Encrypt" in
 *  the first or last 64 KB (the trailer). Only then pdf.js tries to open it: a PDF with an
 *  owner password only (printing / copying limits) opens fine and is accepted. */
export async function pdfNeedsPassword(file: Blob, open: (d: Uint8Array) => Promise<PdfDoc> = openPdf): Promise<boolean> {
  const SPAN = 64 * 1024;
  const head = await blobBytes(file.slice(0, SPAN));
  const tail = file.size > SPAN ? await blobBytes(file.slice(Math.max(SPAN, file.size - SPAN))) : new Uint8Array();
  if (!hasEncrypt(head) && !hasEncrypt(tail)) return false;
  try {
    const doc = await open(await blobBytes(file));
    void doc.destroy();
    return false;
  } catch (e) {
    return isPasswordError(e);
  }
}

const ENCRYPT = [0x2f, 0x45, 0x6e, 0x63, 0x72, 0x79, 0x70, 0x74]; // "/Encrypt"

function hasEncrypt(b: Uint8Array): boolean {
  outer: for (let i = 0; i + ENCRYPT.length <= b.length; i++) {
    for (let j = 0; j < ENCRYPT.length; j++) if (b[i + j] !== ENCRYPT[j]) continue outer;
    return true;
  }
  return false;
}

function blobBytes(b: Blob): Promise<Uint8Array> {
  if (typeof b.arrayBuffer === "function") return b.arrayBuffer().then((ab) => new Uint8Array(ab));
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(new Uint8Array(r.result as ArrayBuffer));
    r.onerror = () => reject(r.error);
    r.readAsArrayBuffer(b);
  });
}

/** Plain text of a page (for the search box). */
export async function pageText(page: PdfPage): Promise<string> {
  const tc = await page.getTextContent();
  return tc.items
    .map((it) => (it && typeof it === "object" && "str" in it ? String((it as { str: unknown }).str) : ""))
    .join(" ");
}

/** Pages (1-based) whose text contains `query` (case-insensitive). */
export function pagesMatching(texts: string[], query: string): number[] {
  const q = query.trim().toLowerCase();
  if (!q) return [];
  const out: number[] = [];
  texts.forEach((t, i) => {
    if (t.toLowerCase().includes(q)) out.push(i + 1);
  });
  return out;
}
