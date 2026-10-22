// Audit 2026-10-12 M-11: what must be fixed before the Mac locks. Shown instead of locking,
// because some of these (Remote Login, Screen Sharing) can't be fixed from inside the lock.
import { nativePlatform } from "../../lib/bridge";

export function PreflightProblems({ problems, onRetry }: { problems: string[]; onRetry: () => void }) {
  return (
    <main className="stu-center">
      <div className="stu-card" style={{ gap: 14, maxWidth: 600 }} role="alert">
        <h1 style={{ margin: 0, fontSize: 22 }}>
          Fix this before the exam locks your{" "}
          {nativePlatform() === "ipad" ? "iPad" : nativePlatform() === "windows" ? "PC" : "Mac"}
        </h1>
        <ul style={{ margin: 0, paddingLeft: 20, display: "flex", flexDirection: "column", gap: 6 }}>
          {problems.map((p) => (
            <li key={p}>{p}</li>
          ))}
        </ul>
        <p className="stu-muted" style={{ margin: 0 }}>
          Your exam time is running. Ask the invigilator if you need help.
        </p>
        <div className="stu-row">
          <button type="button" className="stu-btn primary" onClick={onRetry}>
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
