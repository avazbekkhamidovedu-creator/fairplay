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
  const current = bridge.getNativeState()?.app_version;
  return (
    <main className="stu stu-center" role="alert" data-testid="update-required" style={{ minHeight: "100vh" }}>
      <div className="stu-card" style={{ gap: 14, maxWidth: 560 }}>
        <h1 style={{ margin: 0, fontSize: 24 }}>Update FairPlay</h1>
        <p style={{ margin: 0 }}>
          This version of FairPlay is too old for exams on this server.
          {minVersion ? ` You need version ${minVersion} or newer` : " You need a newer version"}
          {current ? ` (this computer has ${current}).` : "."}
        </p>
        <p className="stu-muted" style={{ margin: 0 }}>
          Quit FairPlay, install the new version from your school, then open it again. Ask the invigilator if you
          need help.
        </p>
      </div>
    </main>
  );
}
