// 2026-10-23 (exam tools): the side pane on the right of the exam screen (Materials,
// Websites). It sits under the top bar, the question stays visible to its left (the exam
// page makes room with --stu-pane-w), its left edge can be dragged to resize, ✕ closes it.
import { useEffect, useRef, useState, type KeyboardEvent, type PointerEvent as ReactPointerEvent, type ReactNode } from "react";
import { CloseIcon } from "./icons";
import "../../styles/tools.css";

export const PANE_MIN = 320;
export const PANE_DEFAULT = 520;
const PANE_KEY = "fairplay:pane-width";

/** Width the pane may have in a window this wide (the question keeps at least 360 px). */
export function clampPaneWidth(w: number, viewport: number): number {
  const max = Math.max(PANE_MIN, Math.min(960, viewport - 360));
  return Math.round(Math.min(max, Math.max(PANE_MIN, w)));
}

export function readPaneWidth(): number {
  try {
    const n = Number(localStorage.getItem(PANE_KEY));
    return Number.isFinite(n) && n >= PANE_MIN ? n : PANE_DEFAULT;
  } catch {
    return PANE_DEFAULT;
  }
}

function writePaneWidth(n: number): void {
  try {
    localStorage.setItem(PANE_KEY, String(n));
  } catch {
    /* storage unavailable */
  }
}

/** Top of the pane: right under the exam's top bar (and any banner above it). */
function barBottom(): number {
  const bar = document.querySelector(".stu-lockbar");
  const b = bar?.getBoundingClientRect().bottom ?? 0;
  return Math.max(0, Math.round(b));
}

export function ToolPane({
  title,
  icon,
  width,
  onWidth,
  onClose,
  children,
  labelId,
}: {
  title: ReactNode;
  icon?: ReactNode;
  width: number;
  onWidth: (w: number) => void;
  onClose: () => void;
  children: ReactNode;
  labelId: string;
}) {
  const [top, setTop] = useState(0);
  const drag = useRef<{ id: number } | null>(null);
  const paneRef = useRef<HTMLElement>(null);

  useEffect(() => {
    const update = () => setTop(barBottom());
    update();
    window.addEventListener("resize", update);
    window.addEventListener("scroll", update, true);
    return () => {
      window.removeEventListener("resize", update);
      window.removeEventListener("scroll", update, true);
    };
  }, []);

  useEffect(() => {
    paneRef.current?.querySelector<HTMLElement>("[data-pane-focus]")?.focus();
  }, []);

  const setWidth = (w: number) => {
    const n = clampPaneWidth(w, window.innerWidth);
    onWidth(n);
    writePaneWidth(n);
  };
  const onDown = (e: ReactPointerEvent<HTMLDivElement>) => {
    drag.current = { id: e.pointerId };
    try {
      e.currentTarget.setPointerCapture(e.pointerId);
    } catch {
      /* tests */
    }
    e.preventDefault();
  };
  const onMove = (e: ReactPointerEvent<HTMLDivElement>) => {
    if (drag.current?.id !== e.pointerId) return;
    setWidth(window.innerWidth - e.clientX);
  };
  const onKey = (e: KeyboardEvent<HTMLDivElement>) => {
    const step = e.shiftKey ? 80 : 24;
    if (e.key === "ArrowLeft") setWidth(width + step);
    else if (e.key === "ArrowRight") setWidth(width - step);
    else return;
    e.preventDefault();
  };

  return (
    <aside
      ref={paneRef}
      className="stu-pane"
      style={{ top, width }}
      aria-labelledby={labelId}
      onKeyDown={(e) => {
        if (e.key === "Escape" && !(e.target as HTMLElement).closest("input")) {
          e.stopPropagation();
          onClose();
        }
      }}
    >
      <div
        className="stu-pane-resize"
        role="separator"
        aria-orientation="vertical"
        aria-label="Resize the side panel"
        aria-valuenow={width}
        aria-valuemin={PANE_MIN}
        tabIndex={0}
        onPointerDown={onDown}
        onPointerMove={onMove}
        onPointerUp={() => (drag.current = null)}
        onPointerCancel={() => (drag.current = null)}
        onKeyDown={onKey}
      />
      <header className="stu-pane-head">
        {icon}
        <h2 id={labelId}>{title}</h2>
        <button type="button" className="stu-icon-btn" aria-label="Close panel" onClick={onClose} data-pane-focus>
          <CloseIcon size={18} />
        </button>
      </header>
      <div className="stu-pane-body">{children}</div>
    </aside>
  );
}
