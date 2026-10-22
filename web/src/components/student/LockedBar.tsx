import { useEffect, useRef, useState } from "react";
import type { SyncStatus } from "../../lib/answerQueue";
import { formatCountdown } from "../../lib/timer";
import { LogoMark } from "../ui/Icon";
import { ClockIcon, CloudCheckIcon, DiskIcon, LockIcon, UnlockIcon } from "./icons";

/**
 * Slim white top bar shown while taking an exam:
 * lock state · course + title · save status · timer (amber under 5 minutes).
 * "Test my device" sample questions: no timer, and a "Stop" button in the right corner.
 * In a plain browser the yellow "not locked" strip above this bar carries the lock state.
 */
export function LockedBar({
  title,
  course,
  locked,
  native,
  sync,
  remaining,
  trial,
}: {
  title: string;
  course?: string;
  locked: boolean;
  /** Running in the Mac app (when false, the yellow strip explains the lock state). */
  native?: boolean;
  sync: SyncStatus;
  remaining: number;
  /** 2026-10-22: "Test my device" sample questions: no timer, "Stop" in the corner. */
  trial?: { onStop: () => void; stopping?: boolean; disabled?: boolean };
}) {
  const lowTime = remaining < 5 * 60 * 1000;
  const synced = sync === "synced";
  const announce = useAnnouncements(sync, trial ? Number.POSITIVE_INFINITY : remaining);
  return (
    <header className="stu-lockbar">
      <LogoMark size={28} title="FairPlay" />
      {locked ? (
        <span className="stu-lock-badge">
          <LockIcon />
          Locked
        </span>
      ) : native ? (
        <span className="stu-lock-badge off">
          <UnlockIcon />
          Not locked
        </span>
      ) : null}
      <span className="stu-lockbar-title">
        {course && <small>{course}</small>}
        <span className="t">{title}</span>
      </span>
      {/* Audit W-14: the chip flips on every keystroke, so it is not a live region; the
          screen-reader line below speaks only lasting changes and the time cues. */}
      <span className="sr-only" role="status" aria-live="polite">
        {announce}
      </span>
      <span className={`stu-sync ${synced ? "synced" : "pending"}`}>
        {synced ? <CloudCheckIcon /> : <DiskIcon />}
        {synced ? "All answers saved" : "Saved on this computer"}
      </span>
      {!trial && (
        <span className={`stu-timer${lowTime ? " low" : ""}`} role="timer" aria-label="Time left">
          <ClockIcon />
          {Number.isFinite(remaining) ? formatCountdown(remaining) : "--:--"}
        </span>
      )}
      {trial && (
        <button
          type="button"
          className="stu-btn sm stu-trial-stop"
          onClick={trial.onStop}
          disabled={trial.disabled || trial.stopping}
          aria-describedby="trial-stop-hint"
        >
          {trial.stopping ? "Stopping…" : "Stop"}
        </button>
      )}
      {trial && (
        <span id="trial-stop-hint" className="sr-only">
          Ends the sample questions now and unlocks this computer. Nothing is graded.
        </span>
      )}
    </header>
  );
}


export const OFFLINE_ANNOUNCE_MS = 5000;

/** Audit W-14: what a screen reader hears -- "kept on this computer" only after answers have
 *  waited OFFLINE_ANNOUNCE_MS, "saved" when they get through after that, and one cue each at
 *  5 minutes and 1 minute left. */
export function useAnnouncements(sync: SyncStatus, remaining: number): string {
  const [text, setText] = useState("");
  const saidOffline = useRef(false);
  const cues = useRef(new Set<number>());
  useEffect(() => {
    if (sync === "synced") {
      if (saidOffline.current) {
        saidOffline.current = false;
        setText("All answers saved.");
      }
      return;
    }
    const t = setTimeout(() => {
      saidOffline.current = true;
      setText("Answers are being kept on this computer and will be sent when the connection is back.");
    }, OFFLINE_ANNOUNCE_MS);
    return () => clearTimeout(t);
  }, [sync]);
  useEffect(() => {
    if (!Number.isFinite(remaining)) return;
    for (const [ms, words] of [
      [5 * 60_000, "5 minutes left."],
      [60_000, "1 minute left."],
    ] as const) {
      if (remaining <= ms && remaining > ms - 30_000 && !cues.current.has(ms)) {
        cues.current.add(ms);
        setText(words);
      }
    }
  }, [remaining]);
  return text;
}
