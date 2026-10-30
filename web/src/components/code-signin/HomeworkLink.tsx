import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import * as bridge from "../../lib/bridge";
import { MSG_AGAIN, continueAsRemembered, loadHomeworkInfo, runHomeworkLogin, type HomeworkInfo } from "../../lib/homework";
import { MSG_SIGNED_OUT, peekSignedOutNotice, takeSignedOutNotice } from "../../lib/homeworkSession";

export function SignedOutNotice() {
  const [show] = useState(() => peekSignedOutNotice());
  useEffect(() => {
    takeSignedOutNotice();
  }, []);
  if (!show) return null;
  return (
    <p className="cs-info" role="status" data-testid="homework-signed-out">
      {MSG_SIGNED_OUT}
    </p>
  );
}

export function HomeworkLink() {
  const navigate = useNavigate();
  const [info, setInfo] = useState<HomeworkInfo | null>(null);
  const [supported, setSupported] = useState(() => bridge.homeworkLoginSupported());
  const [busy, setBusy] = useState(false);
  const [canRemember, setCanRemember] = useState(() => bridge.rememberAvailable());
  const [saved, setSaved] = useState(() => bridge.rememberedName());
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    void loadHomeworkInfo().then((h) => alive && setInfo(h));
    const off = bridge.onNative((m) => {
      if (m.type === "state") {
        setSupported(bridge.homeworkLoginSupported());
        setCanRemember(bridge.rememberAvailable());
        setSaved(bridge.rememberedName());
      }
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

  async function resume() {
    if (busy) return;
    setBusy(true);
    setError(null);
    const r = await continueAsRemembered();
    setBusy(false);
    if (r.ok) navigate("/student/homework", { replace: true });
    else if (r.message) {
      setError(r.message);
      if (r.message === MSG_AGAIN) setSaved(null);
    }
  }

  function otherAccount() {
    bridge.rememberForget();
    setSaved(null);
    setError(null);
  }

  return (
    <div className="cs-homework" data-testid="homework-login">
      {canRemember && saved && (
        <div className="cs-remembered">
          <button type="button" className="cs-btn cs-btn-secondary cs-block" onClick={() => void resume()} disabled={busy}>
            Continue as {saved}
          </button>
          <button type="button" className="cs-link" onClick={otherAccount} disabled={busy}>
            Use a different account
          </button>
        </div>
      )}
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
