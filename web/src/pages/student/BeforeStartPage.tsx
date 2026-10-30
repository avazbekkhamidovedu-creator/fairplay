import { useCallback, useEffect, useRef, useState } from "react";
import { Navigate, useNavigate, useParams } from "react-router-dom";
import { NetworkError, studentApi } from "../../api/client";
import type { StudentExamSummary } from "../../api/types";
import { NotLockedBanner } from "../../components/LockBanner";
import { AppsToClose, PreflightProblems } from "../../components/student/Preflight";
import { CodeExamStart } from "../../components/student/CodeExamStart";
import { StartCodeForm } from "../../components/student/StartCodeForm";
import { StuLoading } from "../../components/student/StudentLayout";
import { examName, plural, toolsLine } from "../../components/student/format";
import { MaterialsUpload } from "../../components/student/MaterialsUpload";
import { BatteryIcon, LockIcon } from "../../components/student/icons";
import { LogoMark } from "../../components/ui/Icon";
import { batteryTone, useBattery } from "../../lib/battery";
import * as bridge from "../../lib/bridge";
import { canRunInBrowser, isCodeLanguage } from "../../lib/code/languages";
import { warmUpRunners } from "../../lib/code/runners";
import { resumeInfo, welcomeBack } from "../../lib/resumeState";
import "../../styles/student.css";

/** Under this level (and not charging) the page asks the student to plug in first. */
export const START_BATTERY_WARN = 0.3;

type Step = "rules" | "checking" | "preflight" | "closeApps" | "locking" | "lockFailed" | "code" | "leaving";

/**
 * 2026-10-23 "Before you start": the rules, a battery check and "I'm ready", then the
 * computer locks fully and asks for the 6-digit start code. Nothing of the exam is on this
 * computer yet, so "Not ready? Leave and unlock" is allowed until the code is right.
 * The exam page then turns the pre-exam lock into the exam lock without unlocking.
 * 2026-10-23 review: a page opened (or reloaded) while the pre-exam lock is on goes straight
 * to the code; leaving always asks the app to unlock when the computer is locked, even when
 * the lock answer came too late (the iPad answers only once Assessment Mode is on).
 */
