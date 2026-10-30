import { useCallback, useEffect, useState } from "react";
import { Navigate, useNavigate } from "react-router-dom";
import { LogoMark, Wordmark } from "../../components/ui/Icon";
import { SessionBar } from "../../components/homework/SessionBar";
import * as bridge from "../../lib/bridge";
import { forgetThisDevice, getHomeworkToken, loadHomeworkMe, rememberThisDevice, signOutHomework, type HomeworkIdentity } from "../../lib/homework";
import "../../styles/code-signin.css";

export default function HomeworkPage() {
  const navigate = useNavigate();
  const hasToken = getHomeworkToken() !== null;
  const [me, setMe] = useState<HomeworkIdentity | null>(null);
  const [failed, setFailed] = useState(false);
  const [gone, setGone] = useState(false);
  const [canRemember, setCanRemember] = useState(() => bridge.rememberAvailable());
  const [savedName, setSavedName] = useState(() => bridge.rememberedName());
  const [justSaved, setJustSaved] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  useEffect(
    () =>
      bridge.onNative((m) => {
        if (m.type === "state") {
          setCanRemember(bridge.rememberAvailable());
          setSavedName(bridge.rememberedName());
        }
      }),
    [],
  );

  useEffect(() => {
    if (!hasToken) return;
    let alive = true;
    void loadHomeworkMe().then((r) => {
      if (!alive) return;
      if (r === "signed-out") setGone(true);
      else if (r === "error") setFailed(true);
      else setMe(r);
    });
    return () => {
      alive = false;
    };
  }, [hasToken]);

  const expired = useCallback(() => setGone(true), []);

  if (!hasToken || gone) return <Navigate to="/student/login" replace />;

  function signOut() {
    signOutHomework();
    navigate("/student/login", { replace: true });
  }

  async function remember() {
    if (!me || saving) return;
    setSaving(true);
    setSaveError(null);
    const r = await rememberThisDevice(me.name);
    setSaving(false);
    if (r.ok) setJustSaved(true);
    else setSaveError(r.message);
  }

  const remembered = justSaved || (me !== null && savedName === me.name);

  async function forget() {
    await forgetThisDevice();
    navigate("/student/login", { replace: true });
  }

  return (
    <div className="cs-page">
      <SessionBar onExpire={expired} />
      <header className="cs-top">
        <span className="cs-brand">
          <LogoMark />
          <Wordmark />
        </span>
      </header>
      <main className="cs-main">
        <section className="cs-card" aria-labelledby="hw-title">
          {me ? (
            <>
              <h1 id="hw-title" className="cs-title">
                Welcome, {me.name}
              </h1>
              <p className="cs-sub">NetID: {me.netid}</p>
            </>
          ) : (
            <h1 id="hw-title" className="cs-title">
              {failed ? "Could not load your account" : "Loading…"}
            </h1>
          )}
          {failed && (
            <p className="cs-error" role="alert">
              Cannot reach the server. Check your connection and reload.
            </p>
          )}
          <p className="cs-sub">Homework is coming soon.</p>
          <button type="button" className="cs-btn cs-btn-secondary cs-block" onClick={signOut}>
            Sign out
          </button>
          {me && canRemember && !remembered && (
            <div className="cs-remember-card" data-testid="remember-device">
              <h2 className="cs-sub">Remember this device</h2>
              <p className="cs-sub">Next time you can sign in with your fingerprint or device password.</p>
              <button type="button" className="cs-btn cs-btn-secondary cs-block" onClick={() => void remember()} disabled={saving}>
                Remember this device
              </button>
              {saveError && (
                <p className="cs-error" role="alert">
                  {saveError}
                </p>
              )}
            </div>
          )}
          {me && canRemember && justSaved && (
            <p className="cs-info" role="status">
              This device is remembered.
            </p>
          )}
          <button type="button" className="cs-link cs-forget" onClick={() => void forget()}>
            Forget this device
          </button>
        </section>
      </main>
    </div>
  );
}
