// 2026-10-24: "choose a password" fields: show/hide, live rule checklist, strength bar and
// a live "passwords match" line. Rules come from lib/passwordPolicy (same as the server's).
import { useId, useState } from "react";
import { checkPassword, strength, type PasswordContext } from "../lib/passwordPolicy";
import "../styles/password.css";

function EyeIcon({ off }: { off: boolean }) {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
      strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z" />
      <circle cx="12" cy="12" r="3" />
      {off && <path d="M4 4l16 16" />}
    </svg>
  );
}

export function NewPasswordFields({
  idPrefix = "new",
  password,
  password2,
  onPassword,
  onPassword2,
  context,
  label = "Password",
  label2 = "Password again",
}: {
  idPrefix?: string;
  password: string;
  password2: string;
  onPassword: (v: string) => void;
  onPassword2: (v: string) => void;
  context: PasswordContext;
  label?: string;
  label2?: string;
}) {
  const [shown, setShown] = useState(false);
  const [typed2, setTyped2] = useState(false);
  const uid = useId();
  const pid = `${idPrefix}-password`;
  const pid2 = `${idPrefix}-password2`;
  const rulesId = `${uid}-rules`;
  const matchId = `${uid}-match`;
  const check = checkPassword(password, context);
  const st = strength(password, context);
  const passed = check.results.filter((r) => r.ok).length;
  const total = check.results.length;
  const match = password2.length > 0 && password === password2;
  const summary = !password
    ? `Your password needs ${total} things.`
    : check.ok
      ? "All password rules met."
      : `${passed} of ${total} password rules met.`;

  return (
    <>
      <div className="t-field">
        <label htmlFor={pid}>{label}</label>
        <div className="pw-input-wrap">
          <input
            id={pid}
            className="input"
            type={shown ? "text" : "password"}
            autoComplete="new-password"
            autoCapitalize="none"
            autoCorrect="off"
            spellCheck={false}
            aria-describedby={rulesId}
            aria-invalid={password.length > 0 && !check.ok ? true : undefined}
            value={password}
            onChange={(e) => onPassword(e.target.value)}
          />
          <button
            type="button"
            className="pw-toggle"
            aria-pressed={shown}
            aria-label="Show password"
            title={shown ? "Hide password" : "Show password"}
            onClick={() => setShown((s) => !s)}
          >
            <EyeIcon off={shown} />
          </button>
        </div>
        <div className="pw-strength" data-level={st.level} data-testid="pw-strength">
          <div className="pw-strength-bar" aria-hidden="true">
            {[1, 2, 3, 4].map((i) => (
              <span key={i} className={i <= st.level ? "on" : undefined} />
            ))}
          </div>
          <span className="pw-strength-label">{st.level > 0 ? `Strength: ${st.label}` : "Strength: —"}</span>
        </div>
        <div id={rulesId}>
          <ul className="pw-rules" aria-label="Password rules">
            {check.results.map((r) => (
              <li key={r.id} className={r.ok ? "ok" : undefined} data-rule={r.id} data-ok={r.ok ? "true" : "false"}>
                <span className="pw-mark" aria-hidden="true">{r.ok ? "✓" : "✗"}</span>
                <span>
                  {r.label}
                  <span className="sr-only">{r.ok ? " (done)" : " (not yet)"}</span>
                </span>
              </li>
            ))}
          </ul>
          <p className="pw-summary" aria-live="polite" data-testid="pw-summary">
            {summary}
          </p>
        </div>
      </div>
      <div className="t-field">
        <label htmlFor={pid2}>{label2}</label>
        <input
          id={pid2}
          className="input"
          type={shown ? "text" : "password"}
          autoComplete="new-password"
          autoCapitalize="none"
          autoCorrect="off"
          spellCheck={false}
          aria-describedby={typed2 ? matchId : undefined}
          aria-invalid={typed2 && password2.length > 0 && !match ? true : undefined}
          value={password2}
          onChange={(e) => {
            setTyped2(true);
            onPassword2(e.target.value);
          }}
        />
        <p id={matchId} className={`pw-match${match ? " ok" : ""}`} aria-live="polite" data-testid="pw-match">
          {typed2 ? (match ? "Passwords match ✓" : "Passwords don't match yet") : ""}
        </p>
      </div>
    </>
  );
}
