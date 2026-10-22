// 2026-10-22: "Test my device" (like Bluebook's): one card in the top-right corner of every
// student dashboard. It shows the device checks and offers five sample questions. Starting
// goes through the same app proof, preflight, apps-to-close step and lock as a real exam (the
// exam page in sample mode); only the class and the start code are not needed. "Stop" in the
// exam bar ends it at any time.
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ApiError, NetworkError, studentApi } from "../../api/client";
import type { TrialInfo } from "../../api/types";
import { DeviceStatus, UpdateNotice, useReadiness } from "./Readiness";

export function trialStartError(err: unknown): string {
  if (err instanceof NetworkError) return "Cannot reach the server. Check the connection and try again.";
  if (err instanceof ApiError) {
    if (err.status === 403 && /FairPlay app/i.test(err.detail)) return err.detail;
    if (err.status === 404) return "The sample questions are not available right now.";
    return err.detail;
  }
  return "Something went wrong. Try again.";
}

function DeviceIcon() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
      strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <rect x="4" y="5" width="16" height="11" rx="2" />
      <path d="M2 19h20" />
    </svg>
  );
}

export function TrialCard() {
  const navigate = useNavigate();
  const readiness = useReadiness();
  const [info, setInfo] = useState<TrialInfo | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    studentApi.trialInfo().then(
      (i) => alive && setInfo(i),
      () => undefined, // the card works without the details
    );
    return () => {
      alive = false;
    };
  }, []);

  const running = info?.attempt?.status === "in_progress";
  const count = info?.question_count ?? 5;

  async function start() {
    setBusy(true);
    setError(null);
    try {
      const attempt = await studentApi.startTrial();
      navigate(`/student/exam/${encodeURIComponent(attempt.attempt_id)}`, { state: { attempt } });
    } catch (err) {
      setBusy(false);
      setError(trialStartError(err));
    }
  }

  return (
    <section className="stu-trial-card" aria-labelledby="trial-h" data-testid="trial-card">
      <h2 id="trial-h" className="stu-trial-title">
        <DeviceIcon />
        Test my device
      </h2>
      {readiness.update && <UpdateNotice update={readiness.update} />}
      <DeviceStatus readiness={readiness} />
      <button
        type="button"
        className="stu-btn primary stu-trial-try"
        onClick={() => void start()}
        disabled={busy}
      >
        {busy ? "Starting…" : running ? "Continue sample questions" : `Try ${count} sample questions`}
      </button>
      <p className="stu-trial-meta">Locks like a real exam. Stop any time. Not graded.</p>
      {error && (
        <p className="stu-alert error" role="alert" style={{ margin: 0 }}>
          {error}
        </p>
      )}
    </section>
  );
}