export default function BeforeStartPage() {
  const { examId = "" } = useParams();
  const navigate = useNavigate();
  const [exam, setExam] = useState<StudentExamSummary | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [ready, setReady] = useState(false);
  const [step, setStep] = useState<Step>(() => (bridge.getNativeState()?.prelock && bridge.isLocked() ? "code" : "rules"));
  const [problems, setProblems] = useState<string[]>([]);
  const [apps, setApps] = useState<string[]>([]);
  const [closing, setClosing] = useState(false);
  const [lockReason, setLockReason] = useState("");
  const [leaveError, setLeaveError] = useState<string | null>(null);
  const [prelocked, setPrelocked] = useState(() => !!bridge.getNativeState()?.prelock && bridge.isLocked());
  const appsConfirmed = useRef(false);
  // 2026-10-23 review: the student is leaving: a lock answer arriving now (the iPad's
  // "lockFailed: cancelled" for the prompt preunlock cancelled) is not an error to show.
  const leavingRef = useRef(false);
  // 2026-10-23 review: a start request is on its way: leaving (and unlocking) waits for it.
  const [starting, setStarting] = useState(false);
  const startingRef = useRef(false);
  const onStartInFlight = useCallback((on: boolean) => {
    startingRef.current = on;
    setStarting(on);
  }, []);
  const battery = useBattery();

  useEffect(() => {
    let alive = true;
    studentApi.listExams().then(
      (list) => {
        if (!alive) return;
        const e = list.find((x) => x.id === examId) ?? null;
        if (!e) setLoadError("This exam was not found.");
        setExam(e);
      },
      (err) => {
        if (!alive) return;
        // Offline: the start code still opens the exam from its downloaded copy.
        if (!(err instanceof NetworkError)) setLoadError("Could not load this exam. Go back and try again.");
      },
    );
    return () => {
      alive = false;
    };
  }, [examId]);

  // Coding exams: the runtimes (Pyodide, SQLite) start loading now, while the student reads the
  // rules, instead of at the first Run. Needs the exam summary to name its code languages.
  useEffect(() => {
    const langs = (exam as { code_languages?: unknown } | null)?.code_languages;
    if (Array.isArray(langs)) warmUpRunners(langs.filter(isCodeLanguage).filter(canRunInBrowser));
  }, [exam]);

  // The app's lock state while this page is open: a late "locked" (prelock) after the 4 s
  // "unsupported" guess, or a "state" that says the pre-exam lock is on.
  useEffect(
    () =>
      bridge.onNative((msg) => {
        if (msg.type === "locked" && msg.prelock) setPrelocked(true);
        else if (msg.type === "state") {
          const on = !!msg.prelock && !!msg.locked;
          setPrelocked(on);
          if (on) setStep((s) => (s === "rules" ? "code" : s));
        } else if (msg.type === "unlocked") setPrelocked(false);
      }),
    [],
  );

  const lockAndContinue = useCallback(async () => {
    setStep("checking");
    const report = await bridge.preflightReport(3000, []);
    const found = report?.problems ?? [];
    if (found.length > 0) {
      setProblems(found);
      setStep("preflight");
      return;
    }
    if (!appsConfirmed.current && report && report.apps_to_close.length > 0) {
      setApps(report.apps_to_close);
      setStep("closeApps");
      return;
    }
    setStep("locking");
    const res = await bridge.requestPreLock();
    if (leavingRef.current) return;      // leave() decides where to go
    if (res.ok) {
      setPrelocked(true);
      setStep("code");
    } else if (res.reason === "unsupported") {
      // A browser (demo) or an older app: the exam locks right after the code, as before.
      setPrelocked(false);
      setStep("code");
    } else if (res.reason === "cancelled") {
      setStep("rules");                  // the prompt was cancelled: nothing is locked
    } else {
      setLockReason(res.reason);
      setStep("lockFailed");
    }
  }, []);

  const leave = useCallback(async () => {
    if (startingRef.current) return;
    setLeaveError(null);
    leavingRef.current = true;
    setStep("leaving");
    // 2026-10-23 review: in the app, always ask: every app answers "unlocked" even when
    // nothing is locked, and on the iPad it also cancels a pending Assessment Mode prompt.
    const ok = await bridge.preUnlock();
    if (ok) {
      navigate("/student", { replace: true });
    } else {
      leavingRef.current = false;
      setLeaveError("This computer could not unlock by itself. Raise your hand and ask the invigilator.");
      setStep("code");
    }
  }, [navigate]);

  /** Back from the rules / locking / lock-failed screens: in the app, always through
   *  preunlock (see leave). */
  const back = useCallback(() => {
    if (bridge.isNative()) void leave();
    else navigate("/student");
  }, [leave, navigate]);

  if (exam?.attempt?.status === "in_progress")
    return <Navigate to={`/student/exam/${encodeURIComponent(exam.attempt.attempt_id)}`} replace />;

  const name = exam ? examName(exam.course, exam.title) : "Your exam";
  const resume = exam?.access_code ? resumeInfo() : null;
  const tone = battery ? batteryTone(battery) : "ok";
  const lowForStart = !!battery && !battery.charging && battery.level < START_BATTERY_WARN;
  const locked = step === "code" || step === "leaving";

  return (
    <div className="stu stu-exam stu-start">
      <NotLockedBanner />
      <header className="stu-start-bar">
        <LogoMark size={28} title="FairPlay" />
        <span className="stu-start-title">{name}</span>
        {locked && prelocked && (
          <span className="stu-lock-badge">
            <LockIcon />
            Locked
          </span>
        )}
      </header>

      {step === "rules" && (
        <main className="stu-center">
          <section className="stu-card stu-start-card" aria-labelledby="before-h">
            <span className="stu-eyebrow">{resume ? "Welcome back" : "Before you start"}</span>
            <h1 id="before-h">{name}</h1>
            {exam && (
              <p className="stu-muted" style={{ margin: 0 }}>
                {exam.duration_min} minutes{exam.question_count != null ? ` · ${plural(exam.question_count, "question")}` : ""}
                {exam.room ? ` · Room ${exam.room}` : ""}
              </p>
            )}
            {exam && toolsLine(exam.tools) && (
              <p className="stu-start-tools" data-testid="start-tools">
                <b>Allowed in FairPlay:</b> {toolsLine(exam.tools)}
              </p>
            )}
            {loadError && (
              <div className="stu-alert error" role="alert">
                {loadError}
              </div>
            )}
            <ul className="stu-rules">
              <li>
                <b>Your screen locks</b> when you continue. Other apps close until you hand in.
              </li>
              {exam?.access_code ? (
                <li>
                  {resume ? (
                    <>
                      <b>Then resume the exam.</b> {welcomeBack(resume.name)} Your timer keeps running from where it was.
                    </>
                  ) : (
                    <>
                      <b>Then start the exam.</b> You are signed in with your code, so there is no start code to type. The timer starts when you start.
                    </>
                  )}
                </li>
              ) : (
                <li>
                  <b>Then type the start code</b> your teacher reads out. The timer starts only when the code is right.
                </li>
              )}
              <li>
                <b>Don&apos;t close the laptop</b> or switch it off during the exam.
              </li>
              <li>
                <b>If the Wi-Fi drops, keep going.</b> Your answers are saved on this computer and sent when it&apos;s back.
              </li>
              <li>
                <b>Need help?</b> Raise your hand. The invigilator can come to your desk.
              </li>
            </ul>

            {/* 2026-10-23: open book: upload (or check) the materials before locking */}
            {exam?.tools?.open_book && <MaterialsUpload examId={exam.id} compact />}

            {battery && (
              <div
                className={`stu-start-battery ${lowForStart ? "low" : tone}`}
                role={lowForStart ? "alert" : "status"}
                data-testid="start-battery"
              >
                <BatteryIcon level={battery.level} charging={battery.charging} />
                {battery.charging
                  ? `Battery ${Math.round(battery.level * 100)}% · charging`
                  : lowForStart
                    ? `Battery ${Math.round(battery.level * 100)}%. Plug in your charger before you start.`
                    : `Battery ${Math.round(battery.level * 100)}%`}
              </div>
            )}

            <label className="stu-ready">
              <input type="checkbox" checked={ready} onChange={(e) => setReady(e.target.checked)} />
              I understand and I&apos;m ready
            </label>
            <div className="stu-row">
              <button
                type="button"
                className="stu-btn primary lg"
                disabled={!ready || !!loadError}
                onClick={() => void lockAndContinue()}
              >
                Lock and continue
              </button>
              <button type="button" className="stu-btn lg" onClick={back}>
                Back
              </button>
            </div>
          </section>
        </main>
      )}

      {(step === "checking" || step === "locking") && (
        <div className="stu-center" style={{ flexDirection: "column", gap: 16 }}>
          <StuLoading label={step === "checking" ? "Checking this computer…" : "Locking this computer…"} />
          {/* 2026-10-23 review: e.g. the iPad's Assessment Mode prompt is waiting: way back */}
          {step === "locking" && bridge.isNative() && (
            <button type="button" className="stu-linkbtn" onClick={back}>
              Cancel
            </button>
          )}
        </div>
      )}

      {step === "preflight" && <PreflightProblems problems={problems} onRetry={() => lockAndContinue()} />}

      {step === "closeApps" && (
        <AppsToClose
          apps={apps}
          busy={closing}
          onContinue={() => {
            setClosing(true);
            void bridge.quitApps([]).then(() => {
              appsConfirmed.current = true;
              setClosing(false);
              void lockAndContinue();
            });
          }}
        />
      )}

      {step === "lockFailed" && (
        <main className="stu-center">
          <div className="stu-card" style={{ gap: 14, maxWidth: 560 }} role="alert">
            <h1 style={{ margin: 0, fontSize: 22 }}>This computer could not lock</h1>
            <p style={{ margin: 0, color: "var(--s-text-2)" }}>
              {bridge.nativePlatform() === "ipad"
                ? "Tap Yes when iPadOS asks to start Assessment Mode, then try again."
                : lockReason === "accessibility"
                  ? "FairPlay needs the Accessibility permission: System Settings → Privacy & Security → Accessibility → turn on FairPlay."
                  : "Try again. If it keeps failing, raise your hand."}
            </p>
            <div className="stu-row">
              <button type="button" className="stu-btn primary lg" onClick={() => void lockAndContinue()}>
                Try again
              </button>
              <button type="button" className="stu-btn lg" onClick={back}>
                Back
              </button>
            </div>
          </div>
        </main>
      )}

      {locked && (
        <main className="stu-center">
          <section className="stu-card stu-start-card stu-code-card" aria-labelledby="code-h">
            <h1 id="code-h">{exam?.access_code ? (resume ? "Resume the exam" : "Start the exam") : "Enter the start code"}</h1>
            <p className="stu-muted" style={{ margin: 0 }}>
              {exam?.access_code
                ? resume
                  ? welcomeBack(resume.name)
                  : "The exam and the timer start when you press the button."
                : "Your teacher reads it out in the room. The exam and the timer start when the code is right."}
            </p>
            {exam?.access_code ? (
              <CodeExamStart examId={examId} onInFlight={onStartInFlight} paused={step === "leaving"} />
            ) : (
              <StartCodeForm examId={examId} autoFocus centered legend="Start code" onInFlight={onStartInFlight} paused={step === "leaving"} />
            )}
            {leaveError && (
              <div className="stu-alert error" role="alert">
                {leaveError}
              </div>
            )}
            <button type="button" className="stu-linkbtn" onClick={() => void leave()} disabled={step === "leaving" || starting}>
              {step === "leaving" ? "Unlocking…" : prelocked ? "Not ready? Leave and unlock" : "Not ready? Go back"}
            </button>
          </section>
        </main>
      )}
    </div>
  );
}
