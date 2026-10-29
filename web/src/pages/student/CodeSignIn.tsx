import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { ApiError, codeLoginApi, setToken } from "../../api/client";
import type { CodeLoginPrompt } from "../../api/types";
import { CODE_LENGTH, CodeBoxes } from "../../components/code-signin/CodeBoxes";
import { DeviceCheckDialog, type DeviceCheckState } from "../../components/code-signin/DeviceCheckDialog";
import { TryExamScreenDialog } from "../../components/code-signin/TryExamScreenDialog";
import { UpdateCard, useSignInUpdate } from "../../components/code-signin/UpdatePrompt";
import { AppVersion } from "../../components/AppVersion";
import { LogoMark, Wordmark } from "../../components/ui/Icon";
import * as bridge from "../../lib/bridge";
import { keepDeviceCheck, sendPendingDeviceCheck } from "../../lib/pendingDeviceCheck";
import { useLegacyStudentUi } from "../../lib/legacyStudentUi";
import "../../styles/code-signin.css";

/** The code as the server reads it: letters and digits, upper case; spaces and dashes dropped. */
export function normalizeCode(raw: string): string {
  return raw.toUpperCase().replace(/[^A-Z0-9]/g, "");
}

export const CODE_CHARS = CODE_LENGTH;
export const NOT_VALID = "That code is not valid.";

function errorText(err: unknown): string {
  if (err instanceof ApiError && err.status === 429) {
    return err.retryAfter ? `Too many tries. Wait ${err.retryAfter} seconds and try again.` : "Too many tries. Wait a moment and try again.";
  }
  if (err instanceof ApiError && err.status === 400) return NOT_VALID;
  if (err instanceof ApiError) return err.detail;
  return "Cannot reach the server. Check your connection and try again.";
}

/** First screen of the student page: the student's access code, and a device test that works
 *  before anyone is signed in. */
export default function CodeSignIn({ onUseAccount }: { onUseAccount: () => void }) {
  const navigate = useNavigate();
  const legacy = useLegacyStudentUi();
  const [chars, setChars] = useState<string[]>(() => Array(CODE_CHARS).fill(""));
  const [typedCode, setTypedCode] = useState("");
  const [prompt, setPrompt] = useState<CodeLoginPrompt | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [shake, setShake] = useState(0);
  const [busy, setBusy] = useState(false);
  const [check, setCheck] = useState<DeviceCheckState>({ kind: "idle" });
  const [panel, setPanel] = useState(false);
  const [tryOpen, setTry] = useState(false);
  const update = useSignInUpdate();
  const installing = update?.phase.kind === "installing";

  const code = chars.join("");
  const complete = code.length === CODE_CHARS;

  function fail(text: string) {
    setError(text);
    if (text === NOT_VALID) setShake((n) => n + 1);
  }

  function clearCode() {
    setChars(Array(CODE_CHARS).fill(""));
    setTypedCode("");
  }

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!complete || busy) return;
    setError(null);
    const typed = normalizeCode(code);
    setBusy(true);
    try {
      const ch = await codeLoginApi.challenge();
      const proof = await bridge.attest(ch.nonce);
      const res = await codeLoginApi.enter(typed, proof);
      setTypedCode(typed);
      setPrompt(res);
    } catch (err) {
      fail(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  async function confirm() {
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
      clearCode();
      setError(err instanceof ApiError && err.status === 400 ? err.detail : errorText(err));
    } finally {
      setBusy(false);
    }
  }

  function notMe() {
    setPrompt(null);
    clearCode();
    setError(null);
  }

  async function runCheck() {
    setCheck({ kind: "running" });
    const report = await bridge.preflightReport();
    keepDeviceCheck(report);
    setCheck(report ? { kind: "done", report } : { kind: "none" });
  }

  function openPanel() {
    if (bridge.getNativeState()?.deviceTest === true) {
      setTry(true);
      return;
    }
    setPanel(true);
    if (check.kind === "idle" || check.kind === "none") void runCheck();
  }

  return (
    <div className="cs-page">
      <header className="cs-top">
        <span className="cs-brand-col">
          <span className="cs-brand">
            <LogoMark />
            <Wordmark />
          </span>
          <AppVersion short className="cs-version" />
        </span>
        <button type="button" className="cs-test-btn" onClick={openPanel}>
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <rect x="4.5" y="5" width="15" height="10.5" rx="1.5" />
            <path d="M2.5 18.5h19" />
          </svg>
          Test my device
        </button>
      </header>
      <main className="cs-main">
        {prompt ? (
          <section className="cs-card" aria-labelledby="cs-title">
            <h1 id="cs-title" className="cs-title cs-title-sm">
              {prompt.netid ? "Is this your slip?" : "Admin tester"}
            </h1>
            {prompt.netid ? (
              <dl className="cs-slip" data-testid="code-who">
                <dt>Last name</dt>
                <dd>{prompt.last_name}</dd>
                <dt>First name</dt>
                <dd>{prompt.first_name}</dd>
                <dt>NetID</dt>
                <dd>{prompt.netid}</dd>
                <dt>Access code</dt>
                <dd className="cs-slip-code">{typedCode}</dd>
              </dl>
            ) : (
              <p className="cs-sub" data-testid="code-admin">
                You can look at every exam. Nothing can be started.
              </p>
            )}
            <p className="cs-exam">Exam: {prompt.exam_title}</p>
            {error && (
              <p className="cs-error" role="alert">
                {error}
              </p>
            )}
            <div className="cs-actions">
              <button type="button" className="cs-btn cs-btn-primary" onClick={() => void confirm()} disabled={busy}>
                {busy ? "Signing in…" : prompt.netid ? "Confirm and continue" : "Continue"}
              </button>
              <button type="button" className="cs-btn cs-btn-secondary" onClick={notMe} disabled={busy}>
                Not me
              </button>
            </div>
          </section>
        ) : (
          <section className="cs-card" aria-labelledby="cs-title">
            {update && !update.hidden && <UpdateCard update={update} countdown />}
            <h1 id="cs-title" className="cs-title">
              Enter your exam code
            </h1>
            <p className="cs-sub">The 6-character code on the sheet your professor gave you.</p>
            <form onSubmit={submit} noValidate>
              <CodeBoxes
                chars={chars}
                onChange={(next) => {
                  setChars(next);
                  if (error) setError(null);
                }}
                invalid={error === NOT_VALID}
                shakeKey={shake}
                disabled={busy || installing}
                describedBy={error ? "cs-error" : undefined}
              />
              {error && (
                <p id="cs-error" className="cs-error" role="alert">
                  {error}
                </p>
              )}
              <button type="submit" className="cs-btn cs-btn-primary cs-block" disabled={!complete || busy || installing}>
                {busy ? "Checking…" : "Continue"}
              </button>
            </form>
            {legacy && (
              <p className="cs-legacy">
                <button type="button" className="cs-link" onClick={onUseAccount}>
                  Use a username and password instead
                </button>
              </p>
            )}
          </section>
        )}
      </main>
      {tryOpen && <TryExamScreenDialog onClose={() => setTry(false)} />}
      {panel && <DeviceCheckDialog state={check} onAgain={() => void runCheck()} onClose={() => setPanel(false)} update={update} />}
    </div>
  );
}
