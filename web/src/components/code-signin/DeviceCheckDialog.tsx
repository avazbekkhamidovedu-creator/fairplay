import { useEffect, useRef } from "react";
import type { PreflightReport } from "../../lib/bridge";

export type DeviceCheckState =
  | { kind: "idle" }
  | { kind: "running" }
  | { kind: "none" }
  | { kind: "done"; report: PreflightReport };

type Pill = "ok" | "info" | "problem";
const PILL_TEXT: Record<Pill, string> = { ok: "OK", info: "Info", problem: "Problem" };

function rowsOf(report: PreflightReport): Array<{ id: string; pill: Pill; text: string; detail?: string }> {
  // Older apps and Windows builds send only `problems`: show those as rows.
  const rows = report.checks.length
    ? report.checks
    : report.problems.map((p, i) => ({ id: `problem_${i}`, ok: false, message: p, info: false }));
  if (rows.length === 0) return [{ id: "all_ok", pill: "ok", text: "Everything looks ready" }];
  return rows.map((c) => ({
    id: c.id,
    pill: c.ok ? "ok" : c.info ? "info" : "problem",
    text: c.message || c.id,
    detail: c.info && !c.ok ? "Just so you know: this does not stop the exam." : undefined,
  }));
}

const FOCUSABLE = "button:not([disabled]), [href], input, select, textarea, [tabindex]:not([tabindex='-1'])";

export function DeviceCheckDialog({
  state,
  onAgain,
  onClose,
}: {
  state: DeviceCheckState;
  onAgain: () => void;
  onClose: () => void;
}) {
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const before = document.activeElement as HTMLElement | null;
    box.current?.focus();
    return () => before?.focus();
  }, []);

  useEffect(() => {
    const esc = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", esc);
    return () => window.removeEventListener("keydown", esc);
  }, [onClose]);

  function keyDown(e: React.KeyboardEvent) {
    if (e.key !== "Tab" || !box.current) return;
    const items = Array.from(box.current.querySelectorAll<HTMLElement>(FOCUSABLE));
    if (items.length === 0) return;
    const first = items[0]!;
    const last = items[items.length - 1]!;
    const active = document.activeElement;
    if (e.shiftKey && (active === first || !box.current.contains(active))) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && (active === last || !box.current.contains(active))) {
      e.preventDefault();
      first.focus();
    }
  }

  const running = state.kind === "running";
  return (
    <div className="cs-overlay" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div ref={box} className="cs-dialog" role="dialog" aria-modal="true" aria-labelledby="cs-dev-title" tabIndex={-1} onKeyDown={keyDown}>
        <h2 id="cs-dev-title">Test my device</h2>
        {running && <p className="cs-muted" role="status">Checking…</p>}
        {state.kind === "none" && (
          <p className="cs-muted" role="status">
            This version of FairPlay cannot run the full check. The exam still checks before it locks.
          </p>
        )}
        {state.kind === "done" && (
          <>
            <ul className="cs-checks" aria-label="Device check results">
              {rowsOf(state.report).map((r) => (
                <li key={r.id} className="cs-check">
                  <span className={`cs-pill cs-pill-${r.pill}`}>{PILL_TEXT[r.pill]}</span>
                  <span>
                    {r.text}
                    {r.detail && <span className="cs-check-detail">{r.detail}</span>}
                  </span>
                </li>
              ))}
            </ul>
            <p className="cs-muted" role="status">Your teacher sees this check after you sign in.</p>
          </>
        )}
        <div className="cs-dialog-actions">
          <button type="button" className="cs-btn cs-btn-secondary" onClick={onAgain} disabled={running}>
            Test again
          </button>
          <button type="button" className="cs-btn cs-btn-primary" onClick={onClose}>
            Close
          </button>
        </div>
      </div>
    </div>
  );
}
