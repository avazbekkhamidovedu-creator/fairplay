import { useEffect, useState, type ReactNode } from "react";
import { Link, NavLink, useNavigate } from "react-router-dom";
import { clearToken } from "../../api/client";
import * as bridge from "../../lib/bridge";
import { clearExamDataOnSignOut } from "../../lib/offline";
import { useFeatures } from "../../lib/features";
import { useMe } from "../../lib/useMe";
import { LogoMark, Wordmark } from "../ui/Icon";
import "../../styles/student.css";

export function StudentLogo({ to = "/student" }: { to?: string }) {
  return (
    <Link to={to} className="stu-logo" aria-label="FairPlay home">
      <LogoMark size={32} />
      <Wordmark />
    </Link>
  );
}

/** True while the FairPlay app holds this computer locked (updates with the app's messages). */
function useAppLocked(): boolean {
  const [locked, setLocked] = useState(bridge.isLocked());
  useEffect(
    () =>
      bridge.onNative((msg) => {
        if (msg.type === "state" || msg.type === "locked" || msg.type === "unlocked" || msg.type === "lockFailed")
          setLocked(bridge.isLocked());
      }),
    [],
  );
  return locked;
}

/** Top-nav layout for every student page except the exam itself. */
export function StudentLayout({ children, white }: { children: ReactNode; white?: boolean }) {
  const navigate = useNavigate();
  // 2026-10-22: while the app is locked (the done page waiting for the unlock) there is nowhere to
  // go: no links, no Sign out (the apps refuse to leave the exam pages while locked anyway).
  const locked = useAppLocked();
  const me = useMe("student");
  const features = useFeatures();
  const initial = me?.name?.trim().charAt(0).toUpperCase() || "?";
  const signOut = () => {
    clearExamDataOnSignOut();   // audit W-03: exam copies, packages and sessions leave this computer
    clearToken("student");
    navigate("/student/login");
  };
  return (
    <div className={`stu${white ? " stu-white" : ""}`}>
      <header className="stu-topnav">
        {locked ? (
          <span className="stu-logo" aria-label="FairPlay">
            <LogoMark size={32} />
            <Wordmark />
          </span>
        ) : (
          <StudentLogo />
        )}
        {!locked && (
          <>
            <nav aria-label="Main" className="stu-nav">
              <NavLink to="/student" end>
                Home
              </NavLink>
              <NavLink to="/student/exams">Exams</NavLink>
              <NavLink to="/student/grades">Grades</NavLink>
              {features.has("practice") && <NavLink to="/student/practice">Practice</NavLink>}
            </nav>
            <div className="stu-user">
              <span className="stu-avatar" title={me?.name} aria-label={me ? `Signed in as ${me.name}` : "Signed in"} role="img">
                {initial}
              </span>
              <button type="button" className="stu-btn quiet" onClick={signOut}>
                Sign out
              </button>
            </div>
          </>
        )}
      </header>
      {children}
    </div>
  );
}

export function StuLoading({ label = "Loading…" }: { label?: string }) {
  return (
    <div className="stu-loading" role="status" aria-live="polite">
      <span className="stu-spin" aria-hidden="true" />
      <span>{label}</span>
    </div>
  );
}
