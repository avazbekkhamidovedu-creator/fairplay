import * as bridge from "./bridge";

export const HOMEWORK_TOKEN_KEY = "fairplay:homework-token";

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
export const MSG_NETWORK = "Cannot reach the server. Check your connection and try again.";

export type LoginOutcome = { ok: true; identity: HomeworkIdentity } | { ok: false; cancelled?: boolean; message?: string };

export function getHomeworkToken(): string | null {
  try {
    return localStorage.getItem(HOMEWORK_TOKEN_KEY);
  } catch {
    return null;
  }
}

export function setHomeworkToken(token: string): void {
  try {
    localStorage.setItem(HOMEWORK_TOKEN_KEY, token);
  } catch {
    /* storage unavailable */
  }
}

export function clearHomeworkToken(): void {
  try {
    localStorage.removeItem(HOMEWORK_TOKEN_KEY);
  } catch {
    /* storage unavailable */
  }
}

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

async function post<T>(path: string, body: unknown): Promise<{ status: number; data: T | null }> {
  const res = await fetch(path, {
    method: "POST",
    headers: { Accept: "application/json", "Content-Type": "application/json" },
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
export async function runHomeworkLogin(): Promise<LoginOutcome> {
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
    const r = await post<{ token: string; name: string; netid: string }>("/api/homework/login/finish", {
      state: start.state,
      ticket: w.ticket,
    });
    if (r.status !== 200 || !r.data?.token) return { ok: false, message: statusMessage(r.status) };
    setHomeworkToken(r.data.token);
    return { ok: true, identity: { name: String(r.data.name ?? ""), netid: String(r.data.netid ?? "") } };
  } catch {
    return { ok: false, message: MSG_NETWORK };
  }
}

/** GET /api/homework/me with the homework token. "signed-out" = no token or the server refused it. */
export async function loadHomeworkMe(): Promise<HomeworkIdentity | "signed-out" | "error"> {
  const token = getHomeworkToken();
  if (!token) return "signed-out";
  try {
    const res = await fetch("/api/homework/me", { headers: { Accept: "application/json", Authorization: `Bearer ${token}` } });
    if (res.status === 401 || res.status === 403) {
      clearHomeworkToken();
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
  clearHomeworkToken();
  bridge.clearHomeworkLogin();
}
