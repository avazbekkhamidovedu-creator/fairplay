import { ApiError } from "../api/client";
import { isSessionConflict } from "../components/student/ReenterExam";

const EXITED_KEY = "fairplay:exited";
const NOTICE_KEY = "fairplay:exit-notice";

export const EXIT_NOTICE = "You left the exam. Ask your invigilator to let you back in.";

function readIds(): string[] {
  try {
    const v = JSON.parse(localStorage.getItem(EXITED_KEY) ?? "[]");
    return Array.isArray(v) ? v.filter((x): x is string => typeof x === "string") : [];
  } catch {
    return [];
  }
}

/** The attempts the invigilator let out of the exam; they come back only with a start code. */
export function markExited(attemptId: string): void {
  try {
    const ids = readIds();
    if (!ids.includes(attemptId)) localStorage.setItem(EXITED_KEY, JSON.stringify([...ids, attemptId]));
    sessionStorage.setItem(NOTICE_KEY, "1");
  } catch {
    /* ignore */
  }
}

export function isExited(attemptId: string | undefined | null): boolean {
  return !!attemptId && readIds().includes(attemptId);
}

export function clearExited(attemptId: string): void {
  try {
    const ids = readIds().filter((x) => x !== attemptId);
    if (ids.length) localStorage.setItem(EXITED_KEY, JSON.stringify(ids));
    else localStorage.removeItem(EXITED_KEY);
  } catch {
    /* ignore */
  }
}

/** True once after an exit: the home page shows the notice and forgets it. */
export function takeExitNotice(): boolean {
  try {
    const v = sessionStorage.getItem(NOTICE_KEY) === "1";
    if (v) sessionStorage.removeItem(NOTICE_KEY);
    return v;
  } catch {
    return false;
  }
}

/** The server says this computer's exam session is gone because the student was let out. */
export function isExitError(e: unknown): boolean {
  if (!(e instanceof ApiError)) return false;
  if (e.code === "exited" || e.code === "session_gone" || e.code === "start_code_required") return true;
  if (e.status !== 409 && e.status !== 403) return false;
  return isSessionConflict(409, e.detail) || /\bexit(ed)?\b|session (is )?gone/i.test(e.detail);
}
