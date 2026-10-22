// 2026-10-22: sample exam only. When Stop can't reach the server there is no invigilator to
// make the offline exit code, so this page makes it from the attempt's offline key (fetched
// while online, see lib/offlineExit.ts). A real exam never shows this.
import { useId, useState } from "react";
import * as bridge from "../../lib/bridge";
import { normalizeAppCode, offlineExitCode } from "../../lib/offlineExit";

export function TrialExit({
  keyHex,
  message,
  onRetry,
  retrying,
}: {
  keyHex: string | null;
  message: string | null;
  onRetry: () => void;
  retrying?: boolean;
}) {
  const [code, setCode] = useState("");
  const inputId = useId();
  const platform = bridge.nativePlatform();
  const keys =
    platform === "windows"
      ? "the invigilator shortcut"
      : platform === "ipad"
        ? "a 3-second press on the lock bar at the top"
        : "Ctrl-Opt-Cmd-E";
  const name = platform === "windows" ? "PC code" : platform === "ipad" ? "iPad code" : "Mac code";
  const exit = keyHex ? offlineExitCode(keyHex, code) : null;
  const typed = code.replace(/[^A-Za-z0-9]/g, "").length >= 8;

  return (
    <main className="stu-center" data-testid="trial-exit">
      <div className="stu-card" style={{ gap: 14, maxWidth: 600 }} role="alert">
        <h1 style={{ margin: 0, fontSize: 22 }}>The sample exam cannot reach the server</h1>
        <p className="stu-muted" style={{ margin: 0 }}>
          {message ?? "Check the Wi-Fi and try again."} Nothing from a sample exam is kept or graded.
        </p>
        <div className="stu-row">
          <button type="button" className="stu-btn primary" onClick={onRetry} disabled={retrying}>
            {retrying ? "Trying…" : "Try again"}
          </button>
        </div>
        {!bridge.isNative() ? null : keyHex ? (
          <>
            <h2 style={{ margin: 0, fontSize: 17 }}>Or unlock without the server</h2>
            <ol style={{ margin: 0, paddingLeft: 20, lineHeight: 1.7 }}>
              <li>
                Use {keys} and choose <b>Offline code</b>. FairPlay shows a {name} like K7QM-3XPA.
              </li>
              <li>
                <label htmlFor={inputId}>Type that {name} here:</label>{" "}
                <input
                  id={inputId}
                  className="stu-answer-input"
                  value={code}
                  onChange={(e) => setCode(e.target.value)}
                  autoComplete="off"
                  spellCheck={false}
                  maxLength={12}
                  style={{ width: 140, textTransform: "uppercase" }}
                  aria-invalid={typed && !normalizeAppCode(code) ? true : undefined}
                />
              </li>
              <li>
                Type this exit code into FairPlay:{" "}
                <output className="stu-trial-exit-code" data-testid="trial-exit-code" aria-live="polite">
                  {exit ? `${exit.slice(0, 4)} ${exit.slice(4)}` : "—"}
                </output>
              </li>
            </ol>
            {typed && !exit && (
              <p className="stu-field-error" style={{ margin: 0 }}>
                That is not a code FairPlay shows. Check the letters and digits.
              </p>
            )}
          </>
        ) : (
          <p style={{ margin: 0 }}>
            This computer never reached the server during this sample exam, so no exit code can be made here.
            Restart the computer to end it.
          </p>
        )}
      </div>
    </main>
  );
}
