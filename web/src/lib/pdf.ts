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
