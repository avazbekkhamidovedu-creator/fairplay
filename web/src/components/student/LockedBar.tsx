import { useCallback, useEffect, useRef, useState, type Ref } from "react";
import type { SyncStatus } from "../../lib/answerQueue";
import { batteryTone, useBattery } from "../../lib/battery";
import { TIMER_FORCE_SHOW_MS, type TextSize } from "../../lib/examPrefs";
import { formatCountdown } from "../../lib/timer";
import { LogoMark } from "../ui/Icon";
import {
  BatteryIcon,
  BookIcon,
  CalculatorIcon,
  ChevronIcon,
  GlobeIcon,
  CloudCheckIcon,
  DiskIcon,
  HelpIcon,
  LockIcon,
  MoreVerticalIcon,
  TextSizeIcon,
  UnlockIcon,
} from "./icons";

/**
 * Top bar shown while taking an exam (2026-10-23 layout):
 *   left   logo · lock state · course + title, "Directions" under it
 *   middle the timer, which the student may hide (it comes back by itself in the last 5 minutes)
 *   right  save status · battery (when known) · Calculator (if allowed) · More (Materials and
 *          Websites when the exam has them, text size, help)
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
  timerHidden = false,
  onTimerHidden,
  directions,
  textSize = 1,
  onTextSize,
  calculator,
  materials,
  websites,
  moreButtonRef,
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
  /** The student hid the timer (ignored in the last 5 minutes). */
  timerHidden?: boolean;
  onTimerHidden?: (hidden: boolean) => void;
  /** Lines shown under "Directions"; omitted -> no Directions button. */
  directions?: string[];
  textSize?: TextSize;
  onTextSize?: () => void;
  /** 2026-10-23 (exam tools): only when the teacher allowed it. */
  calculator?: { open: boolean; onToggle: () => void; buttonRef?: Ref<HTMLButtonElement> };
  /** open-book exams: count = the student's files (null = not known yet) */
  materials?: { count: number | null; onOpen: () => void };
  websites?: { count: number; onOpen: () => void };
  /** 2026-10-23: the More button, so focus can come back to it when a side panel closes. */
  moreButtonRef?: Ref<HTMLButtonElement>;
}) {
  const lowTime = remaining < TIMER_FORCE_SHOW_MS;
  const showTime = !timerHidden || lowTime;
  const synced = sync === "synced";
  const announce = useAnnouncements(sync, trial ? Number.POSITIVE_INFINITY : remaining);
  const battery = useBattery();
  const [open, setOpen] = useState<null | "directions" | "more" | "help" | "battery">(null);
  const barRef = useRef<HTMLElement>(null);
  const close = useCallback(() => setOpen(null), []);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && close();
    const onDown = (e: PointerEvent) => {
      if (barRef.current && !barRef.current.contains(e.target as Node)) close();
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("pointerdown", onDown);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("pointerdown", onDown);
    };
  }, [open, close]);

  const tone = battery ? batteryTone(battery) : "ok";
  const pct = battery ? Math.round(battery.level * 100) : 0;
  // 2026-10-23: one gentle warning when the battery drops under 10 % (not charging).
  const warnedRef = useRef(false);
  useEffect(() => {
    if (tone === "critical" && !warnedRef.current) {
      warnedRef.current = true;
      setOpen("battery");
    }
    if (tone !== "critical") warnedRef.current = false;
  }, [tone]);

  return (
    <header className="stu-lockbar" ref={barRef}>
      <div className="stu-lb-left">
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
        <div className="stu-lockbar-title">
          <span className="t">
            {course && <small>{course} · </small>}
            {title}
          </span>
          {directions && directions.length > 0 && (
            <button
              type="button"
              className="stu-directions-btn"
              aria-expanded={open === "directions"}
              aria-controls="exam-directions"
              onClick={() => setOpen((o) => (o === "directions" ? null : "directions"))}
            >
              Directions
              <ChevronIcon size={14} className="chev" />
            </button>
          )}
        </div>
      </div>

      <div className="stu-lb-center">
        {!trial && (
          <>
            <span
              className={`stu-timer${lowTime ? " low" : ""}${showTime ? "" : " hidden"}`}
              role="timer"
              aria-label={showTime ? "Time left" : "Timer hidden"}
            >
              {showTime ? (Number.isFinite(remaining) ? formatCountdown(remaining) : "--:--") : ""}
            </span>
            {onTimerHidden && !lowTime && (
              <button
                type="button"
                className="stu-timer-toggle"
                aria-pressed={timerHidden}
                onClick={() => onTimerHidden(!timerHidden)}
              >
                {timerHidden ? "Show" : "Hide"}
              </button>
            )}
          </>
        )}
      </div>

      <div className="stu-lb-right">
        {/* Audit W-14: the chip flips on every keystroke, so it is not a live region; the
            screen-reader line below speaks only lasting changes and the time cues. */}
        <span className="sr-only" role="status" aria-live="polite">
          {announce}
        </span>
        <span className={`stu-sync ${synced ? "synced" : "pending"}`}>
          {synced ? <CloudCheckIcon /> : <DiskIcon />}
          <span className="stu-sync-text">{synced ? "All answers saved" : "Saved on this computer"}</span>
        </span>
        {battery && (
          <span
            className={`stu-battery ${tone}`}
            title={battery.charging ? "Charging" : "Battery"}
            aria-label={`Battery ${pct}%${battery.charging ? ", charging" : ""}${tone !== "ok" ? ", plug in your charger" : ""}`}
          >
            <BatteryIcon level={battery.level} charging={battery.charging} />
            <span aria-hidden="true">
              {pct}%{tone !== "ok" ? " · Plug in" : ""}
            </span>
          </span>
        )}
        {calculator && !trial && (
          <button
            ref={calculator.buttonRef}
            type="button"
            className={`stu-tool${calculator.open ? " on" : ""}`}
            aria-pressed={calculator.open}
            onClick={calculator.onToggle}
          >
            <CalculatorIcon />
            <span>Calculator</span>
          </button>
        )}
        {!trial && (
          <button
            ref={moreButtonRef}
            type="button"
            className={`stu-tool${open === "more" || open === "help" ? " on" : ""}`}
            aria-haspopup="menu"
            aria-expanded={open === "more"}
            aria-controls="exam-more-menu"
            onClick={() => setOpen((o) => (o === "more" || o === "help" ? null : "more"))}
          >
            <MoreVerticalIcon />
            <span>More</span>
          </button>
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
      </div>

      {open === "directions" && directions && (
        <div id="exam-directions" className="stu-pop stu-pop-left" role="region" aria-label="Directions">
          <ul>
            {directions.map((d) => (
              <li key={d}>{d}</li>
            ))}
          </ul>
        </div>
      )}
      {open === "more" && (
        <div id="exam-more-menu" className="stu-pop stu-pop-right stu-menu" role="menu" aria-label="More">
          {materials && (
            <button
              type="button"
              role="menuitem"
              className="stu-menu-item"
              onClick={() => {
                close();
                materials.onOpen();
              }}
            >
              <BookIcon />
              Materials
              {materials.count !== null && (
                <span className="stu-menu-note">
                  {materials.count} {materials.count === 1 ? "file" : "files"}
                </span>
              )}
            </button>
          )}
          {websites && (
            <button
              type="button"
              role="menuitem"
              className="stu-menu-item"
              onClick={() => {
                close();
                websites.onOpen();
              }}
            >
              <GlobeIcon />
              Websites
              <span className="stu-menu-note">
                {websites.count} {websites.count === 1 ? "site" : "sites"}
              </span>
            </button>
          )}
          {onTextSize && (
            <button type="button" role="menuitem" className="stu-menu-item" onClick={onTextSize}>
              <TextSizeIcon />
              Text size
              <span className="stu-menu-note">{Math.round(textSize * 100)}%</span>
            </button>
          )}
          <button type="button" role="menuitem" className="stu-menu-item" onClick={() => setOpen("help")}>
            <HelpIcon />
            Help
          </button>
        </div>
      )}
      {open === "battery" && (
        <div className="stu-pop stu-pop-right stu-pop-warn" role="alertdialog" aria-label="Battery low">
          <p className="stu-pop-title">Battery {pct}%</p>
          <p>Plug in your charger now, or raise your hand for one.</p>
          <p className="stu-muted">Your answers are saved.</p>
          <button type="button" className="stu-btn sm" onClick={close}>
            OK
          </button>
        </div>
      )}
      {open === "help" && (
        <div className="stu-pop stu-pop-right" role="dialog" aria-label="Help">
          <p className="stu-pop-title">Need help?</p>
          <p>Raise your hand. The invigilator will come to your desk.</p>
          <p className="stu-muted">Your answers keep saving and the timer keeps running.</p>
        </div>
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
