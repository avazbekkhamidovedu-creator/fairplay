import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { ApiError, codeLoginApi, setToken } from "../../api/client";
import type { CodeLoginPrompt } from "../../api/types";
import { DeviceCheckRows } from "../../components/student/Readiness";
import { Icon, LogoMark, Wordmark } from "../../components/ui/Icon";
import * as bridge from "../../lib/bridge";
import { keepDeviceCheck, sendPendingDeviceCheck } from "../../lib/pendingDeviceCheck";
import "../../styles/student.css";
import "../../styles/teacher.css";

/** The code as the server reads it: letters and digits, upper case; spaces and dashes dropped. */
export function normalizeCode(raw: string): string {
  return raw.toUpperCase().replace(/[^A-Z0-9]/g, "");
}

export const CODE_CHARS = 6;
export const NOT_VALID = "That code is not valid.";

function errorText(err: unknown): string {
  if (err instanceof ApiError && err.status === 429) {
    return err.retryAfter ? `Too many tries. Wait ${err.retryAfter} seconds and try again.` : "Too many tries. Wait a moment and try again.";
  }
  if (err instanceof ApiError && err.status === 400) return NOT_VALID;
  if (err instanceof ApiError) return err.detail;
  return "Cannot reach the server. Check your connection and try again.";
}

type Check = { kind: "idle" } | { kind: "running" } | { kind: "none" } | { kind: "done"; report: bridge.PreflightReport };

/** First screen of the student page: the student's access code, and a device test that works
 *  before anyone is signed in. */
export default function CodeSignIn({ onUseAccount }: { onUseAccount: () => void }) {
  const navigate = useNavigate();
  const [code, setCode] = useState("");
  const [prompt, setPrompt] = useState<CodeLoginPrompt | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [check, setCheck] = useState<Check>({ kind: "idle" });

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    const typed = normalizeCode(code);
    if (typed.length !== CODE_CHARS) {
      setError(`Your code has ${CODE_CHARS} letters and digits.`);
      return;
    }
    setBusy(true);
    try {
      const ch = await codeLoginApi.challenge();
      const proof = await bridge.attest(ch.nonce);
      setPrompt(await codeLoginApi.enter(typed, proof));
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  async function itsMe() {
    if (!prompt) return;
    setBusy(true);
    setError(null);
    try {
      const res = await codeLoginApi.confirm(prompt.confirm_token);
      setToken("student", res.token);
      await sendPendingDeviceCheck();
      navigate("/student", { replace: true });
    } catch (err) {
      setPrompt(null);
      setCode("");
      setError(err instanceof ApiError && err.status === 400 ? err.detail : errorText(err));
    } finally {
      setBusy(false);
    }
  }

  function notMe() {
    setPrompt(null);
    setCode("");
    setError(null);
  }

  async function testDevice() {
    setCheck({ kind: "running" });
    const report = await bridge.preflightReport();
    keepDeviceCheck(report);
    setCheck(report ? { kind: "done", report } : { kind: "none" });
  }

  return (
    <div className="t-login">
      <aside className="t-login-side" aria-label="About FairPlay">
        <span className="t-brand">
          <LogoMark />
          <Wordmark />
        </span>
        <h2>Your answers are saved as you type.</h2>
        <ul className="t-login-points">
          {["Answers sync even if the Wi-Fi drops", "No account or password needed on exam day", "Test this device before the exam"].map((p) => (
            <li key={p}>
              <Icon name="check" size={18} />
              {p}
            </li>
          ))}
        </ul>
      </aside>
      <main className="t-login-main">
        <div className="t-login-card">
          {prompt ? (
            <>
              <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                <h1>Is this you?</h1>
              </div>
              <p data-testid="code-who" style={{ margin: 0, fontSize: 17 }}>
                You are{" "}
                <b>
                  {prompt.first_name} {prompt.last_name}
                </b>{" "}
                ({prompt.netid}) — Exam: <b>{prompt.exam_title}</b>
              </p>
              {error && (
                <div className="alert alert-error" role="alert">
                  {error}
                </div>
              )}
              <div style={{ display: "flex", gap: 10 }}>
                <button type="button" className="btn btn-primary btn-lg" onClick={() => void itsMe()} disabled={busy}>
                  {busy ? "Signing in…" : "This is me"}
                </button>
                <button type="button" className="btn btn-lg" onClick={notMe} disabled={busy}>
                  Not me
                </button>
              </div>
            </>
          ) : (
            <>
              <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                <h1>Student sign in</h1>
                <p className="t-muted" style={{ margin: 0, fontSize: 15 }}>
                  Type the code from the sheet your professor gave you.
                </p>
              </div>
              <form onSubmit={submit} noValidate>
                <div className="t-field">
                  <label htmlFor="access-code">Your code</label>
                  <input
                    id="access-code"
                    className="input mono"
                    autoComplete="off"
                    autoCapitalize="characters"
                    autoCorrect="off"
                    spellCheck={false}
                    maxLength={16}
                    inputMode="text"
                    value={code}
                    onChange={(e) => setCode(e.target.value.toUpperCase())}
                    autoFocus
                  />
                </div>
                {error && (
                  <div className="alert alert-error" role="alert">
                    {error}
                  </div>
                )}
                <button type="submit" className="btn btn-primary btn-lg btn-block" disabled={busy}>
                  {busy ? "Checking…" : "Continue"}
                </button>
              </form>
              <div className="stu-device-check" data-testid="test-device">
                <button type="button" className="btn btn-lg btn-block" onClick={() => void testDevice()} disabled={check.kind === "running"}>
                  {check.kind === "running" ? "Checking…" : check.kind === "done" ? "Test again" : "Test my device"}
                </button>
                {check.kind === "none" && (
                  <p className="stu-hint" role="status">
                    This version of FairPlay cannot run the full check. The exam still checks before it locks.
                  </p>
                )}
                {check.kind === "done" && <DeviceCheckRows report={check.report} saved={false} pending />}
              </div>
              <p className="t-login-switch" style={{ margin: 0 }}>
                <button type="button" className="stu-linkbtn" onClick={onUseAccount}>
                  Use a username and password instead
                </button>
              </p>
            </>
          )}
        </div>
      </main>
    </div>
  );
}
