// 2026-10-23 (exam tools): the exam calculator. A floating panel the student drags by its
// title bar (kept inside the window), closes with ✕ or Esc, and that keeps its state while
// they move between questions (it stays mounted; closed = hidden). "scientific" is the
// keypad; "graphing" adds a Graph tab (y = f(x), up to 3 functions). Expressions go
// through lib/calcExpr (own parser, never eval).
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type KeyboardEvent, type PointerEvent as ReactPointerEvent } from "react";
import { CalcError, calculate, compile, formatResult, type AngleMode } from "../../lib/calcExpr";
import { CloseIcon, HomeIcon, MinusIcon, PlusIcon } from "./icons";
import "../../styles/tools.css";

export type CalculatorMode = "scientific" | "graphing";

const EDGE = 8;
const TOP_MIN = 0;

/** Keep a panel of w x h inside the window. */
export function clampPosition(x: number, y: number, w: number, h: number, vw: number, vh: number): { x: number; y: number } {
  return {
    x: Math.round(Math.min(Math.max(EDGE, x), Math.max(EDGE, vw - w - EDGE))),
    y: Math.round(Math.min(Math.max(TOP_MIN + EDGE, y), Math.max(EDGE, vh - h - EDGE))),
  };
}

/** Insert text at the caret (or over the selection). Returns the new text and caret. */
export function insertAt(expr: string, start: number, end: number, text: string): { expr: string; caret: number } {
  const s = Math.max(0, Math.min(start, expr.length));
  const e = Math.max(s, Math.min(end, expr.length));
  return { expr: expr.slice(0, s) + text + expr.slice(e), caret: s + text.length };
}

type Outcome = { ok: true; value: number; text: string } | { ok: false; error: string };

export function evaluateExpr(expr: string, ans: number, angle: AngleMode): Outcome {
  try {
    const v = calculate(expr, { ans, angle });
    // 2026-10-23: √-4, (-8)^(1/2), asin 2: say so instead of showing NaN as an answer
    if (Number.isNaN(v)) return { ok: false, error: "Not a real number" };
    return { ok: true, value: v, text: formatResult(v) };
  } catch (e) {
    return { ok: false, error: e instanceof CalcError ? e.message : "Check the expression" };
  }
}

interface Key {
  label: string;
  /** text inserted at the caret, or an action */
  insert?: string;
  action?: "clear" | "back" | "equals";
  aria?: string;
  kind?: "fn" | "op" | "num" | "act" | "eq";
}

const KEYS: Key[] = [
  { label: "sin", insert: "sin(", kind: "fn" },
  { label: "cos", insert: "cos(", kind: "fn" },
  { label: "tan", insert: "tan(", kind: "fn" },
  { label: "(", insert: "(", kind: "fn", aria: "Open bracket" },
  { label: ")", insert: ")", kind: "fn", aria: "Close bracket" },
  { label: "log", insert: "log(", kind: "fn", aria: "log base 10" },
  { label: "ln", insert: "ln(", kind: "fn", aria: "natural log" },
  { label: "√", insert: "√(", kind: "fn", aria: "Square root" },
  { label: "xʸ", insert: "^", kind: "fn", aria: "Power" },
  { label: "π", insert: "π", kind: "fn", aria: "pi" },
  { label: "7", insert: "7", kind: "num" },
  { label: "8", insert: "8", kind: "num" },
  { label: "9", insert: "9", kind: "num" },
  { label: "÷", insert: "÷", kind: "op", aria: "Divide" },
  { label: "AC", action: "clear", kind: "act", aria: "Clear" },
  { label: "4", insert: "4", kind: "num" },
  { label: "5", insert: "5", kind: "num" },
  { label: "6", insert: "6", kind: "num" },
  { label: "×", insert: "×", kind: "op", aria: "Multiply" },
  { label: "⌫", action: "back", kind: "act", aria: "Backspace" },
  { label: "1", insert: "1", kind: "num" },
  { label: "2", insert: "2", kind: "num" },
  { label: "3", insert: "3", kind: "num" },
  { label: "−", insert: "−", kind: "op", aria: "Minus" },
  { label: "e", insert: "e", kind: "fn", aria: "Euler's number e" },
  { label: "0", insert: "0", kind: "num" },
  { label: ".", insert: ".", kind: "num", aria: "Decimal point" },
  { label: "Ans", insert: "ans", kind: "fn", aria: "Last answer" },
  { label: "+", insert: "+", kind: "op", aria: "Plus" },
  { label: "=", action: "equals", kind: "eq", aria: "Equals" },
];
const STARTS_WITH_OPERATOR = /^[+\-−×÷*/^!]/;

