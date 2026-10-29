import { useEffect, useState } from "react";
import { Navigate, useNavigate } from "react-router-dom";
import { LogoMark, Wordmark } from "../../components/ui/Icon";
import { getHomeworkToken, loadHomeworkMe, signOutHomework, type HomeworkIdentity } from "../../lib/homework";
import "../../styles/code-signin.css";

export default function HomeworkPage() {
  const navigate = useNavigate();
  const hasToken = getHomeworkToken() !== null;
  const [me, setMe] = useState<HomeworkIdentity | null>(null);
  const [failed, setFailed] = useState(false);
  const [gone, setGone] = useState(false);

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

  if (!hasToken || gone) return <Navigate to="/student/login" replace />;

  function signOut() {
    signOutHomework();
    navigate("/student/login", { replace: true });
  }

  return (
    <div className="cs-page">
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
        </section>
      </main>
    </div>
  );
}
