import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { deviceTestLeave, deviceTestStart, onDeviceTest, setDeviceTestConfirmed } from "../../lib/bridge";

const FOCUSABLE = "button:not([disabled]), [href], input, select, textarea, [tabindex]:not([tabindex='-1'])";

export function TryExamScreenDialog({ onClose }: { onClose: () => void }) {
  const box = useRef<HTMLDivElement>(null);
  const navigate = useNavigate();
  const [trying, setTrying] = useState(true);
  const [refused, setRefused] = useState(false);
  const stop = useRef<(() => void) | null>(null);

  // The test starts as soon as the dialog opens: nothing to confirm, no apps to close.
  useEffect(() => {
    // Deferred so a replayed effect (React Strict Mode) cancels the first start instead of sending two.
    const first = window.setTimeout(start, 0);
    return () => { window.clearTimeout(first); if (stop.current) { stop.current(); deviceTestLeave(); } };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function start() {
    setRefused(false);
    setTrying(true);
    let timer = 0;
    const finish = (remaining?: number) => {
      stop.current?.();
      stop.current = null;
      setDeviceTestConfirmed(true);
      navigate("/student/device-test", { state: { remaining } });
    };
    const off = onDeviceTest((m) => {
      if (m.state === "started") finish(m.remaining);
      else if (m.reason === "refused" || m.reason === "error") {
        stop.current?.();
        stop.current = null;
        setTrying(false);
        setRefused(true);
      }
    });
    stop.current = () => {
      off();
      window.clearTimeout(timer);
    };
    timer = window.setTimeout(() => {
      stop.current?.();
      stop.current = null;
      deviceTestLeave();
      let expire = 0;
      const late = onDeviceTest((m) => {
        if (m.state !== "started") return;
        deviceTestLeave();
        late();
        window.clearTimeout(expire);
      });
      expire = window.setTimeout(late, 65000);
      setTrying(false);
      setRefused(true);
    }, 1500);
    deviceTestStart(60);
  }

  useEffect(() => {
    const before = document.activeElement as HTMLElement | null;
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

  return (
    <div className="cs-overlay" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div ref={box} className="cs-dialog" role="dialog" aria-modal="true" aria-labelledby="cs-try-title" tabIndex={-1} onKeyDown={keyDown}>
        <h2 id="cs-try-title">Try the exam screen</h2>
        {trying && (
          <p className="cs-muted" role="status">
            Starting a sample exam screen for 1 minute. Nothing is saved or graded.
          </p>
        )}
        {refused && (
          <p className="cs-muted" role="status">
            The test can't start right now.
          </p>
        )}
        <div className="cs-dialog-actions">
          <button type="button" className="cs-btn cs-btn-secondary" onClick={onClose}>
            {refused ? "Close" : "Cancel"}
          </button>
          {refused && (
            <button type="button" className="cs-btn cs-btn-primary" onClick={start} autoFocus>
              Try again
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
