// 2026-10-30: "Open exam with Duke Box". The app opens the Box folder in its own window; this
// page only asks for it and shows the state. The Box link (start_url) is a secret-ish value
// and is never rendered, logged or put in an error text here.
import { useCallback, useEffect, useRef, useState } from "react";
import type { ExamBox } from "../../api/types";
import * as bridge from "../../lib/bridge";
import { GlobeIcon } from "./icons";
import "../../styles/tools.css";

export const BOX_NEEDS_APP = "Duke Box needs the FairPlay app.";
export const BOX_OPENING = "Opening…";
export const BOX_OPEN = "Open in the Box window. Close it to return to your exam.";
export const BOX_ERROR = "Duke Box could not be opened.";

export type BoxStatus = "idle" | "opening" | "open" | "error";

export interface DukeBoxState {
  supported: boolean;
  status: BoxStatus;
  error: string | null;
  open: () => void;
}

function safeError(raw: unknown, box: ExamBox): string {
  if (typeof raw !== "string" || !raw.trim()) return BOX_ERROR;
  let t = raw.trim().slice(0, 200);
  if (box.start_url) t = t.split(box.start_url).join("");
  const token = box.path_prefix.split("/").filter(Boolean).pop();
  if (token && token.length >= 8) t = t.split(token).join("");
  t = t.replace(/https?:\/\/\S+/g, "").trim();
  return t || BOX_ERROR;
}

export function useDukeBox(box: ExamBox | undefined): DukeBoxState {
  const [, setTick] = useState(0);
  const [status, setStatus] = useState<BoxStatus>("idle");
  const [error, setError] = useState<string | null>(null);
  const boxRef = useRef(box);
  boxRef.current = box;
  const statusRef = useRef<BoxStatus>("idle");
  statusRef.current = status;
  const supported = !!box && bridge.boxSupported();
  const supportedRef = useRef(supported);
  supportedRef.current = supported;

  useEffect(
    () =>
      bridge.onNative((m) => {
        if (m.type === "state") setTick((n) => n + 1);
        if (m.type !== "boxResult" || !boxRef.current) return;
        if (m.state === "opened") {
          setStatus("open");
          setError(null);
        } else if (m.state === "closed") {
          setStatus("idle");
          setError(null);
        } else if (m.state === "error") {
          setStatus("error");
          setError(safeError(m.error, boxRef.current));
        }
      }),
    [],
  );

  const open = useCallback(() => {
    const b = boxRef.current;
    if (!b || !bridge.boxSupported()) return;
    if (statusRef.current === "opening" || statusRef.current === "open") return;
    setStatus("opening");
    setError(null);
    bridge.openBox(b);
  }, []);

  // Box gone from the attempt (handed in, time up), its link or policy changed (DKU OJ / the teacher
  // changed it while the pane was open) or the page leaves: close the window. A window that was open
  // is opened again with the new link; one that was not stays closed.
  const boxKey = box ? JSON.stringify(box) : "";
  const reopenRef = useRef(false);
  useEffect(() => {
    if (!box) return;
    if (reopenRef.current) {
      reopenRef.current = false;
      setStatus("idle");
      statusRef.current = "idle";
      setError(null);
      open();
    }
    return () => {
      reopenRef.current = statusRef.current === "open" || statusRef.current === "opening";
      bridge.closeBox();
      setStatus("idle");
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [boxKey]);

  return { supported, status, error, open };
}

export function DukeBoxPane({ box }: { box: DukeBoxState }) {
  if (!box.supported)
    return (
      <p className="stu-muted" role="status" data-testid="box-needs-app">
        {BOX_NEEDS_APP}
      </p>
    );
  const busy = box.status === "opening" || box.status === "open";
  return (
    <div className="stu-pane-stack">
      <button type="button" className="stu-btn" onClick={box.open} aria-disabled={busy} data-testid="box-open">
        <GlobeIcon size={16} />
        Open Duke Box
      </button>
      {box.status === "opening" && (
        <p className="stu-muted" role="status" data-testid="box-status">
          {BOX_OPENING}
        </p>
      )}
      {box.status === "open" && (
        <p className="stu-muted" role="status" data-testid="box-status">
          {BOX_OPEN}
        </p>
      )}
      {box.status === "error" && (
        <p className="stu-alert warn" role="status" data-testid="box-status">
          {box.error ?? BOX_ERROR}
        </p>
      )}
    </div>
  );
}
