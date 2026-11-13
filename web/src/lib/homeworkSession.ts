import * as bridge from "./bridge";

/** The old place of the homework token. It is only ever deleted now. */
export const LEGACY_HOMEWORK_TOKEN_KEY = "fairplay:homework-token";

export const MSG_SIGNED_OUT = "You were signed out. Sign in again to continue.";
export const MSG_IDLE_WARNING = "You will be signed out in 2 minutes. Tap anywhere to stay signed in.";
export const MSG_CAP_WARNING = "Your 4-hour session ends soon. Save your work.";

export const RENEW_BELOW_MS = 20 * 60_000;
export const RENEW_MIN_GAP_MS = 5 * 60_000;
export const RETRY_GAP_MS = 15_000;
export const IDLE_WARN_MS = 2 * 60_000;
export const CAP_WARN_MS = 5 * 60_000;
export const TICK_MS = 1000;

export interface SessionTimes {
  expires_at?: string;
  absolute_expires_at?: string;
}

let token: string | null = null;
let idleAt = 0;
let capAt = Infinity;
let lastRenew = 0;
let lastAttempt = 0;
let lastActivity = 0;
let notice = false;

try {
  localStorage.removeItem(LEGACY_HOMEWORK_TOKEN_KEY);
} catch {
  /* storage unavailable */
}

function ms(v: string | undefined, fallback: number): number {
  const t = v ? Date.parse(v) : NaN;
  return Number.isFinite(t) ? t : fallback;
}

export function getHomeworkToken(): string | null {
  return token;
}

export function setHomeworkSession(t: string, times: SessionTimes = {}): void {
  const now = Date.now();
  token = t;
  idleAt = ms(times.expires_at, now + 30 * 60_000);
  capAt = ms(times.absolute_expires_at, Infinity);
  lastRenew = now;
  lastAttempt = now;
  lastActivity = now;
  notice = false;
}

export function clearHomeworkSession(): void {
  token = null;
  idleAt = 0;
  capAt = Infinity;
}

export function noteActivity(): void {
  lastActivity = Date.now();
}

/** True once, after a timed sign-out, so the app home can say why. */
export function takeSignedOutNotice(): boolean {
  const n = notice;
  notice = false;
  return n;
}

export function peekSignedOutNotice(): boolean {
  return notice;
}

export function expireHomeworkSession(): void {
  clearHomeworkSession();
  bridge.clearHomeworkLogin();
  notice = true;
}

export type Warning = "idle" | "cap" | null;

export function warningAt(now: number): Warning {
  if (!token) return null;
  if (capAt - now <= CAP_WARN_MS) return "cap";
  if (idleAt - now <= IDLE_WARN_MS) return "idle";
  return null;
}

let renewing = false;

async function renew(now: number): Promise<"ok" | "expired" | "failed"> {
  const used = token;
  if (!used) return "expired";
  renewing = true;
  lastAttempt = now;
  try {
    const res = await fetch("/api/homework/session/renew", {
      method: "POST",
      headers: { Accept: "application/json", Authorization: `Bearer ${used}` },
    });
    if (res.status === 401) return "expired";
    if (!res.ok) return "failed";
    const j = (await res.json()) as { token?: unknown } & SessionTimes;
    if (token !== used) return "ok";
    if (typeof j.token !== "string" || !j.token) return "failed";
    const keep = lastActivity;
    setHomeworkSession(j.token, j);
    lastActivity = keep;
    return "ok";
  } catch {
    return "failed";
  } finally {
    renewing = false;
  }
}

/** Watches the clock and the student's activity. Calls onChange when the warning changes
 *  and onExpire once the session is over. Returns a stop function. */
export function startHomeworkMonitor(onChange: (w: Warning) => void, onExpire: () => void): () => void {
  let stopped = false;
  let shown: Warning = null;
  const tick = async () => {
    if (stopped || !token) return;
    const now = Date.now();
    if (now >= capAt || now >= idleAt) {
      expireHomeworkSession();
      onExpire();
      return;
    }
    const active = lastActivity > lastRenew;
    if (active && !renewing && idleAt - now < RENEW_BELOW_MS && now - lastRenew > RENEW_MIN_GAP_MS && now - lastAttempt >= RETRY_GAP_MS) {
      const r = await renew(now);
      if (stopped) return;
      if (r === "expired") {
        expireHomeworkSession();
        onExpire();
        return;
      }
    }
    const w = warningAt(Date.now());
    if (w !== shown) {
      shown = w;
      onChange(w);
    }
  };
  const onAct = () => noteActivity();
  const evs = ["pointerdown", "pointermove", "keydown", "scroll", "wheel", "touchstart"] as const;
  evs.forEach((e) => window.addEventListener(e, onAct, { capture: true, passive: true }));
  const id = window.setInterval(() => void tick(), TICK_MS);
  void tick();
  return () => {
    stopped = true;
    window.clearInterval(id);
    evs.forEach((e) => window.removeEventListener(e, onAct, { capture: true }));
  };
}