/** 2026-10-23: x for a calculator of width w so it doesn't cover a side panel paneW wide on
 *  the right of a vw-wide window (it moves left of the panel, never off the screen). */
export function avoidPane(x: number, w: number, vw: number, paneW: number): number {
  if (paneW <= 0) return x;
  const limit = vw - paneW - w - 16;
  return x > limit ? Math.max(8, limit) : x;
}

export function Calculator({
  mode,
  open,
  onClose,
  paneWidth = 0,
}: {
  mode: CalculatorMode;
  open: boolean;
  onClose: () => void;
  /** Width of the side panel (materials / websites) open on the right; 0 = none. */
  paneWidth?: number;
}) {
  const panelRef = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState<{ x: number; y: number } | null>(null);
  const [tab, setTab] = useState<"calc" | "graph">("calc");
  const [angle, setAngle] = useState<AngleMode>("deg");
  const drag = useRef<{ dx: number; dy: number; id: number } | null>(null);

  const size = () => {
    const r = panelRef.current?.getBoundingClientRect();
    return { w: r?.width || (mode === "graphing" ? 420 : 336), h: r?.height || 480 };
  };

  // First open: top right, under the bar. Later opens keep where the student left it.
  useLayoutEffect(() => {
    if (!open) return;
    const { w, h } = size();
    // just under the exam's top bar (and any banner above it)
    const below = Math.round(document.querySelector(".stu-lockbar")?.getBoundingClientRect().bottom ?? 72) + 12;
    setPos((p) => clampPosition(p ? p.x : window.innerWidth - w - 24, p ? p.y : below, w, h, window.innerWidth, window.innerHeight));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, tab]);

  // A side panel opened (or got wider): step out of its way.
  useEffect(() => {
    if (!open || paneWidth <= 0) return;
    setPos((p) => {
      if (!p) return p;
      const { w } = size();
      const x = avoidPane(p.x, w, window.innerWidth, paneWidth);
      return x === p.x ? p : { x, y: p.y };
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [paneWidth, open]);

  useEffect(() => {
    const onResize = () =>
      setPos((p) => {
        if (!p) return p;
        const { w, h } = size();
        return clampPosition(p.x, p.y, w, h, window.innerWidth, window.innerHeight);
      });
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Focus moves into the panel when it opens.
  useEffect(() => {
    if (open) panelRef.current?.querySelector<HTMLElement>("[data-autofocus]")?.focus();
  }, [open, tab]);

  const onDragStart = (e: ReactPointerEvent<HTMLDivElement>) => {
    if ((e.target as HTMLElement).closest("button")) return;
    const r = panelRef.current?.getBoundingClientRect();
    if (!r) return;
    drag.current = { dx: e.clientX - r.left, dy: e.clientY - r.top, id: e.pointerId };
    try {
      e.currentTarget.setPointerCapture(e.pointerId);
    } catch {
      /* not supported (tests) */
    }
    e.preventDefault();
  };
  const onDragMove = (e: ReactPointerEvent<HTMLDivElement>) => {
    const d = drag.current;
    if (!d || d.id !== e.pointerId) return;
    const { w, h } = size();
    setPos(clampPosition(e.clientX - d.dx, e.clientY - d.dy, w, h, window.innerWidth, window.innerHeight));
  };
  const onDragEnd = () => {
    drag.current = null;
  };
  // Title bar with the keyboard: arrows move the panel.
  const onHeadKey = (e: KeyboardEvent<HTMLDivElement>) => {
    const step = e.shiftKey ? 40 : 10;
    const d = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] }[e.key];
    if (!d || !pos) return;
    e.preventDefault();
    const { w, h } = size();
    setPos(clampPosition(pos.x + (d[0] ?? 0), pos.y + (d[1] ?? 0), w, h, window.innerWidth, window.innerHeight));
  };

  return (
    <div
      ref={panelRef}
      className={`stu-calc${open ? " open" : ""}${mode === "graphing" ? " graphing" : ""}`}
      role="dialog"
      aria-label="Calculator"
      aria-modal="false"
      aria-hidden={!open}
      {...(open ? {} : { inert: "" })}
      style={pos ? { left: pos.x, top: pos.y } : undefined}
      onKeyDown={(e) => {
        if (e.key === "Escape") {
          e.stopPropagation();
          onClose();
        }
      }}
    >
      <div
        className="stu-calc-head"
        onPointerDown={onDragStart}
        onPointerMove={onDragMove}
        onPointerUp={onDragEnd}
        onPointerCancel={onDragEnd}
        onKeyDown={onHeadKey}
        tabIndex={0}
        aria-label="Calculator title bar. Use the arrow keys to move it."
      >
        <span className="stu-calc-grip" aria-hidden="true" />
        {mode === "graphing" ? (
          <div className="stu-calc-tabs" role="tablist" aria-label="Calculator mode">
            <button type="button" role="tab" aria-selected={tab === "calc"} onClick={() => setTab("calc")}>
              Calculator
            </button>
            <button type="button" role="tab" aria-selected={tab === "graph"} onClick={() => setTab("graph")}>
              Graph
            </button>
          </div>
        ) : (
          <span className="stu-calc-title">Calculator</span>
        )}
        <button
          type="button"
          className="stu-calc-angle"
          aria-label={`Angles in ${angle === "deg" ? "degrees" : "radians"}. Switch to ${angle === "deg" ? "radians" : "degrees"}`}
          onClick={() => setAngle((a) => (a === "deg" ? "rad" : "deg"))}
        >
          {angle === "deg" ? "DEG" : "RAD"}
        </button>
        <button type="button" className="stu-icon-btn stu-calc-close" aria-label="Close calculator" onClick={onClose}>
          <CloseIcon size={16} />
        </button>
      </div>
      <div className="stu-calc-body" hidden={tab !== "calc"}>
        <ScientificPad angle={angle} />
      </div>
      {mode === "graphing" && (
        <div className="stu-calc-body" hidden={tab !== "graph"}>
          <GraphView angle={angle} active={open && tab === "graph"} />
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- keypad

function ScientificPad({ angle }: { angle: AngleMode }) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [expr, setExpr] = useState("");
  const [ans, setAns] = useState(0);
  const [shown, setShown] = useState<{ expr: string; text: string } | null>(null);
  const [error, setError] = useState<string | null>(null);
  // right after "=": an operator continues from Ans, a digit starts over
  const fresh = useRef(false);

  const preview = useMemo(() => {
    if (!expr.trim() || fresh.current) return null;
    const r = evaluateExpr(expr, ans, angle);
    return r.ok ? r.text : null;
  }, [expr, ans, angle]);

  const put = useCallback(
    (text: string) => {
      const el = inputRef.current;
      let base = expr;
      let start = el?.selectionStart ?? base.length;
      let end = el?.selectionEnd ?? base.length;
      if (fresh.current) {
        fresh.current = false;
        base = STARTS_WITH_OPERATOR.test(text) ? "ans" : "";
        start = end = base.length;
      }
      const next = insertAt(base, start, end, text);
      setExpr(next.expr);
      setError(null);
      requestAnimationFrame(() => {
        el?.focus();
        el?.setSelectionRange(next.caret, next.caret);
      });
    },
    [expr],
  );

  const equals = () => {
    if (!expr.trim()) return;
    const r = evaluateExpr(expr, ans, angle);
    if (!r.ok) {
      setError(r.error);
      return;
    }
    setShown({ expr, text: r.text });
    setError(null);
    if (Number.isFinite(r.value)) setAns(r.value);
    fresh.current = true;
  };
  const clear = () => {
    setExpr("");
    setShown(null);
    setError(null);
    fresh.current = false;
    inputRef.current?.focus();
  };
  const back = () => {
    const el = inputRef.current;
    fresh.current = false;
    const s = el?.selectionStart ?? expr.length;
    const e = el?.selectionEnd ?? expr.length;
    if (s === e && s === 0) return;
    const from = s === e ? s - 1 : s;
    const next = expr.slice(0, from) + expr.slice(e);
    setExpr(next);
    setError(null);
    requestAnimationFrame(() => {
      el?.focus();
      el?.setSelectionRange(from, from);
    });
  };

  const press = (k: Key) => {
    if (k.action === "clear") clear();
    else if (k.action === "back") back();
    else if (k.action === "equals") equals();
    else if (k.insert) put(k.insert);
  };

  // Typing while a key (not the field) has focus still goes to the expression.
  const onPadKey = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.target === inputRef.current || e.metaKey || e.ctrlKey || e.altKey) return;
    // Enter / Space on a focused key press that key (the browser's own click)
    if (e.key === "Enter" && (e.target as HTMLElement).tagName === "BUTTON") return;
    if (e.key === "Enter" || e.key === "=") {
      e.preventDefault();
      equals();
    } else if (e.key === "Backspace") {
      e.preventDefault();
      back();
    } else if (/^[0-9.+\-*/^()!a-zA-Z]$/.test(e.key)) {
      e.preventDefault();
      put(e.key);
    }
  };

  return (
    <div className="stu-calc-sci" onKeyDown={onPadKey}>
      <div className={`stu-calc-display${error ? " error" : ""}`}>
        <span className="stu-calc-prev" aria-live="polite">
          {shown ? `${shown.expr} =` : ""}
        </span>
        <label htmlFor="calc-input" className="sr-only">
          Expression
        </label>
        <input
          id="calc-input"
          ref={inputRef}
          data-autofocus
          className="stu-calc-input"
          value={expr}
          spellCheck={false}
          autoComplete="off"
          autoCapitalize="off"
          inputMode="decimal"
          maxLength={500}
          onChange={(e) => {
            fresh.current = false;
            setExpr(e.target.value);
            setError(null);
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              equals();
            }
          }}
          aria-describedby="calc-result"
        />
        <output id="calc-result" className="stu-calc-result" aria-live="polite" data-testid="calc-result">
          {error ?? (fresh.current && shown ? shown.text : preview !== null ? `= ${preview}` : shown && !expr ? shown.text : "")}
        </output>
      </div>
      <div className="stu-calc-keys">
        {KEYS.map((k) => (
          <button
            key={k.label}
            type="button"
            className={`stu-calc-key ${k.kind ?? "num"}`}
            aria-label={k.aria ?? k.label}
            onClick={() => press(k)}
          >
            {k.label}
          </button>
        ))}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- graph

export const GRAPH_COLORS = ["#2F4BD8", "#D9480F", "#16794C"];
const GRAPH_W = 388;
const GRAPH_H = 260;
const MIN_SCALE = 0.5;
const MAX_SCALE = 20000;

export interface GraphViewState {
  /** world point at the centre */
  cx: number;
  cy: number;
  /** pixels per unit */
  scale: number;
}

const HOME: GraphViewState = { cx: 0, cy: 0, scale: 32 };

/** Grid spacing in units: 1, 2 or 5 × 10^k, about every `px` pixels. */
export function niceStep(scale: number, px = 48): number {
  const raw = px / scale;
  const p = Math.pow(10, Math.floor(Math.log10(raw)));
  const m = raw / p;
  return (m < 1.5 ? 1 : m < 3.5 ? 2 : m < 7.5 ? 5 : 10) * p;
}

/** Zoom by `factor` keeping the world point under (px, py) where it is. */
export function zoomAt(v: GraphViewState, factor: number, px: number, py: number, w: number, h: number): GraphViewState {
  const scale = Math.min(MAX_SCALE, Math.max(MIN_SCALE, v.scale * factor));
  const wx = v.cx + (px - w / 2) / v.scale;
  const wy = v.cy - (py - h / 2) / v.scale;
  return { scale, cx: wx - (px - w / 2) / scale, cy: wy + (py - h / 2) / scale };
}

/** Screen points of y = f(x) per pixel column, split where the curve breaks (NaN, a jump
 *  through an asymptote). */
export function samplePlot(
  f: (x: number) => number,
  v: GraphViewState,
  w: number,
  h: number,
): { x: number; y: number }[][] {
  const runs: { x: number; y: number }[][] = [];
  let run: { x: number; y: number }[] = [];
  let prev: number | null = null;
  for (let px = 0; px <= w; px++) {
    const x = v.cx + (px - w / 2) / v.scale;
    let y: number;
    try {
      y = f(x);
    } catch {
      y = NaN;
    }
    const py = h / 2 - (y - v.cy) * v.scale;
    if (!Number.isFinite(py) || Math.abs(py) > 1e6 || (prev !== null && Math.abs(py - prev) > h * 1.5)) {
      if (run.length > 1) runs.push(run);
      run = [];
      prev = Number.isFinite(py) && Math.abs(py) <= 1e6 ? py : null;
      if (prev !== null) run.push({ x: px, y: py });
      continue;
    }
    run.push({ x: px, y: py });
    prev = py;
  }
  if (run.length > 1) runs.push(run);
  return runs;
}

function label(n: number, step: number): string {
  const digits = Math.max(0, -Math.floor(Math.log10(step)));
  const t = Math.abs(n) < step / 1e6 ? 0 : n;
  return digits > 0 ? t.toFixed(Math.min(digits, 8)).replace(/\.?0+$/, "") : String(Math.round(t));
}

function GraphView({ angle, active }: { angle: AngleMode; active: boolean }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [fns, setFns] = useState<string[]>(["", "", ""]);
  const [view, setView] = useState<GraphViewState>(HOME);
  const [cursor, setCursor] = useState<{ x: number; y: number } | null>(null);
  const pan = useRef<{ x: number; y: number; id: number } | null>(null);

  const compiled = useMemo(
    () =>
      fns.map((src) => {
        if (!src.trim()) return { f: null, error: null };
        try {
          const run = compile(src, { x: true });
          return { f: (x: number) => run({ x, angle }), error: null };
        } catch (e) {
          return { f: null, error: e instanceof CalcError ? e.message : "Check the function" };
        }
      }),
    [fns, angle],
  );

  useEffect(() => {
    if (!active) return;
    const canvas = canvasRef.current;
    const ctx = canvas?.getContext?.("2d");
    if (!canvas || !ctx) return;
    const dpr = window.devicePixelRatio || 1;
    canvas.width = GRAPH_W * dpr;
    canvas.height = GRAPH_H * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    draw(ctx, view, compiled.map((c) => c.f));
  }, [view, compiled, active]);

  const toWorld = (px: number, py: number) => ({
    x: view.cx + (px - GRAPH_W / 2) / view.scale,
    y: view.cy - (py - GRAPH_H / 2) / view.scale,
  });
  const local = (e: { clientX: number; clientY: number }) => {
    const r = canvasRef.current?.getBoundingClientRect();
    const sx = r && r.width ? GRAPH_W / r.width : 1;
    const sy = r && r.height ? GRAPH_H / r.height : 1;
    return { px: (e.clientX - (r?.left ?? 0)) * sx, py: (e.clientY - (r?.top ?? 0)) * sy };
  };

  // Wheel zoom must be able to preventDefault (React's onWheel is passive).
  useEffect(() => {
    const el = canvasRef.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const { px, py } = local(e);
      setView((v) => zoomAt(v, e.deltaY < 0 ? 1.15 : 1 / 1.15, px, py, GRAPH_W, GRAPH_H));
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const zoom = (factor: number) => setView((v) => zoomAt(v, factor, GRAPH_W / 2, GRAPH_H / 2, GRAPH_W, GRAPH_H));

  return (
    <div className="stu-graph">
      <div className="stu-graph-fns">
        {fns.map((src, i) => (
          <div key={i} className="stu-graph-fn">
            <span className="stu-graph-swatch" style={{ background: GRAPH_COLORS[i] }} aria-hidden="true" />
            <label htmlFor={`graph-fn-${i}`} className="stu-graph-y">
              y<sub>{i + 1}</sub> =
            </label>
            <input
              id={`graph-fn-${i}`}
              className="stu-graph-input"
              value={src}
              placeholder={i === 0 ? "e.g. x^2 − 2x" : ""}
              spellCheck={false}
              autoComplete="off"
              maxLength={200}
              data-autofocus={i === 0 ? "" : undefined}
              aria-invalid={!!compiled[i]?.error}
              aria-describedby={compiled[i]?.error ? `graph-fn-${i}-err` : undefined}
              onChange={(e) => setFns((list) => list.map((s, j) => (j === i ? e.target.value : s)))}
            />
            {compiled[i]?.error && (
              <span id={`graph-fn-${i}-err`} className="stu-graph-err" role="alert">
                {compiled[i]?.error}
              </span>
            )}
          </div>
        ))}
      </div>
      <div className="stu-graph-canvas-wrap">
        <canvas
          ref={canvasRef}
          className="stu-graph-canvas"
          width={GRAPH_W}
          height={GRAPH_H}
          style={{ width: GRAPH_W, height: GRAPH_H }}
          tabIndex={0}
          role="img"
          aria-label="Graph. Drag or use the arrow keys to move, plus and minus to zoom."
          onPointerDown={(e) => {
            pan.current = { x: e.clientX, y: e.clientY, id: e.pointerId };
            try {
              e.currentTarget.setPointerCapture(e.pointerId);
            } catch {
              /* tests */
            }
          }}
          onPointerMove={(e) => {
            const p = pan.current;
            if (p && p.id === e.pointerId) {
              const r = canvasRef.current?.getBoundingClientRect();
              const k = r && r.width ? GRAPH_W / r.width : 1;
              const dx = (e.clientX - p.x) * k;
              const dy = (e.clientY - p.y) * k;
              pan.current = { ...p, x: e.clientX, y: e.clientY };
              setView((v) => ({ ...v, cx: v.cx - dx / v.scale, cy: v.cy + dy / v.scale }));
            } else {
              const { px, py } = local(e);
              setCursor(toWorld(px, py));
            }
          }}
          onPointerUp={() => (pan.current = null)}
          onPointerCancel={() => (pan.current = null)}
          onPointerLeave={() => setCursor(null)}
          onKeyDown={(e) => {
            const step = 40 / view.scale;
            const moves: Record<string, [number, number]> = {
              ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, step], ArrowDown: [0, -step],
            };
            const m = moves[e.key];
            if (m) {
              e.preventDefault();
              setView((v) => ({ ...v, cx: v.cx + m[0], cy: v.cy + m[1] }));
            } else if (e.key === "+" || e.key === "=") {
              e.preventDefault();
              zoom(1.25);
            } else if (e.key === "-" || e.key === "_") {
              e.preventDefault();
              zoom(1 / 1.25);
            }
          }}
        />
        <div className="stu-graph-tools">
          <button type="button" className="stu-icon-btn" aria-label="Zoom in" onClick={() => zoom(1.25)}>
            <PlusIcon />
          </button>
          <button type="button" className="stu-icon-btn" aria-label="Zoom out" onClick={() => zoom(1 / 1.25)}>
            <MinusIcon />
          </button>
          <button type="button" className="stu-icon-btn" aria-label="Back to the start view" onClick={() => setView(HOME)}>
            <HomeIcon />
          </button>
        </div>
        <span className="stu-graph-coords" aria-hidden="true">
          {cursor ? `x ${formatResult(Number(cursor.x.toPrecision(4)))} · y ${formatResult(Number(cursor.y.toPrecision(4)))}` : ""}
        </span>
      </div>
      <p className="stu-graph-hint">Drag to move · scroll or +/− to zoom{angle === "deg" ? " · trig in degrees" : ""}</p>
    </div>
  );
}

function draw(ctx: CanvasRenderingContext2D, v: GraphViewState, fns: (((x: number) => number) | null)[]) {
  const w = GRAPH_W;
  const h = GRAPH_H;
  ctx.clearRect(0, 0, w, h);
  ctx.fillStyle = "#ffffff";
  ctx.fillRect(0, 0, w, h);
  const step = niceStep(v.scale);
  const left = v.cx - w / 2 / v.scale;
  const right = v.cx + w / 2 / v.scale;
  const bottom = v.cy - h / 2 / v.scale;
  const top = v.cy + h / 2 / v.scale;
  const sx = (x: number) => w / 2 + (x - v.cx) * v.scale;
  const sy = (y: number) => h / 2 - (y - v.cy) * v.scale;

  // minor + major grid
  ctx.lineWidth = 1;
  for (const [s, color] of [
    [step / 5, "#f2f4f8"],
    [step, "#e2e6ec"],
  ] as const) {
    ctx.strokeStyle = color;
    ctx.beginPath();
    for (let x = Math.ceil(left / s) * s; x <= right; x += s) {
      const X = Math.round(sx(x)) + 0.5;
      ctx.moveTo(X, 0);
      ctx.lineTo(X, h);
    }
    for (let y = Math.ceil(bottom / s) * s; y <= top; y += s) {
      const Y = Math.round(sy(y)) + 0.5;
      ctx.moveTo(0, Y);
      ctx.lineTo(w, Y);
    }
    ctx.stroke();
  }
  // axes
  const ax = Math.min(h - 1, Math.max(0, sy(0)));
  const ay = Math.min(w - 1, Math.max(0, sx(0)));
  ctx.strokeStyle = "#4a5568";
  ctx.lineWidth = 1.25;
  ctx.beginPath();
  ctx.moveTo(0, Math.round(ax) + 0.5);
  ctx.lineTo(w, Math.round(ax) + 0.5);
  ctx.moveTo(Math.round(ay) + 0.5, 0);
  ctx.lineTo(Math.round(ay) + 0.5, h);
  ctx.stroke();
  // labels
  ctx.fillStyle = "#6b7686";
  ctx.font = "10px system-ui, sans-serif";
  ctx.textAlign = "center";
  ctx.textBaseline = "top";
  const ly = ax > h - 14 ? ax - 13 : ax + 3;
  for (let x = Math.ceil(left / step) * step; x <= right; x += step) {
    if (Math.abs(x) < step / 2) continue;
    ctx.fillText(label(x, step), sx(x), ly);
  }
  ctx.textAlign = ay > w - 30 ? "right" : "left";
  ctx.textBaseline = "middle";
  const lx = ay > w - 30 ? ay - 4 : ay + 4;
  for (let y = Math.ceil(bottom / step) * step; y <= top; y += step) {
    if (Math.abs(y) < step / 2) continue;
    ctx.fillText(label(y, step), lx, sy(y));
  }
  // curves
  ctx.lineWidth = 2.25;
  ctx.lineJoin = "round";
  fns.forEach((f, i) => {
    if (!f) return;
    ctx.strokeStyle = GRAPH_COLORS[i] ?? "#2F4BD8";
    for (const run of samplePlot(f, v, w, h)) {
      ctx.beginPath();
      run.forEach((p, j) => (j === 0 ? ctx.moveTo(p.x, p.y) : ctx.lineTo(p.x, p.y)));
      ctx.stroke();
    }
  });
}
