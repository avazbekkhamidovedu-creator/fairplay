import * as bridge from "./bridge";
import {
  LEGACY_HOMEWORK_TOKEN_KEY,
  clearHomeworkSession,
  getHomeworkToken,
  noteActivity,
  setHomeworkSession,
  type SessionTimes,
} from "./homeworkSession";

export { getHomeworkToken };
export const HOMEWORK_TOKEN_KEY = LEGACY_HOMEWORK_TOKEN_KEY;

export interface HomeworkInfo {
  provider: string;
  label: string;
}

export interface HomeworkIdentity {
  name: string;
  netid: string;
}

export const MSG_EXPIRED = "Sign-in expired, try again.";
export const MSG_NO_CLASS = "Your DKU OJ account is not linked to a class. Ask your professor.";
export const MSG_RATE = "Too many tries. Wait a moment and try again.";
export const MSG_BUSY = "DKU OJ cannot be reached right now. Try again in a few minutes.";
export const MSG_BRIDGE = "The sign-in window could not be completed. Try again.";
export const MSG_GENERIC = "Could not sign in. Try again.";
export const MSG_AGAIN = "Please sign in again.";
export const MSG_NETWORK = "Cannot reach the server. Check your connection and try again.";

export type LoginOutcome = { ok: true; identity: HomeworkIdentity } | { ok: false; cancelled?: boolean; message?: string };

export async function loadHomeworkInfo(): Promise<HomeworkInfo | null> {
  try {
    const r = await fetch("/api/health", { headers: { Accept: "application/json" } });
    if (!r.ok) return null;
    const j = (await r.json()) as { homework_login?: { provider?: unknown; label?: unknown } | null };
    const h = j?.homework_login;
    if (!h || typeof h !== "object") return null;
    return { provider: String(h.provider ?? ""), label: typeof h.label === "string" && h.label ? h.label : "DKU OJ" };
  } catch {
    return null;
  }
}

function statusMessage(status: number): string {
  if (status === 400) return MSG_EXPIRED;
  if (status === 403) return MSG_NO_CLASS;
  if (status === 429) return MSG_RATE;
  if (status === 503 || status === 504) return MSG_BUSY;
  return MSG_GENERIC;
}

async function post<T>(path: string, body: unknown, bearer?: string | null): Promise<{ status: number; data: T | null }> {
  noteActivity();
  const res = await fetch(path, {
    method: "POST",
    headers: { Accept: "application/json", "Content-Type": "application/json", ...(bearer ? { Authorization: `Bearer ${bearer}` } : {}) },
    body: JSON.stringify(body),
  });
  let data: T | null = null;
  try {
    data = (await res.json()) as T;
  } catch {
    data = null;
  }
  return { status: res.status, data };
}

interface StartReply extends bridge.HomeworkLoginParams {
  expires_at: string;
}

type Wait = { kind: "ticket"; ticket: string } | { kind: "cancelled" } | { kind: "error" } | { kind: "timeout" };

function waitForResult(state: string, expiresAt: string): Promise<Wait> {
  return new Promise((resolve) => {
    let done = false;
    const end = (w: Wait) => {
      if (done) return;
      done = true;
      clearTimeout(timer);
      off();
      resolve(w);
    };
    const off = bridge.onNative((m) => {
      if (m.type !== "homeworkLoginResult") return;
      if (m.state !== undefined && m.state !== state) return;
      if (m.cancelled === true) end({ kind: "cancelled" });
      else if (typeof m.error === "string" && m.error) end({ kind: "error" });
      else if (m.state === state && typeof m.ticket === "string" && m.ticket) end({ kind: "ticket", ticket: m.ticket });
    });
    const ms = Math.max(0, Date.parse(expiresAt) - Date.now());
    const timer = setTimeout(() => end({ kind: "timeout" }), Number.isFinite(ms) ? ms : 0);
  });
}

/** Whole DKU OJ sign-in: start, the app's login window, finish. Stores the homework token
 *  under its own key; the exam token is never touched. */
