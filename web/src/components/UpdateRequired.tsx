// S2 (2026-10-20): the server refused this FairPlay app as too old (426). Nothing else on
// the page can work until the student updates, so this replaces the whole page.
import { useEffect, useState } from "react";
import { UPGRADE_EVENT } from "../api/client";
import * as bridge from "../lib/bridge";
import "../styles/student.css";

/** null until a 426 arrives; then the minimum version ("" when the server sent none). */
export function useUpgradeRequired(): string | null {
  const [min, setMin] = useState<string | null>(null);
  useEffect(() => {
    const on = (e: Event) => {
      const v = (e as CustomEvent<{ min_app_version?: unknown }>).detail?.min_app_version;
      setMin(typeof v === "string" ? v : "");
    };
    window.addEventListener(UPGRADE_EVENT, on);
    return () => window.removeEventListener(UPGRADE_EVENT, on);
  }, []);
  return min;
}

export function UpdateRequired({ minVersion }: { minVersion: string }) {
  const [state, setState] = useState(() => bridge.getNativeState());
  const [run, setRun] = useState<"idle" | "installing" | "failed">("idle");
  const [reason, setReason] = useState<bridge.UpdateFailure | null>(null);
  useEffect(() => {
    bridge.checkUpdate();       // a 426 means we are too old: look for the update now
    return bridge.onNative((msg) => {
      if (msg.type === "state") setState(bridge.getNativeState());
      else if (msg.type === "updateProgress") setRun("installing");
      else if (msg.type === "updateFailed") {
        const r = bridge.parseUpdateFailure(msg.reason);
        setReason(r);
        setRun(r === "busy" ? "installing" : "failed");
      }
    });
  }, []);
  const current = state?.app_version;
  const update = state && !bridge.isLocked() ? state.update : undefined;
  return (
    <main className="stu stu-center" role="alert" data-testid="update-required" style={{ minHeight: "100vh" }}>
      <div className="stu-card" style={{ gap: 14, maxWidth: 560 }}>
        <h1 style={{ margin: 0, fontSize: 24 }}>Update FairPlay</h1>
        <p style={{ margin: 0 }}>
          This version of FairPlay is too old for exams on this server.
          {minVersion ? ` You need version ${minVersion} or newer` : " You need a newer version"}
          {current ? ` (this computer has ${current}).` : "."}
        </p>
        {update && run !== "installing" && (
          <button type="button" className="stu-btn primary" data-testid="update-now" onClick={() => bridge.installUpdate() && setRun("installing")}>
            {run === "failed" ? "Try again" : `Update now to ${update.version}`}
          </button>
        )}
        {run === "installing" && <p style={{ margin: 0 }} role="status">Updating... FairPlay opens again by itself.</p>}
        {run === "failed" && (
          <p style={{ margin: 0 }} data-testid="update-failed">
            {reason === "locked" ? "Cannot update during an exam." : "The update did not work. Download it yourself below."}
          </p>
        )}
        <p className="stu-muted" style={{ margin: 0 }}>
          Or download the new version yourself, install it, then open FairPlay again:{" "}
          <a
            href={bridge.DOWNLOAD_PAGE}
            target="_blank"
            rel="noreferrer"
            onClick={(e) => {
              if (bridge.openDownloadPage()) e.preventDefault();
            }}
          >
            {bridge.DOWNLOAD_PAGE}
          </a>
          . Ask the invigilator if you need help.
        </p>
      </div>
    </main>
  );
}
