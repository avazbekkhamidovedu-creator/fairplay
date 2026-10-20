// S5 (2026-10-20): the time is up but the hand-in can't reach the server. Instead of an
// endless "Trying again…" the student (or the invigilator) picks what happens next.
import { useState } from "react";
import * as bridge from "../../lib/bridge";

export type SealState = "idle" | "sealing" | "sealed" | "failed";

interface Props {
  /** The submit error shown above the buttons. */
  message: string | null;
  onRetry: () => void;
  /** Ask the app to seal; resolves true when the sealed file is written. */
  onSeal: () => Promise<boolean>;
}

export function TimeUpOffline({ message, onRetry, onSeal }: Props) {
  const [sealState, setSealState] = useState<SealState>("idle");
  const [help, setHelp] = useState(false);
  const platform = bridge.nativePlatform();

  async function seal() {
    setSealState("sealing");
    setSealState((await onSeal()) ? "sealed" : "failed");
  }

  if (sealState === "sealed")
    return (
      <main className="stu-center" data-testid="time-up-sealed">
        <div className="stu-card" style={{ gap: 14, maxWidth: 600 }} role="status">
          <h1 style={{ margin: 0, fontSize: 22 }}>Your answers are sealed on this computer</h1>
          <p style={{ margin: 0 }}>Ask the invigilator to end the exam.</p>
          <OfflineExitSteps platform={platform} />
          <p className="stu-muted" style={{ margin: 0 }}>
            FairPlay sends the sealed answers to your teacher by itself as soon as this computer is online again.
          </p>
        </div>
      </main>
    );

  return (
    <main className="stu-center" data-testid="time-up-offline">
      <div className="stu-card" style={{ gap: 14, maxWidth: 600 }} role="alert">
        <h1 style={{ margin: 0, fontSize: 22 }}>Time is up, but your answers could not be sent</h1>
        <p className="stu-muted" style={{ margin: 0 }}>
          {message ?? "Cannot reach the server. Your answers are saved on this computer."}
        </p>
        <div className="stu-row" style={{ flexWrap: "wrap", gap: 8 }}>
          <button type="button" className="stu-btn primary" onClick={onRetry}>
            Try again now
          </button>
          {bridge.isNative() && (
            <button type="button" className="stu-btn" onClick={() => void seal()} disabled={sealState === "sealing"}>
              {sealState === "sealing" ? "Sealing…" : "Submit later from this computer"}
            </button>
          )}
          <button type="button" className="stu-btn" onClick={() => setHelp(true)} aria-expanded={help}>
            Ask the teacher for help
          </button>
        </div>
        {sealState === "failed" && (
          <p className="stu-field-error" style={{ margin: 0 }}>
            This computer could not seal your answers. Try again, or ask the teacher for help.
          </p>
        )}
        {help && (
          <div className="stu-alert warn" style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            <strong>Raise your hand.</strong>
            <OfflineExitSteps platform={platform} />
          </div>
        )}
      </div>
    </main>
  );
}

/** The existing offline exit: the app shows this computer's code, the teacher's live room
 *  turns it into the exit code (CONTRACT.md "Offline exit"). The page never sees the code. */
function OfflineExitSteps({ platform }: { platform: "mac" | "windows" | null }) {
  const keys = platform === "windows" ? "the invigilator shortcut" : "Ctrl-Opt-Cmd-E";
  const code = platform === "windows" ? "PC code" : "Mac code";
  return (
    <ol style={{ margin: 0, paddingLeft: 20, lineHeight: 1.7 }} data-testid="offline-exit-steps">
      <li>
        The invigilator presses {keys} and chooses <b>Offline code</b>. This computer then shows its {code} (like
        K7QM-3XPA).
      </li>
      <li>In the live room they pick you, type that code and get an 8-digit exit code.</li>
      <li>They type the exit code here. The computer unlocks and keeps your answers.</li>
    </ol>
  );
}
