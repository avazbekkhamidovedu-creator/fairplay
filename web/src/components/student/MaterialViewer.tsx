// 2026-10-23 (exam tools): open-book files shown inside FairPlay. PDF (pdf.js: one page at
// a time on a canvas, page list, zoom, page jump, text search), images (zoom, drag to
// move), TXT / MD as plain text. There is no download, print or open-in-a-new-window: the
// bytes stay in this page (blob URLs only for <img>, revoked when closed).
import { useEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent, type RefObject } from "react";
import type { MaterialFile } from "../../api/types";
import { openPdf, pageText, pagesMatching, type PdfDoc } from "../../lib/pdf";
import { ArrowLeftIcon, ArrowRightIcon, MinusIcon, PlusIcon, SearchIcon } from "./icons";
import "../../styles/tools.css";

const ZOOMS = [0.5, 0.75, 1, 1.25, 1.5, 2, 3, 4];

export function nextZoom(z: number, dir: 1 | -1): number {
  if (dir > 0) return ZOOMS.find((s) => s > z + 1e-6) ?? ZOOMS[ZOOMS.length - 1]!;
  return [...ZOOMS].reverse().find((s) => s < z - 1e-6) ?? ZOOMS[0]!;
}

type Load = () => Promise<Blob>;

export function MaterialViewer({ file, load }: { file: MaterialFile; load: Load }) {
  const [blob, setBlob] = useState<Blob | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    setBlob(null);
    setError(null);
    load().then(
      (b) => alive && setBlob(b),
      () => alive && setError("This file can't be opened right now. It needs the connection."),
    );
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [file.id]);

  return (
    <div className="stu-viewer" onContextMenu={(e) => e.preventDefault()} data-testid="material-viewer">
      {error ? (
        <p className="stu-alert warn" role="alert">
          {error}
        </p>
      ) : !blob ? (
        <p className="stu-muted stu-viewer-wait" role="status">
          Opening {file.name}…
        </p>
      ) : file.kind === "pdf" ? (
        <PdfView blob={blob} name={file.name} />
      ) : file.kind === "png" || file.kind === "jpg" ? (
        <ImageView blob={blob} name={file.name} />
      ) : (
        <TextView blob={blob} />
      )}
    </div>
  );
}

// ---------------------------------------------------------------- text

function TextView({ blob }: { blob: Blob }) {
  const [text, setText] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    blobText(blob).then((t) => alive && setText(t));
    return () => {
      alive = false;
    };
  }, [blob]);
  if (text === null) return null;
  return <pre className="stu-viewer-text">{text}</pre>;
}

