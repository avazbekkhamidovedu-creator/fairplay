import { useEffect, useRef, useState, type FormEvent } from "react";
import { ApiError, setToken, setupApi } from "../api/client";
import type { ClassLoginSetupResponse } from "../api/types";
import { NewPasswordFields } from "../components/NewPasswordFields";
import { checkPassword } from "../lib/passwordPolicy";

/** Bridge v2 §4: what the sign-in page shows when the set-up must start over. */
export const SETUP_EXPIRED = "Your class login session expired. Sign in again.";
const OFFLINE = "Cannot reach the server. Check your connection and try again.";
const ROSTER_DOWN = "Can't check the class list right now, try again in a minute.";
const TAKEN = "That username is taken";

const USERNAME_RE = /^[a-z0-9._-]{3,32}$/;

/** Client-side checks matching the server's (§4 step 3). null = fine. */
export function checkNewLogin(username: string, password: string, password2: string, netid: string): string | null {
  if (!USERNAME_RE.test(username)) {
    return "Choose a username of 3 to 32 characters: letters a-z, digits, dots, dashes or underscores.";
  }
  // 2026-10-24: the strong-password rules (lib/passwordPolicy, same as the server's)
  const pw = checkPassword(password, { username, netid });
  if (!pw.ok) return pw.message;
  if (password !== password2) return "The two passwords don't match.";
  return null;
}

function tidy(s: string): string {
  return s.trim().replace(/\s+/g, " ");
}

type Identity = { token: string; name: string; netid: string };

/**
 * First sign-in with a temporary class login: who are you (checked against the class list),
 * then choose your own login. The setup and identity tokens live only in this component's
 * state; only the final session token is stored, exactly like a normal student sign-in.
 */
