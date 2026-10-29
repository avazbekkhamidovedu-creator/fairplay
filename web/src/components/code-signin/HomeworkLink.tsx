import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import * as bridge from "../../lib/bridge";
import { loadHomeworkInfo, runHomeworkLogin, type HomeworkInfo } from "../../lib/homework";

export function HomeworkLink() {
  const navigate = useNavigate();
  const [info, setInfo] = useState<HomeworkInfo | null>(null);
  const [supported, setSupported] = useState(() => bridge.homeworkLoginSupported());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    void loadHomeworkInfo().then((h) => alive && setInfo(h));
    const off = bridge.onNative((m) => {
      if (m.type === "state") setSupported(bridge.homeworkLoginSupported());
    });
    return () => {
      alive = false;
      off();
    };
  }, []);

  if (!info || !supported) return null;

  async function go() {
    if (busy) return;
    setBusy(true);
    setError(null);
    const r = await runHomeworkLogin();
    setBusy(false);
    if (r.ok) navigate("/student/homework", { replace: true });
    else if (r.message) setError(r.message);
  }

  return (
    <div className="cs-homework" data-testid="homework-login">
      <span>Homework?</span>{" "}
      <button type="button" className="cs-link" onClick={() => void go()} disabled={busy}>
        {busy ? "Waiting for the sign-in window…" : `Log in with ${info.label}`}
      </button>
      {error && (
        <p className="cs-error" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}