export async function runHomeworkLogin(opts: { remember?: boolean } = {}): Promise<LoginOutcome> {
  if (!bridge.homeworkLoginSupported()) return { ok: false, message: MSG_BRIDGE };
  let start: StartReply;
  try {
    const r = await post<StartReply>("/api/homework/login/start", {});
    if (r.status !== 200 || !r.data) return { ok: false, message: statusMessage(r.status) };
    start = r.data;
  } catch {
    return { ok: false, message: MSG_NETWORK };
  }
  const waiting = waitForResult(start.state, start.expires_at);
  const sent = bridge.openHomeworkLogin({
    state: start.state,
    start_url: start.start_url,
    allowed_hosts: start.allowed_hosts,
    finish_url: start.finish_url,
    resume_paths: start.resume_paths,
  });
  if (!sent) return { ok: false, message: MSG_BRIDGE };
  const w = await waiting;
  if (w.kind === "cancelled") return { ok: false, cancelled: true };
  if (w.kind === "timeout") return { ok: false, message: MSG_EXPIRED };
  if (w.kind === "error") return { ok: false, message: MSG_BRIDGE };
  try {
    const r = await post<{ token: string; name: string; netid: string } & SessionTimes>("/api/homework/login/finish", {
      state: start.state,
      ticket: w.ticket,
    });
    if (r.status !== 200 || !r.data?.token) return { ok: false, message: statusMessage(r.status) };
    setHomeworkSession(r.data.token, r.data);
    const identity = { name: String(r.data.name ?? ""), netid: String(r.data.netid ?? "") };
    if (opts.remember) await createRemember(identity.name);
    return { ok: true, identity };
  } catch {
    return { ok: false, message: MSG_NETWORK };
  }
}

/** GET /api/homework/me with the homework token. "signed-out" = no token or the server refused it. */
export async function loadHomeworkMe(): Promise<HomeworkIdentity | "signed-out" | "error"> {
  const token = getHomeworkToken();
  if (!token) return "signed-out";
  noteActivity();
  try {
    const res = await fetch("/api/homework/me", { headers: { Accept: "application/json", Authorization: `Bearer ${token}` } });
    if (res.status === 401 || res.status === 403) {
      clearHomeworkSession();
      return "signed-out";
    }
    if (!res.ok) return "error";
    const j = (await res.json()) as { name?: unknown; netid?: unknown };
    return { name: String(j.name ?? ""), netid: String(j.netid ?? "") };
  } catch {
    return "error";
  }
}

export function signOutHomework(): void {
  clearHomeworkSession();
  bridge.clearHomeworkLogin();
}

/** Best effort: a failure here never blocks the sign-in that just worked. */
async function createRemember(name: string): Promise<void> {
  try {
    const r = await post<{ remember_token?: string }>("/api/homework/remember/create", {}, getHomeworkToken());
    if (r.status === 200 && r.data?.remember_token) bridge.rememberSave(r.data.remember_token, name);
  } catch {
    /* not remembered */
  }
}

export type ContinueOutcome = { ok: true } | { ok: false; cancelled?: boolean; message?: string };

/** "Continue as <name>": device unlock, then trade the stored remember token for a session. */
export async function continueAsRemembered(): Promise<ContinueOutcome> {
  const u = await bridge.rememberUnlock();
  if ("cancelled" in u) return { ok: false, cancelled: true };
  if ("error" in u) return { ok: false, message: MSG_GENERIC };
  try {
    const r = await post<{ token: string; name: string; remember_token?: string } & SessionTimes>("/api/homework/remember/exchange", {
      remember_token: u.token,
    });
    if (r.status === 401) {
      bridge.rememberForget();
      return { ok: false, message: MSG_AGAIN };
    }
    if (r.status !== 200 || !r.data?.token) return { ok: false, message: statusMessage(r.status) };
    setHomeworkSession(r.data.token, r.data);
    if (r.data.remember_token) bridge.rememberSave(r.data.remember_token, String(r.data.name ?? ""));
    return { ok: true };
  } catch {
    return { ok: false, message: MSG_NETWORK };
  }
}

/** "Forget this device": drop every remember token of the account, then sign out. */
export async function forgetThisDevice(): Promise<void> {
  const token = getHomeworkToken();
  try {
    if (token) await post("/api/homework/remember/forget-all", {}, token);
  } catch {
    /* the local copy is deleted below either way */
  }
  bridge.rememberForget();
  signOutHomework();
}
