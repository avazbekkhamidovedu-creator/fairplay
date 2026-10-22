// 2026-10-22: "Try a sample exam". A small card on every student dashboard. Starting goes
// through the same app proof, preflight, apps-to-close step and lock as a real exam (the
// exam page in sample mode); only the class and the start code are not needed.
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ApiError, NetworkError, studentApi } from "../../api/client";
import type { TrialInfo } from "../../api/types";
import { LogoMark } from "../ui/Icon";

export function trialStartError(err: unknown): string {
  if (err instanceof NetworkError) return "Cannot reach the server. Check the connection and try again.";
  if (err instanceof ApiError) {
    if (err.status === 403 && /FairPlay app/i.test(err.detail)) return err.detail;
    if (err.status === 404) return "The sample exam is not available right now.";
    return err.detail;
  }
  return "Something went wrong. Try again.";
}

export function TrialCard() {
  const navigate = useNavigate();
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

  const meta = info ? `${info.duration_min} minutes · ${info.question_count} questions · not graded` : "Not graded";
  return (
    <section className="stu-trial-card" aria-labelledby="trial-h" data-testid="trial-card">
      <LogoMark size={36} />
      <div className="stu-trial-text">
        <h2 id="trial-h">Sample exam</h2>
        <p>See how an exam works: FairPlay locks this computer just like on exam day.</p>
        <span className="stu-trial-meta">{meta}</span>
        {error && (
          <p className="stu-alert error" role="alert" style={{ margin: "6px 0 0" }}>
            {error}
          </p>
        )}
      </div>
      <button
        type="button"
        className="stu-btn primary sm"
        onClick={() => void start()}
        disabled={busy}
        aria-label={running ? "Continue the sample exam" : "Try the sample exam"}
      >
        {busy ? "Starting…" : running ? "Continue" : "Try it"}
      </button>
    </section>
  );
}
