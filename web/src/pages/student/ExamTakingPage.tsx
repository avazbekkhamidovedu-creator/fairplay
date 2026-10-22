import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";
import { ApiError, NetworkError, currentStudentId, getToken, slice6Api, studentApi } from "../../api/client";
import type { AllowedApp, AnswerMap, AnswerValue, AttemptView, IntegrityEvent } from "../../api/types";
import { NotLockedBanner } from "../../components/LockBanner";
import { AnswerInput, RubricPanel } from "../../components/student/AnswerInput";
import { CodeQuestion } from "../../components/student/CodeQuestion";
import { preloadCodeEditor } from "../../components/code/LazyCodeEditor";
import { prefetchRunner } from "../../lib/code/runners";
import { canRunInBrowser, resolveLanguage } from "../../lib/code/languages";
import { LockedBar } from "../../components/student/LockedBar";
import { ReviewPanel } from "../../components/student/ReviewPanel";
import { StuLoading } from "../../components/student/StudentLayout";
import { ArrowLeftIcon, ArrowRightIcon, BookmarkIcon, ChevronIcon, CloseIcon, CrossOutIcon, PinIcon } from "../../components/student/icons";
import {
  type Crossed,
  nextTextSize,
  readCrossMode,
  readCrossed,
  readTextSize,
  readTimerHidden,
  toggleCrossed,
  writeCrossMode,
  writeCrossed,
  writeTextSize,
  writeTimerHidden,
} from "../../lib/examPrefs";
import { useStudentIdentity } from "../../lib/useStudentIdentity";
import { useBattery } from "../../lib/battery";
import { AnswerQueue, type SyncStatus } from "../../lib/answerQueue";
import * as bridge from "../../lib/bridge";
import { readTrialKey, writeTrialKey } from "../../lib/trialKey";
import { clearAttemptSession, getAttemptSession } from "../../lib/appSession";
import { clockOffset, remainingMs } from "../../lib/timer";
import {
  cacheAttemptView,
  cachedAttemptView,
  clearAttemptView,
  clearOfflineRecord,
  examIdOfOffline,
  isOfflineAttemptId,
  readOfflineRecord,
  saveOfflineRecord,
  NeedStartCodeError,
  forgetStartCode,
  rememberStartCode,
  rememberedStartCode,
} from "../../lib/offline";
import { ReenterExam, isSessionConflict } from "../../components/student/ReenterExam";
import { AppsToClose, PreflightProblems } from "../../components/student/Preflight";
import { TimeUpOffline } from "../../components/student/TimeUpOffline";
import { CODE_LENGTH, StartCodeBoxes, emptyCode } from "../../components/student/StartCodeBoxes";
import { QuestionImages } from "../../components/QuestionImages";
import { TrialExit } from "../../components/student/TrialExit";
import { Calculator } from "../../components/student/Calculator";
import { MaterialsPaneContent, WebsitesPaneContent } from "../../components/student/ExamToolPanes";
import { ToolPane, clampPaneWidth, readPaneWidth } from "../../components/student/ToolPane";
import { BookIcon, GlobeIcon } from "../../components/student/icons";
import { forgetAttemptMaterials, loadAttemptMaterials } from "../../lib/materials";
import "../../styles/student.css";

export const HEARTBEAT_MS = 5000;
export const EVENT_RETRY_MS = 3000;
export const SUBMIT_RETRY_MS = 3000;

/** "Mark for review" flags are local only, kept per attempt in localStorage. */
export const marksStorageKey = (aid: string) => `proctora:marks:${aid}`;

export function readMarks(aid: string): Set<string> {
  try {
    const raw = localStorage.getItem(marksStorageKey(aid));
    const v = raw ? (JSON.parse(raw) as unknown) : [];
    return new Set(Array.isArray(v) ? v.filter((x): x is string => typeof x === "string") : []);
  } catch {
    return new Set();
  }
}

function writeMarks(aid: string, marks: Set<string>): void {
  try {
    if (marks.size === 0) localStorage.removeItem(marksStorageKey(aid));
    else localStorage.setItem(marksStorageKey(aid), JSON.stringify([...marks]));
  } catch {
    /* storage unavailable: marks stay in memory */
  }
}

/** 2026-10-23: the "Directions" list under the exam title. */
export function examDirections(durationMin: number, count: number): string[] {
  return [
    `${count} ${count === 1 ? "question" : "questions"} · ${durationMin} minutes.`,
    "Answer in any order. Use the question list at the bottom to jump between questions.",
    "Mark for review flags a question so you can come back to it.",
    "On multiple choice, the ABC tool lets you cross out answers you think are wrong.",
    "Your answers save automatically, even if the Wi-Fi drops.",
    "When you finish, open the review page and submit.",
  ];
}

type Phase =
  | "loading" | "locking" | "preflight" | "closeApps" | "lockFailed" | "exam" | "review" | "submitting"
  | "loadError" | "reenter"
  // sample exam: the app refused the lock for this attempt (offer to start again)
  | "trialEnded";

export interface DoneState {
  receipt: string;
  submitted_at: string;
  title: string;
  /** answered / total question counts at submit time (optional: unknown after a reload) */
  answered?: number;
  total?: number;
  /** Audit W-05: why the page stopped (shown on the done page). */
  notice?: string;
  /** 2026-10-22: the sample exam: the done page waits for the unlock, then goes home. */
  trial?: "stopped" | "finished";
}


/** Remount per attempt id so no state leaks between attempts. */
export default function ExamTakingPage() {
  const { attemptId = "" } = useParams();
  return <ExamTakingInner key={attemptId} />;
}

