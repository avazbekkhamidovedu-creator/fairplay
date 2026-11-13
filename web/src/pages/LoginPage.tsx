import { Suspense, lazy, useEffect, useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { ApiError, auth, setToken } from "../api/client";
import { sendPendingDeviceCheck } from "../lib/pendingDeviceCheck";
import { isClassLoginSetup, type ClassLoginSetupResponse, type Role } from "../api/types";
const ClassLoginSetup = lazy(() => import("./ClassLoginSetup"));
import { Icon, LogoMark, Wordmark } from "../components/ui/Icon";
import "../styles/teacher.css";

export interface LoginCopy {
  title: string;
  sub: string;
  hint: string;
  pitch: string;
  points: string[];
  /** shown under the form (the student page's note about the class login) */
  note?: string;
}

const TEACHER_COPY: LoginCopy = {
  title: "Teacher sign in",
  sub: "Create exams, run them live and grade in one place.",
  hint: "teacher / teacher123",
  pitch: "Calm, fair exams on every student laptop.",
  points: ["Full-screen lockdown on every laptop", "Start codes you read aloud in the room", "Grade essays with rubrics, release when ready"],
};

/** Audit S-01 / W-04: the demo passwords exist (and are shown) only on a dev or demo
 *  server, which says so in /api/health. The local demo serves a production build, so a
 *  build-time flag would hide them there too. */
export function useDemoLogins(): boolean {
  const [demo, setDemo] = useState(false);
  useEffect(() => {
    let live = true;
    fetch("/api/health")
      .then((r) => (r.ok ? r.json() : null))
      .then((j: { demo_logins?: unknown } | null) => {
        if (live) setDemo(j?.demo_logins === true);
      })
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, []);
  return demo;
}

export default function LoginPage({ role, copy = TEACHER_COPY }: { role: Role; copy?: LoginCopy }) {
  const navigate = useNavigate();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  /** Bridge v2: a class login's set-up (the setup token lives only here, never in storage). */
  const [setup, setSetup] = useState<ClassLoginSetupResponse | null>(null);
  const demo = useDemoLogins();

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    if (!username.trim() || !password) {
      setError("Enter your username and password.");
      return;
    }
    setBusy(true);
    try {
      const res = await auth.login(username.trim(), password);
      if (isClassLoginSetup(res)) {
        if (role !== "student") {
          setError("This is a class login for students. Use the student sign-in page.");
          return;
        }
        setPassword("");
        setSetup(res);
        return;
      }
      if (res.role !== role) {
        setError(
          role === "teacher"
            ? "This is a student account. Use the student sign-in page."
            : "This is a teacher account. Use the teacher sign-in page.",
        );
        return;
      }
      setToken(role, res.token);
      if (role === "student") await sendPendingDeviceCheck();
      navigate(`/${role}`, { replace: true });
    } catch (err) {
      // Bridge v2: an expired or stopped class login says so (and who to ask).
      if (err instanceof ApiError && err.status === 401 && /class login/i.test(err.detail)) setError(err.detail);
      else if (err instanceof ApiError && err.status === 401) setError("Invalid username or password.");
      else if (err instanceof ApiError) setError(err.detail);
      else setError("Cannot reach the server. Check your connection and try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="t-login">
      <aside className="t-login-side" aria-label="About FairPlay">
        <span className="t-brand">
          <LogoMark />
          <Wordmark />
        </span>
        <h2>{copy.pitch}</h2>
        <ul className="t-login-points">
          {copy.points.map((p) => (
            <li key={p}>
              <Icon name="check" size={18} />
              {p}
            </li>
          ))}
        </ul>
      </aside>
      <main className="t-login-main">
        <div className="t-login-card">
          {setup ? (
            <Suspense fallback={null}>
              <ClassLoginSetup
                setup={setup}
                onExpired={(message) => {
                  setSetup(null);
                  setPassword("");
                  setError(message);
                }}
                onDone={() => navigate(`/${role}`, { replace: true })}
              />
            </Suspense>
          ) : (
            <>
            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              <h1>{copy.title}</h1>
              <p className="t-muted" style={{ margin: 0, fontSize: 15 }}>
                {copy.sub}
              </p>
            </div>
            <form onSubmit={onSubmit} noValidate>
              <div className="t-field">
                <label htmlFor="username">Username</label>
                <input
                  id="username"
                  className="input"
                  autoComplete="username"
                  autoCapitalize="none"
                  autoCorrect="off"
                  spellCheck={false}
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  autoFocus
                />
              </div>
              <div className="t-field">
                <label htmlFor="password">Password</label>
                <input
                  id="password"
                  className="input"
                  type="password"
                  autoComplete="current-password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                />
              </div>
              {error && (
                <div className="alert alert-error" role="alert">
                  {error}
                </div>
              )}
              <button type="submit" className="btn btn-primary btn-lg btn-block" disabled={busy}>
                {busy ? "Signing in…" : "Sign in"}
              </button>
            </form>
            {copy.note && (
              <p className="t-login-switch" style={{ margin: 0 }}>
                {copy.note}
              </p>
            )}
            {demo && (
              <p className="t-demo" style={{ margin: 0 }}>
                Demo account: <span className="mono">{copy.hint}</span>
              </p>
            )}
            </>
          )}
        </div>
      </main>
    </div>
  );
}