function blobText(b: Blob): Promise<string> {
  if (typeof b.text === "function") return b.text();
  return new Promise((resolve) => {
    const r = new FileReader();
    r.onload = () => resolve(String(r.result ?? ""));
    r.onerror = () => resolve("");
    r.readAsText(b);
  });
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

// ---------------------------------------------------------------- image

function ImageView({ blob, name }: { blob: Blob; name: string }) {
  const url = useMemo(() => URL.createObjectURL(blob), [blob]);
  useEffect(() => () => URL.revokeObjectURL(url), [url]);
  const [zoom, setZoom] = useState(1);
  const [natural, setNatural] = useState<{ w: number; h: number } | null>(null);
  const boxRef = useRef<HTMLDivElement>(null);
  const pan = useDragScroll(boxRef);
  const fitW = boxRef.current?.clientWidth ? boxRef.current.clientWidth - 24 : 480;
  const base = natural ? Math.min(1, fitW / natural.w) : 1;
  return (
    <>
      <div className="stu-viewer-bar">
        <ZoomControls zoom={zoom} onZoom={setZoom} />
      </div>
      <div className="stu-viewer-canvas grab" ref={boxRef} {...pan}>
        <img
          src={url}
          alt={name}
          draggable={false}
          onLoad={(e) => setNatural({ w: e.currentTarget.naturalWidth, h: e.currentTarget.naturalHeight })}
          style={natural ? { width: Math.round(natural.w * base * zoom), height: "auto" } : { maxWidth: "100%" }}
        />
      </div>
    </>
  );
}

function ZoomControls({ zoom, onZoom, fit }: { zoom: number; onZoom: (z: number) => void; fit?: () => void }) {
  return (
    <span className="stu-viewer-group">
      <button type="button" className="stu-icon-btn" aria-label="Zoom out" onClick={() => onZoom(nextZoom(zoom, -1))} disabled={zoom <= ZOOMS[0]!}>
        <MinusIcon />
      </button>
      <span className="stu-viewer-zoom" aria-live="polite">
        {Math.round(zoom * 100)}%
      </span>
      <button type="button" className="stu-icon-btn" aria-label="Zoom in" onClick={() => onZoom(nextZoom(zoom, 1))} disabled={zoom >= ZOOMS[ZOOMS.length - 1]!}>
        <PlusIcon />
      </button>
      <button type="button" className="stu-btn sm" onClick={fit ?? (() => onZoom(1))}>
        Fit
      </button>
    </span>
  );
}

/** Drag inside a scrolling box to move what is in it (images, big PDF pages). */
function useDragScroll(ref: RefObject<HTMLDivElement>) {
  const d = useRef<{ x: number; y: number; l: number; t: number; id: number } | null>(null);
  return {
    onPointerDown: (e: ReactPointerEvent<HTMLDivElement>) => {
      if (e.button !== 0 || !ref.current) return;
      d.current = { x: e.clientX, y: e.clientY, l: ref.current.scrollLeft, t: ref.current.scrollTop, id: e.pointerId };
      try {
        e.currentTarget.setPointerCapture(e.pointerId);
      } catch {
        /* tests */
      }
    },
    onPointerMove: (e: ReactPointerEvent<HTMLDivElement>) => {
      const s = d.current;
      if (!s || s.id !== e.pointerId || !ref.current) return;
      ref.current.scrollLeft = s.l - (e.clientX - s.x);
      ref.current.scrollTop = s.t - (e.clientY - s.y);
    },
    onPointerUp: () => (d.current = null),
    onPointerCancel: () => (d.current = null),
  };
}

// ---------------------------------------------------------------- pdf

function PdfView({ blob, name }: { blob: Blob; name: string }) {
  const [doc, setDoc] = useState<PdfDoc | null>(null);
  const [failed, setFailed] = useState(false);
  const [page, setPage] = useState(1);
  const [pageInput, setPageInput] = useState("1");
  const [zoom, setZoom] = useState(1);
  const [query, setQuery] = useState("");
  const [texts, setTexts] = useState<string[] | null>(null);
  const [searching, setSearching] = useState(false);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const boxRef = useRef<HTMLDivElement>(null);
  const pan = useDragScroll(boxRef);

  useEffect(() => {
    let alive = true;
    let opened: PdfDoc | null = null;
    blobBytes(blob)
      .then((bytes) => openPdf(bytes))
      .then(
        (d) => {
          opened = d;
          if (alive) setDoc(d);
          else void d.destroy();
        },
        () => alive && setFailed(true),
      );
    return () => {
      alive = false;
      if (opened) void opened.destroy();
    };
  }, [blob]);

  // draw the current page (fit to the pane's width at 100 %)
  useEffect(() => {
    if (!doc) return;
    let cancelled = false;
    let task: { cancel(): void } | null = null;
    void doc.getPage(page).then((p) => {
      if (cancelled) return;
      const canvas = canvasRef.current;
      const ctx = canvas?.getContext?.("2d");
      if (!canvas || !ctx) return;
      const avail = Math.max(200, (boxRef.current?.clientWidth ?? 600) - 24);
      const base = p.getViewport({ scale: 1 });
      const css = (avail / base.width) * zoom;
      const dpr = window.devicePixelRatio || 1;
      const vp = p.getViewport({ scale: css * dpr });
      canvas.width = Math.floor(vp.width);
      canvas.height = Math.floor(vp.height);
      canvas.style.width = `${Math.floor(vp.width / dpr)}px`;
      canvas.style.height = `${Math.floor(vp.height / dpr)}px`;
      const r = p.render({ canvasContext: ctx, viewport: vp });
      task = r;
      r.promise.catch(() => undefined);
    });
    return () => {
      cancelled = true;
      task?.cancel();
    };
  }, [doc, page, zoom]);

  useEffect(() => setPageInput(String(page)), [page]);

  const total = doc?.numPages ?? 0;
  const go = (n: number) => {
    if (!total) return;
    setPage(Math.min(total, Math.max(1, Math.round(n))));
    boxRef.current?.scrollTo?.({ top: 0 });
  };

  const search = async () => {
    if (!doc || !query.trim()) return;
    let t = texts;
    if (!t) {
      setSearching(true);
      t = [];
      for (let i = 1; i <= doc.numPages; i++) {
        try {
          t.push(await pageText(await doc.getPage(i)));
        } catch {
          t.push("");
        }
      }
      setTexts(t);
      setSearching(false);
    }
    const hits = pagesMatching(t, query);
    const next = hits.find((n) => n > page) ?? hits[0];
    if (next) go(next);
  };
  const hits = texts ? pagesMatching(texts, query) : [];

  if (failed)
    return (
      <p className="stu-alert warn" role="alert">
        {name} could not be opened. The file may be damaged.
      </p>
    );
  if (!doc)
    return (
      <p className="stu-muted stu-viewer-wait" role="status">
        Opening {name}…
      </p>
    );
  return (
    <div className="stu-pdf">
      <div className="stu-viewer-bar">
        <span className="stu-viewer-group">
          <button type="button" className="stu-icon-btn" aria-label="Previous page" onClick={() => go(page - 1)} disabled={page <= 1}>
            <ArrowLeftIcon size={16} />
          </button>
          <form
            className="stu-viewer-page"
            onSubmit={(e) => {
              e.preventDefault();
              const n = Number(pageInput);
              if (Number.isFinite(n)) go(n);
              else setPageInput(String(page));
            }}
          >
            <label htmlFor="pdf-page">Page</label>
            <input
              id="pdf-page"
              inputMode="numeric"
              value={pageInput}
              onChange={(e) => setPageInput(e.target.value.replace(/[^0-9]/g, "").slice(0, 5))}
              onBlur={() => setPageInput(String(page))}
            />
            <span>of {total}</span>
          </form>
          <button type="button" className="stu-icon-btn" aria-label="Next page" onClick={() => go(page + 1)} disabled={page >= total}>
            <ArrowRightIcon size={16} />
          </button>
        </span>
        <ZoomControls zoom={zoom} onZoom={setZoom} />
        <form
          className="stu-viewer-search"
          role="search"
          onSubmit={(e) => {
            e.preventDefault();
            void search();
          }}
        >
          <label htmlFor="pdf-search" className="sr-only">
            Search in this PDF
          </label>
          <input
            id="pdf-search"
            value={query}
            placeholder="Search"
            onChange={(e) => setQuery(e.target.value)}
          />
          <button type="submit" className="stu-icon-btn" aria-label="Find next" disabled={!query.trim() || searching}>
            <SearchIcon />
          </button>
          <span className="stu-viewer-hits" aria-live="polite">
            {searching ? "Searching…" : texts && query.trim() ? (hits.length ? `${hits.length} ${hits.length === 1 ? "page" : "pages"}` : "Not found") : ""}
          </span>
        </form>
      </div>
      <div className="stu-pdf-main">
        <nav className="stu-pdf-pages" aria-label="Pages">
          {Array.from({ length: total }, (_, i) => i + 1).map((n) => (
            <button
              key={n}
              type="button"
              className={`stu-pdf-pagebtn${n === page ? " on" : ""}${hits.includes(n) ? " hit" : ""}`}
              aria-current={n === page ? "page" : undefined}
              aria-label={`Page ${n}${hits.includes(n) ? ", has a match" : ""}`}
              onClick={() => go(n)}
            >
              {n}
            </button>
          ))}
        </nav>
        <div className="stu-viewer-canvas grab" ref={boxRef} {...pan}>
          <canvas ref={canvasRef} aria-label={`${name}, page ${page} of ${total}`} role="img" />
        </div>
      </div>
    </div>
  );
}
