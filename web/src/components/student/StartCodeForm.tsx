import { useEffect, useRef, useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { ApiError, NetworkError, SwapNeededError, studentApi } from "../../api/client";
import { CODE_LENGTH, StartCodeBoxes, emptyCode, type StartCodeBoxesHandle } from "./StartCodeBoxes";
import { PackageLockedError, WrongCodeError, startOffline, storedPackage } from "../../lib/offline";

/** S4: how often a computer waiting for the teacher's "Approve swap" tries to start. */
export const SWAP_RETRY_MS = 5000;

/** S4: ask the teacher to move this student's attempt to this computer. False when this
 *  computer can't take over (a browser has no app device id) or the attempt is unknown. */
export async function requestSwap(examId: string, err: SwapNeededError): Promise<boolean> {
  if (!err.deviceId) return false;
  const exams = await studentApi.listExams();
  const aid = exams.find((e) => e.id === examId)?.attempt?.attempt_id;
  if (!aid) return false;
  await studentApi.swapRequest(aid, err.deviceId);
  return true;
}

/** Turns a start error into a clear sentence for the student. */
export function startErrorMessage(err: unknown): string {
  if (err instanceof PackageLockedError)
    return "No connection, and this exam's offline copy is still locked on this computer. It unlocks when you are online once after your teacher opens the exam. Ask your teacher.";
  if (err instanceof NetworkError) return "Cannot reach the server. Check the connection and try again.";
  if (err instanceof ApiError) {
    // audit 2026-10-12: app-only start and one computer per attempt
    if (err.status === 403 && /FairPlay app/i.test(err.detail)) return err.detail;
    // audit S-03: not in the exam's class
    if (err.status === 403 && /is for .* students|not for your class/i.test(err.detail)) return err.detail;
    if (err.status === 409 && /another computer|somewhere else/i.test(err.detail)) return err.detail;
    if (err.status === 403) return "That code isn't right. Check it with your teacher and try again.";
    if (err.status === 429) return "Too many wrong codes. Wait a moment, then try again.";
    if (err.status === 409 && /submitted/i.test(err.detail)) return "You have already submitted this exam.";
    if (err.status === 409) return "This exam is not open right now. Wait for your teacher to open it.";
    if (err.status === 404) return "This exam was not found.";
    return err.detail;
  }
  return "Something went wrong. Try again.";
}

/**
 * Start-code boxes (CODE_LENGTH digits) + "Start exam". On success navigates to the exam
 * (the attempt goes along in router state so the exam opens without a second request).
 */
export function StartCodeForm({
  examId,
  autoFocus,
  onConflict,
  onCancel,
  legend,
}: {
  examId: string;
  autoFocus?: boolean;
  /** 409 (not open / already submitted): let the parent reload its data. */
  onConflict?: () => void;
  onCancel?: () => void;
  legend?: string;
}) {
  const navigate = useNavigate();
  const [digits, setDigits] = useState<string[]>(emptyCode);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // 2026-10-23: after too many wrong codes the boxes switch off and a countdown runs.
  const [waitUntil, setWaitUntil] = useState<number | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const [shake, setShake] = useState(0);
  const [readyAgain, setReadyAgain] = useState(false);
  // S4: asked the teacher to move the exam here; start is retried every SWAP_RETRY_MS.
  const [waitingSwap, setWaitingSwap] = useState(false);
  const swapTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const mounted = useRef(true);
  useEffect(
    () => () => {
      mounted.current = false;
      if (swapTimer.current) clearTimeout(swapTimer.current);
    },
    [],
  );
  const boxes = useRef<StartCodeBoxesHandle>(null);
  const startBtn = useRef<HTMLButtonElement>(null);
  const code = digits.join("");
  const complete = code.length === CODE_LENGTH && /^\d+$/.test(code);
  const errId = `start-${examId}-err`;

  useEffect(() => {
    if (autoFocus) boxes.current?.focus(0);
  }, [autoFocus]);

  const waitLeft = waitUntil === null ? 0 : Math.max(0, Math.ceil((waitUntil - now) / 1000));
  const waiting = waitUntil !== null && waitLeft > 0;
  useEffect(() => {
    if (waitUntil === null) return;
    const t = setInterval(() => setNow(Date.now()), 250);
    return () => clearInterval(t);
  }, [waitUntil]);
  useEffect(() => {
    if (waitUntil !== null && waitLeft === 0) {
      setWaitUntil(null);
      setError(null);
      setReadyAgain(true);
      setTimeout(() => boxes.current?.focus(0), 0);
    }
  }, [waitUntil, waitLeft]);

  function retryAfterSwap(startCode: string) {
    swapTimer.current = setTimeout(async () => {
      if (!mounted.current) return;
      try {
        const attempt = await studentApi.start(examId, startCode);
        if (!mounted.current) return;
        navigate(`/student/exam/${encodeURIComponent(attempt.attempt_id)}`, { state: { attempt } });
      } catch (err) {
        if (!mounted.current) return;
        if (err instanceof SwapNeededError || err instanceof NetworkError) {
          retryAfterSwap(startCode);      // not approved yet (or a Wi-Fi blip): keep waiting
          return;
        }
        setWaitingSwap(false);
        setBusy(false);
        setError(startErrorMessage(err));
      }
    }, SWAP_RETRY_MS);
  }

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (waiting) return;
    setReadyAgain(false);
    if (!complete) {
      setError(`The start code has ${CODE_LENGTH} digits.`);
      boxes.current?.focus(digits.indexOf("") === -1 ? 0 : digits.indexOf(""));
      return;
    }
    setBusy(true);
    setError(null);
    try {
      let attempt;
      try {
        attempt = await studentApi.start(examId, code);
      } catch (err) {
        // Slice 6: no network at start -> open the package downloaded at check-in.
        if (!(err instanceof NetworkError) || !storedPackage(examId)) throw err;
        try {
          attempt = await startOffline(examId, code);
        } catch (offErr) {
          if (offErr instanceof PackageLockedError) throw offErr;
          throw offErr instanceof WrongCodeError ? new ApiError(403, "Wrong start code") : err;
        }
      }
      navigate(`/student/exam/${encodeURIComponent(attempt.attempt_id)}`, { state: { attempt } });
    } catch (err) {
      if (err instanceof SwapNeededError && (await requestSwap(examId, err).catch(() => false))) {
        setWaitingSwap(true);
        retryAfterSwap(code);
        return;
      }
      setBusy(false);
      setError(startErrorMessage(err));
      if (err instanceof ApiError && err.status === 409) {
        onConflict?.();
        return;
      }
      setDigits(emptyCode());
      if (err instanceof ApiError && err.status === 429) {
        setNow(Date.now());
        setWaitUntil(Date.now() + (err.retryAfter ?? 60) * 1000);
        return;
      }
      if (err instanceof ApiError && err.status === 403) setShake((n) => n + 1);
      setTimeout(() => boxes.current?.focus(0), 0);
    }
  }

  return (
    <form onSubmit={submit} className="stu-code-panel" noValidate>
      <div key={shake} className={shake ? "stu-shake" : undefined}>
      <StartCodeBoxes
        ref={boxes}
        idPrefix={`code-${examId}`}
        value={digits}
        onChange={(d) => {
          setDigits(d);
          if (error) setError(null);
        }}
        onComplete={() => startBtn.current?.focus()}
        invalid={!!error && !waiting}
        describedBy={error ? errId : undefined}
        disabled={busy || waiting}
        legend={legend}
      />
      </div>
      {error && !waiting && (
        <div id={errId} className="stu-alert error" role="alert">
          {error}
        </div>
      )}
      {waiting && (
        <div id={errId} className="stu-alert warn" role="status" data-testid="code-wait">
          Too many wrong codes. Try again in <b className="stu-tabnum">{waitLeft} s</b>.
        </div>
      )}
      {readyAgain && !error && (
        <div className="stu-alert success" role="status">
          You can try again.
        </div>
      )}
      {waitingSwap && (
        <div className="stu-alert warn" role="status" data-testid="swap-waiting">
          This exam is open on another computer. Your teacher was asked to move it here. Raise your hand; this
          page continues by itself once they approve.
        </div>
      )}
      <div className="stu-row">
        <button ref={startBtn} type="submit" className="stu-btn primary lg" style={{ flex: "1 1 160px" }} disabled={busy || waiting}>
          {waitingSwap ? "Waiting for your teacher…" : busy ? "Checking…" : "Start exam"}
        </button>
        {onCancel && (
          <button type="button" className="stu-btn lg" onClick={onCancel} disabled={busy}>
            Cancel
          </button>
        )}
      </div>
    </form>
  );
}
