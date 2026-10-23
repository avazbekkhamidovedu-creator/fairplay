// 2026-10-23 (exam tools): PDF materials are drawn with pdf.js, bundled from npm (no CDN:
// exams run in China and inside locked apps). Loaded only when a PDF is opened. Its worker
// is a file Vite emits next to the app (worker-src 'self'); fonts and CJK character maps
// come from /pdfjs/ on our own server (copied there at build, see vite.config.ts).
// isEvalSupported is off (and the page's CSP has no unsafe-eval anyway).

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

const assetBase = import.meta.env?.DEV ? "/node_modules/pdfjs-dist/" : "/pdfjs/";

export async function openPdf(data: Uint8Array): Promise<PdfDoc> {
  const pdfjs = await import("pdfjs-dist/legacy/build/pdf.mjs");
  const worker = await import("pdfjs-dist/legacy/build/pdf.worker.min.mjs?url");
  pdfjs.GlobalWorkerOptions.workerSrc = worker.default;
  const task = pdfjs.getDocument({
    data,
    isEvalSupported: false,
    enableXfa: false,
    disableAutoFetch: true,
    disableStream: true,
    cMapUrl: `${assetBase}cmaps/`,
    cMapPacked: true,
    standardFontDataUrl: `${assetBase}standard_fonts/`,
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
