import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { clearToken, getToken, tokenScope } from "../../api/client";

export const SIGN_OUT_AFTER_MS = 5 * 60 * 1000;

const clock = (s: number) => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;

/** After the hand-in of a code sign-in: counts down 5 minutes, then ends the session and every
 *  local copy of it and returns to the code screen. Nothing for a normal sign-in. */
export function HandInSignOut() {
  const navigate = useNavigate();
  const scoped = !!tokenScope(getToken("student"));
  const [left, setLeft] = useState(SIGN_OUT_AFTER_MS / 1000);

  useEffect(() => {
    if (!scoped) return;
    const end = Date.now() + SIGN_OUT_AFTER_MS;
    const t = setInterval(() => setLeft(Math.max(0, Math.ceil((end - Date.now()) / 1000))), 1000);
    return () => clearInterval(t);
  }, [scoped]);

  const out = () => {
    clearToken("student");
    navigate("/student/login", { replace: true });
  };

  useEffect(() => {
    if (scoped && left === 0) out();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scoped, left]);

  if (!scoped) return null;
  return (
    <div className="stu-signout-timer" data-testid="signout-timer">
      <p role="timer" aria-live="off" style={{ margin: 0 }}>
        You will be signed out in <b className="stu-tabnum">{clock(left)}</b>
      </p>
      <button type="button" className="stu-btn" onClick={out}>
        Sign out now
      </button>
    </div>
  );
}
