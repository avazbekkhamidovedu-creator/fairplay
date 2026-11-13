import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { ApiError, adminTestApi, setToken } from "../../api/client";
import { clearHomeworkSession } from "../../lib/homeworkSession";
import "../../styles/code-signin.css";

/** Operator preview sign-in. Nothing links here: open /student/admin-test and type the
 *  FAIRPLAY_ADMIN_TEST_CODE. The session can only look at the exam list. */
export default function AdminTestPage() {
  const navigate = useNavigate();
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!code.trim() || busy) return;
    setBusy(true);
    setError(null);
    clearHomeworkSession();
    try {
      const res = await adminTestApi.enter(code);
      setToken("student", res.token);
      navigate("/student", { replace: true });
    } catch (err) {
      if (err instanceof ApiError && err.status === 429) setError("Too many tries. Wait a moment and try again.");
      else if (err instanceof ApiError && err.status === 400) setError("That code is not valid.");
      else setError("Cannot reach the server. Check your connection and try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="cs-page">
      <main className="cs-main">
        <section className="cs-card" aria-labelledby="at-title">
          <h1 id="at-title" className="cs-title cs-title-sm">
            Admin preview
          </h1>
          <form onSubmit={submit} noValidate>
            <label htmlFor="at-code" className="cs-sub">
              Preview code
            </label>
            <input
              id="at-code"
              className="input mono"
              type="password"
              autoComplete="off"
              autoCapitalize="none"
              autoCorrect="off"
              spellCheck={false}
              value={code}
              onChange={(e) => setCode(e.target.value)}
            />
            {error && (
              <p className="cs-error" role="alert">
                {error}
              </p>
            )}
            <button type="submit" className="cs-btn cs-btn-primary cs-block" disabled={!code.trim() || busy}>
              {busy ? "Checking…" : "Continue"}
            </button>
          </form>
        </section>
      </main>
    </div>
  );
}
