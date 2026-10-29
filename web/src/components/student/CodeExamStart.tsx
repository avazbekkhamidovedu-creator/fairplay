import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ApiError, studentApi } from "../../api/client";
import { resumeInfo } from "../../lib/resumeState";
import { startErrorMessage } from "./StartCodeForm";

/**
 * "Start exam" for an exam the student entered with their access code: signing in was the
 * authorisation, so there is no start code to type. The server still refuses until the
 * exam is open.
 */
export function CodeExamStart({
  examId,
  onInFlight,
  onConflict,
  paused,
}: {
  examId: string;
  onInFlight?: (inFlight: boolean) => void;
  onConflict?: () => void;
  paused?: boolean;
}) {
  const navigate = useNavigate();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const resuming = resumeInfo() !== null;
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      onInFlight?.(false);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function start() {
    if (paused) return;
    setBusy(true);
    setError(null);
    onInFlight?.(true);
    try {
      const attempt = await studentApi.start(examId, "");
      onInFlight?.(false);
      if (mounted.current) navigate(`/student/exam/${encodeURIComponent(attempt.attempt_id)}`, { state: { attempt } });
    } catch (err) {
      onInFlight?.(false);
      if (!mounted.current) return;
      setBusy(false);
      setError(startErrorMessage(err));
      if (err instanceof ApiError && err.status === 409) onConflict?.();
    }
  }

  return (
    <div className="stu-code-panel">
      {error && (
        <div className="stu-alert error" role="alert">
          {error}
        </div>
      )}
      <div className="stu-row">
        <button type="button" className="stu-btn primary lg" style={{ flex: "1 1 160px" }} onClick={() => void start()} disabled={busy || !!paused}>
          {busy ? (resuming ? "Resuming…" : "Starting…") : resuming ? "Resume exam" : "Start exam"}
        </button>
      </div>
    </div>
  );
}
