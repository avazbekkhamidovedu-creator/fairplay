import { useCallback, useEffect, useRef, useState } from "react";
import * as bridge from "../../lib/bridge";

export const COUNTDOWN_S = 5;
export const INSTALL_TIMEOUT_MS = 60_000;

export type FailReason = bridge.UpdateFailure | "timeout";
export type UpdatePhase =
  | { kind: "countdown"; left: number }
  | { kind: "offer" }
  | { kind: "installing" }
  | { kind: "failed"; reason: FailReason };

const autoTried = new Set<string>();
let dismissed = false;

export function _resetUpdatePromptForTests() {
  autoTried.clear();
  dismissed = false;
}

export interface SignInUpdate {
  version: string;
  phase: UpdatePhase;
  hidden: boolean;
  start: () => void;
  dismiss: () => void;
}

/** The app's offered update, as the sign-in screen needs it: a short countdown, one automatic
 *  install per version per app run, and a plain failure state. Null when nothing is offered. */
export function useSignInUpdate(): SignInUpdate | null {
  const [state, setState] = useState(() => bridge.getNativeState());
  const [, bump] = useState(0);
  const version = state && !bridge.isLocked() ? (state.update?.version ?? null) : null;
  const [phase, setPhase] = useState<UpdatePhase>({ kind: "countdown", left: COUNTDOWN_S });
  const phaseRef = useRef(phase);
  phaseRef.current = phase;

  useEffect(
    () =>
      bridge.onNative((msg) => {
        if (msg.type === "state") setState(bridge.getNativeState());
        else if (msg.type === "updateProgress") setPhase({ kind: "installing" });
        else if (msg.type === "updateFailed") {
          const reason = bridge.parseUpdateFailure(msg.reason);
          setPhase(reason === "busy" ? { kind: "installing" } : { kind: "failed", reason });
        }
      }),
    [],
  );

  useEffect(() => {
    if (version) setPhase(autoTried.has(version) ? { kind: "offer" } : { kind: "countdown", left: COUNTDOWN_S });
  }, [version]);

  const start = useCallback(() => {
    if (!version) return;
    autoTried.add(version);
    if (bridge.installUpdate()) setPhase({ kind: "installing" });
  }, [version]);

  const counting = phase.kind === "countdown" && !dismissed;
  const left = phase.kind === "countdown" ? phase.left : 0;
  useEffect(() => {
    if (!version || !counting) return;
    if (left <= 0) {
      if (!autoTried.has(version)) start();
      else setPhase({ kind: "offer" });
      return;
    }
    const t = setTimeout(() => setPhase({ kind: "countdown", left: left - 1 }), 1000);
    return () => clearTimeout(t);
  }, [version, counting, left, start]);

  useEffect(() => {
    if (phase.kind !== "installing") return;
    const t = setTimeout(() => {
      if (phaseRef.current.kind === "installing") setPhase({ kind: "failed", reason: "timeout" });
    }, INSTALL_TIMEOUT_MS);
    return () => clearTimeout(t);
  }, [phase.kind]);

  const dismiss = useCallback(() => {
    dismissed = true;
    bump((n) => n + 1);
  }, []);

  if (!version) return null;
  return { version, phase, hidden: dismissed, start, dismiss };
}

const REASON_TEXT: Record<FailReason, string> = {
  locked: "FairPlay is locked for an exam right now.",
  not_writable: "This copy was not installed with the installer, so it cannot update itself.",
  no_update: "No newer version was found.",
  download: "The download did not finish.",
  verify: "The download could not be checked.",
  busy: "Another update is already running.",
  timeout: "The app did not answer.",
  unknown: "Something went wrong.",
};

export function UpdateCard({ update, countdown }: { update: SignInUpdate; countdown: boolean }) {
  const { phase, version } = update;
  if (phase.kind === "installing") {
    return (
      <div className="cs-update" role="status" data-testid="update-notice">
        <p className="cs-update-line">Updating FairPlay… the app will reopen</p>
      </div>
    );
  }
  if (phase.kind === "failed") {
    return (
      <div className="cs-update cs-update-warn" data-testid="update-notice">
        <p className="cs-update-line" role="alert">
          The update could not be installed. You can keep using this version. {REASON_TEXT[phase.reason]}
        </p>
        <div className="cs-update-actions">
          <button type="button" className="cs-btn cs-btn-primary cs-btn-sm" onClick={update.start}>
            Update now
          </button>
        </div>
      </div>
    );
  }
  const counting = countdown && phase.kind === "countdown";
  return (
    <div className="cs-update" role="status" data-testid="update-notice">
      <p className="cs-update-line">
        {counting ? `FairPlay ${version} is available. Updating in ${phase.left} s…` : `FairPlay ${version} is available.`}
      </p>
      <div className="cs-update-actions">
        <button type="button" className="cs-btn cs-btn-primary cs-btn-sm" onClick={update.start}>
          Update now
        </button>
        {countdown && (
          <button type="button" className="cs-link" onClick={update.dismiss}>
            Not now
          </button>
        )}
      </div>
    </div>
  );
}