export default function ClassLoginSetup({
  setup,
  onExpired,
  onDone,
}: {
  setup: ClassLoginSetupResponse;
  /** Back to the sign-in form with this message. */
  onExpired: (message: string) => void;
  /** "Go to my exams". */
  onDone: () => void;
}) {
  const [step, setStep] = useState<1 | 2 | 3>(1);
  const [first, setFirst] = useState("");
  const [last, setLast] = useState("");
  const [netid, setNetid] = useState("");
  const [identity, setIdentity] = useState<Identity | null>(null);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [password2, setPassword2] = useState("");
  const [doneAs, setDoneAs] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const heading = useRef<HTMLHeadingElement>(null);

  useEffect(() => {
    heading.current?.focus();
  }, [step]);

  async function onIdentify(e: FormEvent) {
    e.preventDefault();
    if (busy) return;
    setError(null);
    const f = tidy(first);
    const l = tidy(last);
    const n = netid.trim();
    if (!f || !l || !n) {
      setError("Enter your first name, last name and NetID.");
      return;
    }
    setBusy(true);
    try {
      const res = await setupApi.identify(setup.setup_token, { first_name: f, last_name: l, netid: n });
      setIdentity({ token: res.identity_token, name: res.name, netid: res.netid });
      setStep(2);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) onExpired(SETUP_EXPIRED);
      else if (err instanceof ApiError && err.status === 503) setError(ROSTER_DOWN);
      else if (err instanceof ApiError) setError(err.detail);
      else setError(OFFLINE);
    } finally {
      setBusy(false);
    }
  }

  async function onAccount(e: FormEvent) {
    e.preventDefault();
    if (busy || !identity) return;
    setError(null);
    const u = username.trim().toLowerCase();
    const problem = checkNewLogin(u, password, password2, identity.netid);
    if (problem) {
      setError(problem);
      return;
    }
    setBusy(true);
    try {
      const res = await setupApi.account(identity.token, { username: u, password, password2 });
      setToken("student", res.token);
      setIdentity(null);
      setPassword("");
      setPassword2("");
      setDoneAs(u);
      setStep(3);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) onExpired(SETUP_EXPIRED);
      else if (err instanceof ApiError && err.status === 409) setError(TAKEN);
      else if (err instanceof ApiError && err.status === 503) setError("The server is busy. Try again in a minute.");
      else if (err instanceof ApiError) setError(err.detail);
      else setError(OFFLINE);
    } finally {
      setBusy(false);
    }
  }

  const passwordReady =
    checkPassword(password, { username: username.trim().toLowerCase(), netid: identity?.netid ?? "" }).ok &&
    password === password2;
  const usernameTyped = username.trim().toLowerCase();
  const usernameBad = usernameTyped.length > 0 && !USERNAME_RE.test(usernameTyped);
  const classLine = [setup.class.name, setup.class.course].filter(Boolean).join(" · ");
  const alert = error && (
    <div className="alert alert-error" role="alert">
      {error}
    </div>
  );
  const forgot = (
    <p className="t-login-switch" style={{ margin: 0 }}>
      Forgot your login? Ask your professor to reset it.
    </p>
  );

  if (step === 3) {
    return (
      <>
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          <h1 ref={heading} tabIndex={-1}>
            You're set up, {tidy(first)}.
          </h1>
          <p className="t-muted" style={{ margin: 0, fontSize: 15 }}>
            Next time sign in as <span className="mono">{doneAs}</span>.
          </p>
        </div>
        <button type="button" className="btn btn-primary btn-lg btn-block" onClick={onDone} autoFocus>
          Go to my exams
        </button>
      </>
    );
  }

  return (
    <>
      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        {classLine && (
          <p className="t-muted" style={{ margin: 0, fontSize: 14 }}>
            {classLine}
          </p>
        )}
        <p className="t-muted" style={{ margin: 0, fontSize: 13, fontWeight: 600 }}>
          Step {step} of 2
        </p>
        <h1 ref={heading} tabIndex={-1}>
          {step === 1 ? "Who are you?" : "Choose your own login"}
        </h1>
        {step === 2 && identity && (
          <p className="t-muted" style={{ margin: 0, fontSize: 15 }}>
            {identity.name} ({identity.netid})
          </p>
        )}
      </div>
      {step === 1 ? (
        <form onSubmit={onIdentify} noValidate aria-label="Who are you?">
          <div className="t-field">
            <label htmlFor="setup-first">First name</label>
            <input id="setup-first" className="input" autoComplete="given-name" value={first}
              onChange={(e) => setFirst(e.target.value)} />
          </div>
          <div className="t-field">
            <label htmlFor="setup-last">Last name</label>
            <input id="setup-last" className="input" autoComplete="family-name" value={last}
              onChange={(e) => setLast(e.target.value)} />
          </div>
          <div className="t-field">
            <label htmlFor="setup-netid">NetID</label>
            <input id="setup-netid" className="input" autoComplete="off" autoCapitalize="none" spellCheck={false}
              value={netid} onChange={(e) => setNetid(e.target.value)} />
          </div>
          {alert}
          <button type="submit" className="btn btn-primary btn-lg btn-block" disabled={busy}>
            {busy ? "Checking…" : "Continue"}
          </button>
        </form>
      ) : (
        <form onSubmit={onAccount} noValidate aria-label="Choose your own login">
          <div className="t-field">
            <label htmlFor="setup-username">Username</label>
            <input id="setup-username" className="input" autoComplete="username" autoCapitalize="none"
              spellCheck={false} value={username} onChange={(e) => setUsername(e.target.value)}
              aria-describedby="setup-username-hint" aria-invalid={usernameBad || undefined} />
            <p id="setup-username-hint" className={`pw-summary${usernameBad ? " pw-bad" : ""}`}>
              3 to 32 of a-z, 0-9, dot, dash, underscore.
            </p>
          </div>
          <NewPasswordFields
            idPrefix="setup"
            password={password}
            password2={password2}
            onPassword={setPassword}
            onPassword2={setPassword2}
            context={{ username: username.trim().toLowerCase(), netid: identity?.netid ?? "" }}
          />
          {alert}
          <button type="submit" className="btn btn-primary btn-lg btn-block" disabled={busy || !passwordReady}>
            {busy ? "Setting up…" : "Finish set-up"}
          </button>
        </form>
      )}
      {forgot}
    </>
  );
}
