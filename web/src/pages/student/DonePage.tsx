import { useEffect, useState } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";
import * as bridge from "../../lib/bridge";
import { studentApi } from "../../api/client";
import { clockTime, shortDate } from "../../components/student/format";
import { CheckIcon } from "../../components/student/icons";
import { HandInSignOut } from "../../components/student/HandInSignOut";
import { useStudentGradesOn } from "../../lib/features";
import { StudentLayout } from "../../components/student/StudentLayout";
import { TrialExit } from "../../components/student/TrialExit";
import { readTrialKey, writeTrialKey } from "../../lib/trialKey";
import type { DoneState } from "./ExamTakingPage";

export default function DonePage() {
  const location = useLocation();
  const fromState = location.state as DoneState | null;
  if (fromState?.trial) return <TrialDone how={fromState.trial} />;
  return <ExamDone fromState={fromState} />;
}

/** 2026-10-22: the app answered "unlockPending" (it could not confirm the signed hand-in yet):
 *  the reason, or null while none came. Cleared again by "unlocked". */
function useUnlockPending(): string | null {
  const [pending, setPending] = useState<string | null>(null);
  useEffect(
    () =>
      bridge.onNative((msg) => {
        if (msg.type === "unlockPending") setPending(msg.reason || "not_submitted");
        else if (msg.type === "unlocked") setPending(null);
      }),
    [],
  );
  return pending;
}

/** 2026-10-22: the sample exam ended. This page is allowed while locked; it goes to the
 *  dashboard only once the app has unlocked (the app checks the signed hand-in first). When the
 *  app says it cannot confirm yet, the student sees why and the way out (offline code), instead
 *  of "unlocking…" forever. */
function TrialDone({ how }: { how: "stopped" | "finished" }) {
  const { attemptId = "" } = useParams();
  const navigate = useNavigate();
  const pending = useUnlockPending();
  useEffect(() => {
    const home = () => {
      writeTrialKey(attemptId, null);
      navigate("/student", { replace: true, state: { trialDone: how } });
    };
    if (!bridge.isNative() || !bridge.isLocked()) {
      home();
      return;
    }
    const off = bridge.onNative((msg) => {
      if (msg.type === "unlocked" || (msg.type === "state" && !bridge.isLocked())) home();
    });
    return off;
  }, [attemptId, how, navigate]);
  if (pending === "offline")
    return (
      <div className="stu stu-exam">
        <TrialExit
          keyHex={readTrialKey(attemptId)}
          message="FairPlay cannot reach the server to confirm that the sample questions ended."
          onRetry={() => bridge.unlock()}
        />
      </div>
    );
  return (
    <StudentLayout>
      <main className="stu-main">
        <section className="stu-done" aria-labelledby="trial-done-h" role="status">
          <h1 id="trial-done-h">{how === "stopped" ? "Sample questions stopped" : "Sample questions finished"}</h1>
          <p className="stu-done-text">FairPlay is unlocking this computer…</p>
          {pending && <StillLocked />}
        </section>
      </main>
    </StudentLayout>
  );
}

/** Shown while the app waits for the server's signed confirmation: what to do if it stays locked. */
function StillLocked() {
  return (
    <p className="stu-alert warn" role="status" style={{ margin: 0 }} data-testid="still-locked">
      FairPlay is waiting for the server to confirm. If this computer stays locked, ask the invigilator to
      press {bridge.exitShortcut()} and end the exam.
    </p>
  );
}

function ExamDone({ fromState }: { fromState: DoneState | null }) {
  const { attemptId = "" } = useParams();
  const [info, setInfo] = useState<DoneState | null>(fromState?.receipt ? fromState : null);
  const [missing, setMissing] = useState(false);
  const pending = useUnlockPending();
  const gradesOn = useStudentGradesOn();

  useEffect(() => {
    if (info) return;
    // Opened directly (e.g. after reload): find the receipt in the exam list.
    studentApi.listExams().then(
      (list) => {
        const exam = list.find((e) => e.attempt?.attempt_id === attemptId);
        if (exam?.attempt?.receipt) {
          setInfo({ receipt: exam.attempt.receipt, submitted_at: exam.attempt.submitted_at ?? "", title: exam.title });
        } else setMissing(true);
      },
      () => setMissing(true),
    );
  }, [attemptId, info]);

  const at = clockTime(info?.submitted_at);
  const day = shortDate(info?.submitted_at);
  let reached = "Your exam reached the server.";
  if (info && at) {
    if (info.total !== undefined && info.answered !== undefined) {
      reached =
        info.answered === info.total
          ? `All ${info.total} answers reached the server at ${at}.`
          : `Your exam (${info.answered} of ${info.total} questions answered) reached the server at ${at}.`;
    } else reached = `Your exam reached the server ${day ? `on ${day} ` : ""}at ${at}.`;
  }

  return (
    <StudentLayout>
      <main className="stu-main">
        <section className="stu-done" aria-labelledby="done-h">
          <span className="stu-done-check" aria-hidden="true">
            <CheckIcon size={28} color="currentColor" />
          </span>
          <h1 id="done-h">Submitted</h1>
          {info?.title && <p className="stu-done-title">{info.title}</p>}
          {fromState?.notice && (
            <p className="stu-alert warn" role="status" style={{ margin: 0 }}>
              {fromState.notice}
            </p>
          )}
          <p className="stu-done-text">{reached}</p>
          <div className="stu-receipt">
            <span>Your receipt code</span>
            <span className="code" data-testid="receipt">
              {info?.receipt ?? (missing ? "—" : "…")}
            </span>
          </div>
          {missing && <p className="stu-muted stu-small">The receipt could not be loaded. It is also shown under Exams, Past.</p>}
          <p className="stu-done-foot">Your laptop unlocks now.{gradesOn && " Grades appear in Grades when your teacher releases them."}</p>
          {pending && bridge.isLocked() && <StillLocked />}
          <HandInSignOut />
          <Link to="/student" className="stu-btn" style={{ marginTop: 12 }}>
            Back to home
          </Link>
        </section>
      </main>
    </StudentLayout>
  );
}