function ExamTakingInner() {
  const { attemptId = "" } = useParams();
  const navigate = useNavigate();
  const location = useLocation();

  const [phase, setPhase] = useState<Phase>("loading");
  const [attempt, setAttempt] = useState<AttemptView | null>(null);
  const [loadError, setLoadError] = useState<string>("");
  const [lockReason, setLockReason] = useState<string>("");
  const [answers, setAnswers] = useState<AnswerMap>({});
  const [sync, setSync] = useState<SyncStatus>("synced");
  const [current, setCurrent] = useState(0);
  const [deadline, setDeadline] = useState<string>("");
  const [offset, setOffset] = useState(0);
  const [now, setNow] = useState(Date.now());
  const [locked, setLocked] = useState(bridge.isLocked());
  const [submitError, setSubmitError] = useState<string | null>(null);
  // S5: the automatic hand-in failed (offline): offer retry / seal / help.
  const [timeUpProblem, setTimeUpProblem] = useState(false);
  // S3: back after FairPlay closed; the teacher must approve before questions show again.
  const [waiting, setWaiting] = useState(false);
  const [marked, setMarked] = useState<Set<string>>(() => readMarks(attemptId));
  // 2026-10-23 exam screen: cross-out, hidden timer and text size (this device only).
  const [crossed, setCrossed] = useState<Crossed>(() => readCrossed(attemptId));
  const [crossMode, setCrossMode] = useState(() => readCrossMode(attemptId));
  const [timerHidden, setTimerHidden] = useState(() => readTimerHidden(attemptId));
  const [textSize, setTextSize] = useState(readTextSize);
  const me = useStudentIdentity();
  // 2026-10-23: sent with the heartbeat so the invigilator sees low batteries.
  const battery = useBattery();
  const batteryRef = useRef(battery);
  batteryRef.current = battery;
  // 2026-10-22: sample exam ("Stop practice"). trialRef is set as soon as the attempt loads.
  const trialRef = useRef(false);
  const [stopping, setStopping] = useState(false);
  const [stopProblem, setStopProblem] = useState<string | null>(null);
  const [trialKey, setTrialKey] = useState<string | null>(() => readTrialKey(attemptId));
  const [restartError, setRestartError] = useState<string | null>(null);

  const queueRef = useRef<AnswerQueue | null>(null);
  const currentRef = useRef(0);
  currentRef.current = current;
  const finishedRef = useRef(false);
  const submittingRef = useRef(false);
  const eventsRef = useRef<IntegrityEvent[]>([]);
  const eventTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const submitRetryRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const phaseBeforeSubmit = useRef<Phase>("exam");

  // ----- slice 6: offline attempts ("offline:<exam_id>", started from the package) -----
  // realAidRef is the server attempt id; null while an offline attempt has not reached the
  // server yet. Every API call goes through call(): online attempts call straight through,
  // offline ones first POST start with offline_started_at (once), then use the real id.
  const appsRef = useRef<AllowedApp[] | undefined>(undefined);
  // 2026-10-23 (exam tools): the exam's websites, handed to the app with "lock" / "session"
  const sitesRef = useRef<string[]>([]);
  const offline = isOfflineAttemptId(attemptId);
  const offlineExamId = offline ? examIdOfOffline(attemptId) : "";
  const realAidRef = useRef<string | null>(
    offline ? (readOfflineRecord(offlineExamId)?.real_attempt_id ?? null) : attemptId,
  );
  const [connected, setConnected] = useState(!!realAidRef.current);
  const [offlineProblem, setOfflineProblem] = useState<string | null>(null);
  // Audit W-03 / W-02: the start code is never stored. After a reload (or a new code from the
  // teacher) the student types it again to send the offline answers.
  const [needCode, setNeedCode] = useState<null | "reload" | "changed">(null);
  const [preflightProblems, setPreflightProblems] = useState<string[]>([]);
  // 2026-10-22: apps the lock will close, shown first so the student can save their work.
  const [appsToClose, setAppsToClose] = useState<string[]>([]);
  const [closingApps, setClosingApps] = useState(false);
  const appsConfirmedRef = useRef(false);
  const reconnectRef = useRef<Promise<string> | null>(null);

  const ensureOnline = useCallback((): Promise<string> => {
    if (realAidRef.current) return Promise.resolve(realAidRef.current);
    if (reconnectRef.current) return reconnectRef.current;
    const rec = readOfflineRecord(offlineExamId);
    if (!rec) return Promise.reject(new ApiError(404, "This exam attempt was not found."));
    const code = rememberedStartCode(offlineExamId);
    if (!code) {
      setNeedCode((n) => n ?? "reload");
      return Promise.reject(new NeedStartCodeError("reload"));
    }
    const p = slice6Api
      .startAfterOffline(offlineExamId, code, rec.started_at)
      .then(
        (view) => {
          realAidRef.current = view.attempt_id;
          saveOfflineRecord({ ...rec, real_attempt_id: view.attempt_id });
          setConnected(true);
          setNeedCode(null);
          setOfflineProblem(null);
          setDeadline(view.deadline_at);
          setOffset(clockOffset(view.server_time));
          return view.attempt_id;
        },
        (e) => {
          if (e instanceof ApiError && e.status === 403 && /start code/i.test(e.detail)) {
            // The teacher made a new start code (audit W-02): ask for it, keep the answers.
            forgetStartCode(offlineExamId);
            setNeedCode("changed");
            throw new NeedStartCodeError("changed");
          }
          if (e instanceof ApiError)
            setOfflineProblem(
              `${e.detail}. Your answers are kept on this computer — tell your teacher before closing this page.`,
            );
          throw e;
        },
      )
      .finally(() => {
        reconnectRef.current = null;
      });
    reconnectRef.current = p;
    return p;
  }, [offlineExamId]);

  const call = useCallback(
    <T,>(fn: (aid: string) => Promise<T>): Promise<T> => {
      const aid = realAidRef.current;
      return aid ? fn(aid) : ensureOnline().then(fn);
    },
    [ensureOnline],
  );

  // ----- finishing -----

  const finish = useCallback(
    (done: DoneState | null) => {
      if (finishedRef.current) return;
      finishedRef.current = true;
      queueRef.current?.stop();
      queueRef.current?.clearStorage();
      try {
        localStorage.removeItem(marksStorageKey(attemptId));
      } catch {
        /* ignore */
      }
      clearAttemptView(attemptId);
      const doneAid = realAidRef.current ?? attemptId;
      if (offline) {
        clearOfflineRecord(offlineExamId);
        clearAttemptView(doneAid);
      }
      clearAttemptSession(doneAid);
      forgetAttemptMaterials(doneAid);
      // Sample exam: while the app is still locked the done page keeps the key, so a hand-in the
      // app cannot confirm still has a way out there (it clears the key once unlocked).
      if (trialRef.current && !bridge.isLocked()) writeTrialKey(doneAid, null);
      // The app unlocks only after the server confirms the hand-in (audit #11).
      bridge.unlock();
      // The done page is allowed while locked; for the sample exam it waits for the unlock
      // and then goes to the dashboard (no receipt, no score).
      const state = trialRef.current
        ? { receipt: "", submitted_at: "", title: done?.title ?? "", trial: done?.trial ?? "finished" }
        : done ?? undefined;
      navigate(`/student/done/${encodeURIComponent(doneAid)}`, { replace: true, state });
    },
    [attemptId, navigate, offline, offlineExamId],
  );

  const doSubmit = useCallback(
    async (auto: boolean) => {
      const q = queueRef.current;
      if (!q || finishedRef.current || submittingRef.current) return;
      submittingRef.current = true;
      if (submitRetryRef.current) clearTimeout(submitRetryRef.current);
      setPhase((p) => {
        if (p !== "submitting") phaseBeforeSubmit.current = p;
        return "submitting";
      });
      setSubmitError(null);
      try {
        const sent = q.getAnswers();
        const seq = q.getClientSeq();
        const res = await call((aid) => studentApi.submit(aid, sent, seq));
        submittingRef.current = false;
        const total = attempt?.questions.length;
        const answered = attempt ? attempt.questions.filter((qq) => sent[qq.id] !== undefined).length : undefined;
        finish({ receipt: res.receipt, submitted_at: res.submitted_at, title: attempt?.title ?? "", answered, total });
      } catch (e) {
        submittingRef.current = false;
        // Audit W-05: a 4xx the student can't fix by waiting is final. 409/404 (already
        // handed in, moved to another computer, gone): leave the exam; the app checks with
        // the server before it unlocks.
        if (e instanceof ApiError && (e.status === 409 || e.status === 404)) {
          finish({ receipt: "", submitted_at: "", title: attempt?.title ?? "", notice: e.detail });
          return;
        }
        if (e instanceof NeedStartCodeError) {
          setSubmitError("Type the start code again (above) to hand in. Your answers are saved on this computer.");
          setPhase(phaseBeforeSubmit.current);
          return;
        }
        const final = e instanceof ApiError && e.status >= 400 && e.status < 500 && e.status !== 429;
        const msg =
          e instanceof NetworkError
            ? "Cannot reach the server. Your answers are saved on this computer."
            : e instanceof ApiError
              ? e.detail
              : "Submitting failed.";
        if (!final && (auto || e instanceof NetworkError)) {
          setSubmitError(`${msg} Trying again…`);
          if (auto) setTimeUpProblem(true);
          submitRetryRef.current = setTimeout(() => void doSubmit(auto), SUBMIT_RETRY_MS);
        } else {
          setSubmitError(msg);
          setPhase(phaseBeforeSubmit.current);
        }
      }
    },
    [attempt, finish, call],
  );
  const doSubmitRef = useRef(doSubmit);
  doSubmitRef.current = doSubmit;

  // S5: "Submit later from this computer": the app seals exactly these answers.
  const sealAnswers = useCallback(async (): Promise<boolean> => {
    const q = queueRef.current;
    const aid = realAidRef.current;
    if (!q || !aid) return false;     // an offline start that never reached the server has no key
    return bridge.seal({ attempt_id: aid, answers_json: JSON.stringify(q.getAnswers()), client_seq: q.getClientSeq() });
  }, []);

  // ----- load attempt (from start navigation or GET for resume) -----

  useEffect(() => {
    let alive = true;
    const fromState = (location.state as { attempt?: AttemptView } | null)?.attempt;
    let load: Promise<AttemptView>;
    if (fromState && fromState.attempt_id === attemptId) load = Promise.resolve(fromState);
    else if (offline) {
      // reload during an offline attempt: the local copy is the source (local clock)
      const rec = readOfflineRecord(offlineExamId);
      load = rec
        ? Promise.resolve({ ...rec.view, server_time: new Date().toISOString() })
        : Promise.reject(new ApiError(404, "This exam attempt was not found."));
    } else
      load = studentApi.getAttempt(attemptId).catch((e: unknown) => {
        // slice 6: resume from the last copy on this computer when the server is unreachable
        const cached = e instanceof NetworkError ? cachedAttemptView(attemptId) : null;
        if (cached) return cached;
        throw e;
      });
    load.then(
      (a) => {
        if (!alive) return;
        trialRef.current = a.trial === true;
        if (!offline && a.status !== "submitted") cacheAttemptView(a, clockOffset(a.server_time));
        if (a.mode === "external") {
          // Exams held on another website run in their own page (CONTRACT slice 3).
          navigate(`/student/external/${encodeURIComponent(attemptId)}`, { replace: true, state: { attempt: a } });
          return;
        }
        if (a.status === "submitted") {
          finish(null);
          return;
        }
        const queue = new AnswerQueue(attemptId, (ans, seq) => call((aid) => studentApi.saveAnswers(aid, ans, seq)), {
          owner: currentStudentId(),      // audit 2026-10-13: this account's copy only
          onClosed: () => void doSubmitRef.current(true),
        });
        queue.subscribe((s) => {
          setSync(s);
          setAnswers(queue.getAnswers());
        });
        queueRef.current = queue;
        queue.init(a.answers);
        setAnswers(queue.getAnswers());
        appsRef.current = a.allowed_apps;   // 2026-10-14: handed to the app with "lock"
        sitesRef.current = [...(a.tools?.websites ?? [])];
        setWaiting(a.needs_approval === true || a.approval === "pending");
        setAttempt(a);
        setDeadline(a.deadline_at);
        setOffset(clockOffset(a.server_time));
        setPhase("locking");
      },
      (e) => {
        if (!alive) return;
        if (e instanceof ApiError && isSessionConflict(e.status, e.detail) && cachedAttemptView(attemptId)?.trial) {
          setPhase("trialEnded");      // sample exam: no start code, offer a new one
          return;
        }
        if (e instanceof ApiError && isSessionConflict(e.status, e.detail)) {
          setLoadError(e.detail);       // audit W-01: offer the start code instead of a dead end
          setPhase("reenter");
          return;
        }
        setLoadError(
          e instanceof ApiError && e.status === 404
            ? "This exam attempt was not found."
            : e instanceof ApiError
              ? e.detail
              : "Cannot reach the server. Check the connection and reload the page.",
        );
        setPhase("loadError");
      },
    );
    return () => {
      alive = false;
      queueRef.current?.stop();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [attemptId]);

  // ----- lock before showing questions -----

  const lockingRef = useRef(false);
  const lock = useCallback(async () => {
    if (lockingRef.current || finishedRef.current) return;
    lockingRef.current = true;
    setPhase("locking");
    // Audit M-11: problems the student must fix outside the lock come first.
    const allowed = bridge.allowedAppIds(appsRef.current);
    const report = await bridge.preflightReport(3000, allowed);
    // Stopped (sample exam) or handed in meanwhile: never lock after that.
    if (finishedRef.current) {
      lockingRef.current = false;
      return;
    }
    const problems = report?.problems ?? [];
    if (problems.length > 0) {
      lockingRef.current = false;
      setPreflightProblems(problems);
      setPhase("preflight");
      return;
    }
    if (!appsConfirmedRef.current && report && report.apps_to_close.length > 0) {
      lockingRef.current = false;
      setAppsToClose(report.apps_to_close);
      setPhase("closeApps");
      return;
    }
    const aid = realAidRef.current;
    const session = aid ? getAttemptSession(aid) : null;
    const token = getToken("student");
    if (finishedRef.current) {
      lockingRef.current = false;
      return;
    }
    const res = await bridge.requestLock(
      10000,
      aid && session && token
        ? { attempt_id: aid, token, session, allowed_apps: bridge.allowedAppIds(appsRef.current), allowed_sites: sitesRef.current }
        : { allowed_apps: bridge.allowedAppIds(appsRef.current), allowed_sites: sitesRef.current },   // offline start
    );
    lockingRef.current = false;
    if (finishedRef.current) return;
    if (res.ok) {
      setLocked(bridge.isLocked());
      setPhase("exam");
    } else if (res.reason === "bad_session" && trialRef.current) {
      // Sample exam: no start code to type; offer a fresh start instead.
      setPhase("trialEnded");
    } else if (res.reason === "bad_session") {
      // Audit M-06: the app won't lock without a valid exam session: start code again.
      setLoadError("This computer has no valid session for this exam.");
      setPhase("reenter");
    } else {
      setLockReason(res.reason);
      setPhase("lockFailed");
    }
  }, []);

  useEffect(() => {
    if (phase === "locking" && attempt) void lock();
    // only when we enter "locking" after load
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [attempt]);

  // ----- sample exam: offline exit key (while online) and "Stop practice" -----
  useEffect(() => {
    const aid = realAidRef.current;
    if (!attempt?.trial || !aid || trialKey) return;
    let alive = true;
    studentApi.trialKey(aid).then(
      (r) => {
        if (!alive) return;
        writeTrialKey(aid, r.key);
        setTrialKey(r.key);
      },
      () => undefined, // offline / ended: Stop through the server is still the normal way
    );
    return () => {
      alive = false;
    };
  }, [attempt, trialKey]);

  const stopPractice = useCallback(async () => {
    if (finishedRef.current || submittingRef.current) return;
    setStopping(true);
    setStopProblem(null);
    try {
      // Wait for the server to commit the stop: only then does the app's signed hand-in
      // say "submitted" and the app unlock (bridge.unlock in finish).
      await call((aid) => studentApi.trialStop(aid));
      finish({ receipt: "", submitted_at: "", title: attempt?.title ?? "", trial: "stopped" });
    } catch (e) {
      setStopping(false);
      if (e instanceof ApiError && e.status === 404) {
        // Gone (replaced by a newer sample attempt): the app hears "gone" and unlocks.
        finish({ receipt: "", submitted_at: "", title: attempt?.title ?? "", trial: "stopped" });
        return;
      }
      setStopProblem(
        e instanceof NetworkError
          ? "Cannot reach the server, so FairPlay cannot unlock yet."
          : e instanceof ApiError
            ? e.detail
            : "Stopping failed.",
      );
    }
  }, [attempt, call, finish]);

  const restartTrial = useCallback(async () => {
    setRestartError(null);
    try {
      const a = await studentApi.startTrial();
      navigate(`/student/exam/${encodeURIComponent(a.attempt_id)}`, { replace: true, state: { attempt: a } });
    } catch (e) {
      setRestartError(e instanceof ApiError ? e.detail : "Cannot reach the server. Try again.");
    }
  }, [navigate]);

  // ----- the app's own signed beats need the attempt + session (audit #2, #6) -----
  useEffect(() => {
    const aid = realAidRef.current;
    const session = aid ? getAttemptSession(aid) : null;
    const token = getToken("student");
    if (attempt && aid && session && token)
      bridge.setExamSession({
        attempt_id: aid,
        token,
        session,
        allowed_apps: bridge.allowedAppIds(attempt.allowed_apps),
        allowed_sites: [...(attempt.tools?.websites ?? [])],   // 2026-10-23
      });
  }, [attempt, connected]);

  // ----- native messages: lock state + integrity events -----

  const flushEvents = useCallback(async () => {
    if (eventTimerRef.current) {
      clearTimeout(eventTimerRef.current);
      eventTimerRef.current = null;
    }
    const batch = eventsRef.current;
    if (batch.length === 0) return;
    eventsRef.current = [];
    try {
      await call((aid) => studentApi.events(aid, batch));
    } catch (e) {
      if (e instanceof ApiError && e.status !== 429 && e.status < 500) return; // rejected: drop
      eventsRef.current = [...batch, ...eventsRef.current];
      eventTimerRef.current = setTimeout(() => void flushEvents(), EVENT_RETRY_MS);
    }
  }, [call]);

  useEffect(() => {
    const off = bridge.onNative((msg) => {
      if (msg.type === "state" || msg.type === "locked" || msg.type === "unlocked" || msg.type === "lockFailed") {
        setLocked(bridge.isLocked());
      }
      if (msg.type === "event" && msg.event) {
        eventsRef.current.push({
          type: String(msg.event.type),
          detail: String(msg.event.detail ?? ""),
          client_time: msg.event.client_time ?? new Date().toISOString(),
        });
        void flushEvents();
      }
    });
    bridge.requestEvents();   // review 2026-10-14: events the app held for us
    return () => {
      off();
      if (eventTimerRef.current) clearTimeout(eventTimerRef.current);
    };
  }, [flushEvents]);

  // ----- heartbeat every 5 s -----

  useEffect(() => {
    if (!attempt) return;
    const beat = async () => {
      if (finishedRef.current) return;
      try {
        const res = await call((aid) => studentApi.heartbeat(aid, currentRef.current, bridge.isLocked(), batteryRef.current));
        setOffset(clockOffset(res.server_time));
        if (res.deadline_at) setDeadline(res.deadline_at);
        if (res.approval !== undefined) setWaiting(res.approval === "pending");
        if (res.status === "submitted") {
          // closed by the teacher (or submitted elsewhere): finish and unlock
          if (!submittingRef.current) void doSubmitRef.current(true);
        }
      } catch {
        /* offline: keep going, answers stay on this computer */
      }
    };
    void beat();
    const t = setInterval(beat, HEARTBEAT_MS);
    return () => clearInterval(t);
  }, [attempt, attemptId, call]);

  // ----- countdown + auto submit at 0 -----

  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 500);
    return () => {
      clearInterval(t);
      if (submitRetryRef.current) clearTimeout(submitRetryRef.current);
    };
  }, []);

  const remaining = deadline ? remainingMs(deadline, offset, now) : Infinity;
  useEffect(() => {
    if (!attempt || !deadline) return;
    // S3: while waiting for the teacher the time is given back on approval: don't hand in.
    if (waiting) return;
    if (remaining <= 0 && (phase === "exam" || phase === "review" || phase === "lockFailed" || phase === "preflight")) {
      void doSubmit(true);
    }
  }, [remaining, phase, attempt, deadline, doSubmit, waiting]);

  // ----- answering -----

  const setAnswer = useCallback((qid: string, value: AnswerValue | undefined) => {
    queueRef.current?.set(qid, value);
    if (queueRef.current) setAnswers(queueRef.current.getAnswers());
  }, []);

  const questions = attempt?.questions ?? [];
  // Phase 4: load the runtime of runnable coding questions in idle time (Python is large)
  const runLangs = useMemo(
    () =>
      [...new Set(questions.filter((x) => x.type === "code" && !x.write_only).map((x) => resolveLanguage(x.language)))].filter(
        canRunInBrowser,
      ),
    [questions],
  );
  const runLangKey = runLangs.join(",");
  const hasCode = questions.some((x) => x.type === "code");
  useEffect(() => {
    // the editor chunk loads now, while the exam is surely online
    if (hasCode) void preloadCodeEditor().catch(() => undefined);
  }, [hasCode]);
  useEffect(() => {
    if (phase !== "exam" && phase !== "review") return;
    for (const l of runLangs) if (l === "python") prefetchRunner(l);
    // once per exam screen
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runLangKey, phase === "exam" || phase === "review"]);
  const unanswered = useMemo(
    () => questions.map((q, i) => ({ q, i })).filter(({ q }) => answers[q.id] === undefined),
    [questions, answers],
  );


  // ----- mark for review (local only) -----

  const toggleMark = useCallback(
    (qid: string) => {
      setMarked((prev) => {
        const next = new Set(prev);
        if (next.has(qid)) next.delete(qid);
        else next.add(qid);
        writeMarks(attemptId, next);
        return next;
      });
    },
    [attemptId],
  );

  const toggleCross = useCallback(
    (qid: string, index: number) => {
      setCrossed((prev) => {
        const next = toggleCrossed(prev, qid, index);
        writeCrossed(attemptId, next);
        return next;
      });
    },
    [attemptId],
  );
  const flipCrossMode = () =>
    setCrossMode((on) => {
      writeCrossMode(attemptId, !on);
      return !on;
    });
  const hideTimer = (hidden: boolean) => {
    writeTimerHidden(attemptId, hidden);
    setTimerHidden(hidden);
  };
  const cycleTextSize = () =>
    setTextSize((t) => {
      const n = nextTextSize(t);
      writeTextSize(n);
      return n;
    });

  // Move focus to the question when the student changes question (not on first show).
  const promptRef = useRef<HTMLHeadingElement>(null);
  const shownRef = useRef(false);
  useEffect(() => {
    if (phase !== "exam") return;
    if (!shownRef.current) {
      shownRef.current = true;
      return;
    }
    promptRef.current?.focus();
  }, [current, phase]);

  const goTo = (i: number) => {
    setCurrent(i);
    setPhase("exam");
  };

  // ----- question navigator panel (opens from the bottom bar) -----

  const [navOpen, setNavOpen] = useState(false);
  const navToggleRef = useRef<HTMLButtonElement>(null);
  const navPanelRef = useRef<HTMLDivElement>(null);
  const closeNav = useCallback((restoreFocus: boolean) => {
    setNavOpen(false);
    if (restoreFocus) navToggleRef.current?.focus();
  }, []);
  useEffect(() => {
    if (phase !== "exam") setNavOpen(false);
  }, [phase]);
  useEffect(() => {
    if (!navOpen) return;
    // focus the current question's cell so arrow/Tab navigation starts there
    navPanelRef.current?.querySelector<HTMLElement>("[aria-current=step]")?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") closeNav(true);
    };
    const onDown = (e: MouseEvent) => {
      const t = e.target as Node;
      if (navPanelRef.current?.contains(t) || navToggleRef.current?.contains(t)) return;
      closeNav(false);
    };
    window.addEventListener("keydown", onKey);
    window.addEventListener("mousedown", onDown);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("mousedown", onDown);
    };
  }, [navOpen, closeNav]);

  // ----- 2026-10-23 exam tools: calculator, materials, websites -----

  const tools = attempt?.tools;
  const calcMode = tools?.calculator === "scientific" || tools?.calculator === "graphing" ? tools.calculator : null;
  const [calcOpen, setCalcOpen] = useState(false);
  const [calcMounted, setCalcMounted] = useState(false);   // stays mounted: keeps its state
  const calcButtonRef = useRef<HTMLButtonElement>(null);
  const toggleCalc = () => {
    setCalcMounted(true);
    setCalcOpen((o) => !o);
  };
  const closeCalc = () => {
    setCalcOpen(false);
    calcButtonRef.current?.focus();
  };
  const [pane, setPane] = useState<null | "materials" | "websites">(null);
  const [paneWidth, setPaneWidth] = useState(() =>
    clampPaneWidth(readPaneWidth(), typeof window === "undefined" ? 1280 : window.innerWidth),
  );
  const [materialCount, setMaterialCount] = useState<number | null>(null);
  const materialsAid = connected ? realAidRef.current : null;
  useEffect(() => {
    // fetched once when the exam opens (kept in memory for a Wi-Fi drop later)
    if (!tools?.open_book || !materialsAid || phase === "loading") return;
    let alive = true;
    loadAttemptMaterials(materialsAid).then(
      (f) => alive && setMaterialCount(f.length),
      () => undefined,
    );
    return () => {
      alive = false;
    };
  }, [tools?.open_book, materialsAid, phase]);

  // ----- render -----

  if (phase === "loading")
    return (
      <div className="stu stu-exam">
        <div className="stu-center">
          <StuLoading label="Loading your exam…" />
        </div>
      </div>
    );
  const trialEndedPanel = (
        <main className="stu-center" data-testid="trial-ended">
          <div className="stu-card" style={{ gap: 14, maxWidth: 560 }} role="alert">
            <h1 style={{ margin: 0, fontSize: 22 }}>These sample questions were ended on this computer</h1>
            <p style={{ margin: 0 }}>Start again from Test my device. Nothing from the sample questions is kept or graded.</p>
            {restartError && (
              <p className="stu-alert error" role="alert" style={{ margin: 0 }}>
                {restartError}
              </p>
            )}
            <div className="stu-row">
              <button type="button" className="stu-btn primary" onClick={() => void restartTrial()}>
                Start again
              </button>
              <Link to="/student" className="stu-btn">
                Back to home
              </Link>
            </div>
          </div>
        </main>
  );
  if (phase === "trialEnded" && !attempt) return <div className="stu stu-exam">{trialEndedPanel}</div>;
  if (phase === "reenter") return <ReenterExam attemptId={attemptId} detail={loadError} />;
  if (phase === "loadError")
    return (
      <div className="stu stu-exam">
        <main className="stu-center">
          <div className="stu-card">
            <h1 style={{ margin: 0, fontSize: 22 }}>Cannot open the exam</h1>
            <p className="stu-muted">{loadError}</p>
            <div className="stu-row">
              <button type="button" className="stu-btn primary" onClick={() => window.location.reload()}>
                Try again
              </button>
              <Link to="/student" className="stu-btn">
                Back to home
              </Link>
            </div>
          </div>
        </main>
      </div>
    );
  if (!attempt) return null;

  const q = questions[current];
  const answeredCount = questions.length - unanswered.length;
  const markedCount = questions.filter((qq) => marked.has(qq.id)).length;
  const isMarked = q ? marked.has(q.id) : false;
  const timeUpPanel = phase === "submitting" && timeUpProblem;
  const waitPanel = waiting && (phase === "exam" || phase === "review");
  const stopPanel = !!stopProblem && !timeUpPanel;   // sample exam: Stop could not reach the server
  const showExam =
    !waitPanel && !stopPanel && (phase === "exam" || (phase === "submitting" && !timeUpPanel && phaseBeforeSubmit.current === "exam"));
  const showReview =
    !waitPanel && !stopPanel && (phase === "review" || (phase === "submitting" && !timeUpPanel && phaseBeforeSubmit.current !== "exam"));
  const typeLabel = q?.type === "essay" ? " · Essay" : q?.type === "code" ? " · Coding" : "";

  const isLast = current === questions.length - 1;
  const toolsShown = showExam || showReview;
  const paneShown = toolsShown && !!pane && (pane === "materials" ? !!tools?.open_book : (tools?.websites?.length ?? 0) > 0);

  return (
    <div
      className={`stu stu-exam${paneShown ? " has-pane" : ""}`}
      style={{ ["--stu-text-scale" as string]: String(textSize), ["--stu-pane-w" as string]: `${paneWidth}px` }}
    >
      <NotLockedBanner />
      <LockedBar
        title={attempt.title}
        course={attempt.course}
        locked={bridge.isNative() && locked}
        native={bridge.isNative()}
        sync={sync}
        remaining={remaining}
        timerHidden={timerHidden}
        onTimerHidden={hideTimer}
        textSize={textSize}
        onTextSize={cycleTextSize}
        calculator={calcMode && toolsShown ? { open: calcOpen, onToggle: toggleCalc, buttonRef: calcButtonRef } : undefined}
        materials={tools?.open_book && toolsShown ? { count: materialCount, onOpen: () => setPane("materials") } : undefined}
        websites={
          tools && tools.websites.length > 0 && toolsShown
            ? { count: tools.websites.length, onOpen: () => setPane("websites") }
            : undefined
        }
        directions={attempt.trial ? undefined : examDirections(attempt.duration_min, questions.length)}
        trial={
          attempt.trial
            ? {
                onStop: () => void stopPractice(),
                stopping,
                // never while a lock request is on its way (the app could lock after the stop)
                disabled: phase === "locking" || closingApps || phase === "submitting",
              }
            : undefined
        }
      />
      {offline && (offlineProblem || !connected) && !needCode && (
        <div className="stu-alert warn fp-offline-note" role="status" data-testid="offline-banner">
          {offlineProblem ??
            "Working offline. Your answers are kept on this computer and sent when the connection is back."}
        </div>
      )}
      {offline && needCode && (
        <ReconnectCode
          reason={needCode}
          onCode={(code) => {
            rememberStartCode(offlineExamId, code);
            setNeedCode(null);
            void ensureOnline().catch(() => undefined);
          }}
        />
      )}

      {phase === "locking" && (
        <div className="stu-center">
          <StuLoading label="Locking this computer for the exam…" />
        </div>
      )}

      {phase === "preflight" && !stopPanel && <PreflightProblems problems={preflightProblems} onRetry={() => lock()} trial={!!attempt.trial} />}
      {phase === "closeApps" && !stopPanel && (
        <AppsToClose
          apps={appsToClose}
          busy={closingApps}
          onContinue={() => {
            setClosingApps(true);
            void bridge.quitApps(bridge.allowedAppIds(appsRef.current)).then(() => {
              // Whatever is still open is closed by the lock itself.
              appsConfirmedRef.current = true;
              setClosingApps(false);
              void lock();
            });
          }}
        />
      )}

      {phase === "lockFailed" && !stopPanel && (
        <main className="stu-center">
          <div className="stu-card" style={{ gap: 14 }}>
            <h1 style={{ margin: 0, fontSize: 22 }}>Locked mode could not start</h1>
            <p style={{ margin: 0, color: "var(--s-text-2)" }}>
              {bridge.nativePlatform() === "ipad"
                ? "FairPlay could not turn on Assessment Mode on this iPad."
                : lockReason === "accessibility"
                ? "FairPlay needs the Accessibility permission to lock this computer."
                : "The FairPlay app could not lock this computer."}
            </p>
            {bridge.nativePlatform() === "ipad" ? (
<ol style={{ margin: 0, paddingLeft: 20, lineHeight: 1.7, color: "var(--s-text-2)" }}>
              <li>Press Retry and tap Yes when iPadOS asks to start Assessment Mode.</li>
              <li>Stop screen recording, Screen Mirroring or AirPlay, and close Split View.</li>
              <li>Still stuck? Ask the invigilator.</li>
            </ol>
) : (
<ol style={{ margin: 0, paddingLeft: 20, lineHeight: 1.7, color: "var(--s-text-2)" }}>
              <li>Open System Settings → Privacy &amp; Security → Accessibility.</li>
              <li>Turn on FairPlay.</li>
              <li>Come back here and press Retry.</li>
            </ol>
)}
            <p className="stu-muted stu-small" style={{ margin: 0 }}>
              {attempt.trial
                ? "FairPlay needs this for every exam. Turn it on now, so it is ready on exam day."
                : "Your exam time is running. Ask your teacher if you need help."}
            </p>
            <div>
              <button type="button" className="stu-btn primary lg" onClick={() => void lock()}>
                Retry
              </button>
            </div>
          </div>
        </main>
      )}

      {showExam && q && (
        <>
          <div className={`stu-exam-body${q.type === "code" ? " code" : ""}`}>
            <main className={`stu-paper stu-question${q.type === "code" ? " stu-cq-paper" : ""}`} aria-labelledby="question-prompt">
              <div className="stu-qhead">
                <span className="stu-qnum" aria-hidden="true">
                  {current + 1}
                </span>
                <span className="sr-only">
                  Question {current + 1} of {questions.length}
                </span>
                <span className="stu-qhead-pts">
                  {q.points} {q.points === 1 ? "point" : "points"}
                  {typeLabel}
                </span>
                <button
                  type="button"
                  role="checkbox"
                  aria-checked={isMarked}
                  className="stu-mark"
                  onClick={() => toggleMark(q.id)}
                >
                  <BookmarkIcon />
                  {isMarked ? "Marked for review" : "Mark for review"}
                </button>
                {q.type === "mcq" && (
                  <button
                    type="button"
                    className="stu-crossout-tool"
                    aria-pressed={crossMode}
                    aria-label="Cross out answers"
                    title="Cross out answers"
                    onClick={flipCrossMode}
                  >
                    <CrossOutIcon />
                  </button>
                )}
              </div>

              {q.type === "code" ? (
                <CodeQuestion
                  key={q.id}
                  question={q}
                  value={answers[q.id]}
                  onChange={(v) => setAnswer(q.id, v)}
                  prompt={
                    <>
                      <QuestionImages images={q.images} />
                      <h1 id="question-prompt" ref={promptRef} tabIndex={-1} className="stu-prompt">
                        {q.prompt}
                      </h1>
                    </>
                  }
                />
              ) : (
                <>
                  <QuestionImages images={q.images} />

                  <h1 id="question-prompt" ref={promptRef} tabIndex={-1} className="stu-prompt">
                    {q.prompt}
                  </h1>

                  {q.type === "essay" && <RubricPanel question={q} />}

                  <AnswerInput
                    key={q.id}
                    question={q}
                    value={answers[q.id]}
                    onChange={(v) => setAnswer(q.id, v)}
                    labelledBy="question-prompt"
                    cross={{ on: crossMode, crossed: crossed[q.id] ?? [], onToggle: (i) => toggleCross(q.id, i) }}
                  />
                </>
              )}
            </main>
          </div>

          <nav className="stu-bottombar" aria-label="Questions">
            <div className="stu-bottombar-inner">
              <span className="stu-who" data-testid="student-identity">
                {me && (
                  <>
                    <b>{me.name}</b>
                    <span className="netid">{me.netId}</span>
                  </>
                )}
              </span>

              <button
                ref={navToggleRef}
                type="button"
                className="stu-qtoggle"
                aria-expanded={navOpen}
                aria-controls="question-navigator"
                aria-label={`Question ${current + 1} of ${questions.length}`}
                onClick={() => setNavOpen((o) => !o)}
              >
                Question {current + 1}
                <ChevronIcon className="chev" />
              </button>

              <div className="stu-bottombar-nav">
                <button
                  type="button"
                  className="stu-btn lg"
                  onClick={() => setCurrent((c) => Math.max(0, c - 1))}
                  disabled={current === 0}
                >
                  <ArrowLeftIcon />
                  Back
                </button>
                {isLast ? (
                  <button type="button" className="stu-btn primary lg" onClick={() => setPhase("review")}>
                    Review and submit
                  </button>
                ) : (
                  <button type="button" className="stu-btn primary lg" onClick={() => setCurrent((c) => c + 1)}>
                    Next
                    <ArrowRightIcon />
                  </button>
                )}
              </div>

              {navOpen && (
                <div
                  ref={navPanelRef}
                  id="question-navigator"
                  className="stu-navigator"
                  role="dialog"
                  aria-labelledby="nav-h"
                >
                  <div className="stu-navigator-head">
                    <h2 id="nav-h">{attempt.title}: questions</h2>
                    <button type="button" className="stu-icon-btn" aria-label="Close" onClick={() => closeNav(true)}>
                      <CloseIcon />
                    </button>
                  </div>
                  <div className="stu-legend">
                    <span>
                      <PinIcon size={14} />
                      Current
                    </span>
                    <span>
                      <span className="stu-swatch" aria-hidden="true" />
                      Not yet {unanswered.length}
                    </span>
                    <span>
                      <span className="stu-swatch answered" aria-hidden="true" />
                      Answered {answeredCount}
                    </span>
                    <span>
                      <span className="stu-swatch marked" aria-hidden="true" />
                      Marked {markedCount}
                    </span>
                  </div>
                  <div className="stu-qgrid">
                    {questions.map((qq, i) => {
                      const isAnswered = answers[qq.id] !== undefined;
                      const m = marked.has(qq.id);
                      const isCurrent = i === current;
                      const state = [isAnswered ? "answered" : "not answered", m && "marked for review", isCurrent && "current"]
                        .filter(Boolean)
                        .join(", ");
                      return (
                        <button
                          key={qq.id}
                          type="button"
                          className={`stu-qcell${isAnswered ? " answered" : ""}${m ? " marked" : ""}${isCurrent ? " current" : ""}`}
                          aria-label={`Question ${i + 1}, ${state}`}
                          aria-current={isCurrent ? "step" : undefined}
                          onClick={() => {
                            setCurrent(i);
                            closeNav(false);
                          }}
                        >
                          {isCurrent && (
                            <span className="stu-qcell-pin" aria-hidden="true">
                              <PinIcon size={14} />
                            </span>
                          )}
                          {i + 1}
                        </button>
                      );
                    })}
                  </div>
                  <button type="button" className="stu-btn stu-goreview" onClick={() => setPhase("review")}>
                    Go to review page
                  </button>
                  <p className="stu-offline-note">
                    If Wi-Fi drops, keep going. Answers are stored on this computer and sent when you&apos;re back online.
                  </p>
                </div>
              )}
            </div>
          </nav>
        </>
      )}

      {showReview && (
        <ReviewPanel
          questions={questions}
          answers={answers}
          marked={marked}
          onGoTo={goTo}
          onKeepWorking={() => setPhase("exam")}
          onSubmit={() => void doSubmit(false)}
          submitting={phase === "submitting"}
          error={submitError}
        />
      )}

      {waitPanel && (
        <main className="stu-center" data-testid="wait-for-teacher">
          <div className="stu-card" style={{ gap: 12, maxWidth: 560 }} role="status">
            <h1 style={{ margin: 0, fontSize: 22 }}>Wait for your teacher</h1>
            <p style={{ margin: 0 }}>
              FairPlay was closed during the exam. Raise your hand: your teacher lets you continue from the live room.
            </p>
            <p className="stu-muted" style={{ margin: 0 }}>
              Your answers are saved. The time you wait is given back (up to 15 minutes).
            </p>
          </div>
        </main>
      )}

      {timeUpPanel && !attempt.trial && (
        <TimeUpOffline message={submitError} onRetry={() => void doSubmit(true)} onSeal={sealAnswers} />
      )}
      {timeUpPanel && attempt.trial && (
        <TrialExit keyHex={trialKey} message={submitError} onRetry={() => void doSubmit(true)} />
      )}
      {stopPanel && (
        <TrialExit keyHex={trialKey} message={stopProblem} onRetry={() => void stopPractice()} retrying={stopping} />
      )}

      {phase === "trialEnded" && trialEndedPanel}

      {calcMode && calcMounted && toolsShown && <Calculator mode={calcMode} open={calcOpen} onClose={closeCalc} />}
      {paneShown && pane === "materials" && (
        <ToolPane title="Materials" icon={<BookIcon />} labelId="pane-materials-h" width={paneWidth} onWidth={setPaneWidth} onClose={() => setPane(null)}>
          <MaterialsPaneContent attemptId={materialsAid} />
        </ToolPane>
      )}
      {paneShown && pane === "websites" && tools && (
        <ToolPane title="Websites" icon={<GlobeIcon />} labelId="pane-websites-h" width={paneWidth} onWidth={setPaneWidth} onClose={() => setPane(null)}>
          <WebsitesPaneContent sites={tools.websites} />
        </ToolPane>
      )}

      {phase === "submitting" && !timeUpPanel && phaseBeforeSubmit.current === "exam" && (
        <div className="stu-toast" role="status">
          {submitError ?? "Time is up. Submitting your answers…"}
        </div>
      )}
    </div>
  );
}


/** Audit W-03 / W-02: ask for the start code again so offline answers can be sent. */
function ReconnectCode({ reason, onCode }: { reason: "reload" | "changed"; onCode: (code: string) => void }) {
  const [digits, setDigits] = useState<string[]>(emptyCode);
  const code = digits.join("");
  const complete = code.length === CODE_LENGTH && /^\d+$/.test(code);
  return (
    <form
      className="stu-alert warn fp-offline-note"
      role="region"
      aria-label="Start code to reconnect"
      data-testid="reconnect-code"
      onSubmit={(e) => {
        e.preventDefault();
        if (complete) onCode(code);
      }}
      style={{ display: "flex", flexDirection: "column", gap: 10 }}
    >
      <span>
        {reason === "changed"
          ? "Your teacher changed the start code. Type the new one to send your answers."
          : "Type the start code again to send your answers. They are kept on this computer."}
      </span>
      <StartCodeBoxes idPrefix="reconnect" value={digits} onChange={setDigits} legend="Start code" />
      <div>
        <button type="submit" className="stu-btn primary" disabled={!complete}>
          Send my answers
        </button>
      </div>
    </form>
  );
}
