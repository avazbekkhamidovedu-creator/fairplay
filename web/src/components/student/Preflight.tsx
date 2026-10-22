// Audit 2026-10-12 M-11: what must be fixed before the Mac locks. Shown instead of locking,
// because some of these (Remote Login, Screen Sharing) can't be fixed from inside the lock.
import { useEffect, useRef } from "react";
import { nativePlatform, openSettings, settingsPaneFor } from "../../lib/bridge";

/** While problems are shown, check again this often, so a fixed setting moves on by itself. */
export const PREFLIGHT_RECHECK_MS = 3000;

const PANE_LABEL: Record<string, string> = {
  sharing: "Open Sharing settings",
  datetime: "Open Date & Time settings",
  displays: "Open Displays settings",
  keyboard: "Open Keyboard settings",
  accessibility: "Open Accessibility settings",
};

export function PreflightProblems({
  problems,
  onRetry,
  trial = false,
}: {
  problems: string[];
  onRetry: () => void | Promise<unknown>;
  /** "Test my device": no exam time and no invigilator; say what this check means. */
  trial?: boolean;
}) {
  const mac = nativePlatform() === "mac";
  // Check again by itself: the screen moves on as soon as the setting is off.
  const retry = useRef(onRetry);
  retry.current = onRetry;
  useEffect(() => {
    let busy = false;
    const t = setInterval(() => {
      if (busy) return; // the previous check is still running
      busy = true;
      void Promise.resolve(retry.current()).finally(() => {
        busy = false;
      });
    }, PREFLIGHT_RECHECK_MS);
    return () => clearInterval(t);
  }, []);
  return (
    <main className="stu-center">
      <div className="stu-card" style={{ gap: 14, maxWidth: 600 }} role="alert">
        <h1 style={{ margin: 0, fontSize: 22 }}>
          Fix this before the exam locks your{" "}
          {nativePlatform() === "ipad" ? "iPad" : nativePlatform() === "windows" ? "PC" : "Mac"}
        </h1>
        <ul style={{ margin: 0, paddingLeft: 20, display: "flex", flexDirection: "column", gap: 10 }}>
          {problems.map((p) => {
            const pane = mac ? settingsPaneFor(p) : null;
            return (
              <li key={p}>
                <span>{p}</span>
                {pane && (
                  <div style={{ marginTop: 6 }}>
                    <button type="button" className="stu-btn sm" onClick={() => openSettings(pane)}>
                      {PANE_LABEL[pane]}
                    </button>
                  </div>
                )}
              </li>
            );
          })}
        </ul>
        <p className="stu-muted" style={{ margin: 0 }}>
          {trial
            ? "FairPlay checks this before every exam. Fix it now so it is ready on exam day; this screen goes on by itself. Or press Stop to go back."
            : "Your exam time is running. Ask the invigilator if you need help."}
        </p>
        <div className="stu-row">
          <button type="button" className="stu-btn primary" onClick={() => void onRetry()}>
            Check again
          </button>
        </div>
      </div>
    </main>
  );
}

/**
 * 2026-10-22: before the lock, the apps that will be closed. The student saves their work
 * in them, then one button closes them and starts the exam (the lock would close them
 * anyway, without the chance to save).
 */
export function AppsToClose({ apps, busy, onContinue }: { apps: string[]; busy: boolean; onContinue: () => void }) {
  const device = nativePlatform() === "windows" ? "PC" : "Mac";
  return (
    <main className="stu-center">
      <div className="stu-card" style={{ gap: 14, maxWidth: 600 }} role="alert">
        <h1 style={{ margin: 0, fontSize: 22 }}>These apps will close when the exam starts</h1>
        <p style={{ margin: 0 }}>
          Save your work in them now. Only the apps your teacher allowed can stay open while this {device} is locked.
        </p>
        <ul style={{ margin: 0, paddingLeft: 20, display: "flex", flexDirection: "column", gap: 4 }}>
          {apps.map((a) => (
            <li key={a}>{a}</li>
          ))}
        </ul>
        <p className="stu-muted" style={{ margin: 0 }}>
          If an app asks whether to save, answer it within a few seconds; after that it is closed anyway.
        </p>
        <div className="stu-row">
          <button type="button" className="stu-btn primary" onClick={onContinue} disabled={busy}>
            {busy ? "Closing apps..." : "Close them and start"}
          </button>
        </div>
      </div>
    </main>
  );
}
