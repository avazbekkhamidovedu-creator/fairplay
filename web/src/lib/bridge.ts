// Native bridge to the FairPlay Mac app (CONTRACT.md "Native bridge").
// In a normal browser every call is a no-op and isNative() is false.

import type { CanvasScopeView, IntegrityEvent } from "../api/types";

/** What the app's "state" message says. app_version / platform (S2) and sealed_pending
 *  (S5) are missing from older app builds. */
export interface NativeState {
  locked: boolean;
  trusted: boolean;
  version: string;
  app_version?: string;
  platform?: string;
  sealed_pending?: boolean;
  /** 2026-10-23: locked before the start code, no exam attached yet. */
  prelock?: boolean;
  /** 2026-10-22 (Mac): a newer FairPlay the app can install itself (missing = none). */
  update?: AppUpdate;
  /** 2026-10-29: the app can run the "Try the exam screen" lock preview (missing = no). */
  deviceTest?: boolean;
  /** 2026-10-30: the app can open the separate homework login window (missing = no). */
  homeworkLogin?: boolean;
}

export type DeviceTestEnd = "leave" | "timeout" | "refused" | "error";

/** The app's answer to deviceTestStart; "ended" also arrives when the preview is left. */
export interface DeviceTestMessage {
  type: "deviceTest";
  state: "started" | "ended";
  reason?: DeviceTestEnd;
  remaining?: number;
}

/** The app's answer to openHomeworkLogin: the ticket from DKU OJ, a cancel, or an error. */
export interface HomeworkLoginResultMessage {
  type: "homeworkLoginResult";
  state?: string;
  ticket?: string;
  cancelled?: boolean;
  error?: string;
}

/** What the app needs to open the DKU OJ sign-in (POST /api/homework/login/start). */
export interface HomeworkLoginParams {
  state: string;
  start_url: string;
  allowed_hosts: string[];
  finish_url: string;
  resume_paths: string[];
}

/** A newer release the Mac app found and verified it can install ({action:"installUpdate"}). */
export interface AppUpdate {
  version: string;
}

export type NativeMessage =
  | ({ type: "state" } & NativeState)
  | { type: "locked"; prelock?: boolean }
  | { type: "lockFailed"; reason: string }
  | { type: "unlocked" }
  | DeviceTestMessage
  | HomeworkLoginResultMessage
  /** 2026-10-23: "preunlock" refused (an exam is attached to the lock). */
  | { type: "preunlockRefused" }
  /** Audit 2026-10-12: the app unlocks only once the server confirms the hand-in. */
  | { type: "unlockPending"; reason: string }
  /** iPad app: iPadOS is asking the student to allow Assessment Mode; keep waiting. */
  | { type: "lockPending"; reason: string }
  | { type: "event"; event: IntegrityEvent }
  | { type: "attestation"; nonce: string; device_id: string; signature: string; version: string; build: string }
  | { type: "attestationFailed"; nonce: string; reason: string }
  | { type: "preflight"; problems: string[]; checks?: DeviceCheck[]; platform?: string; app_version?: string; os_version?: string; apps_to_close?: string[] }
  /** 2026-10-22: answer to {action:"quitApps"}: what is still open after closing. */
  | { type: "appsQuit"; still_running: string[] }
  /** Review 2026-10-14: the app could not open the website exam (e.g. "bad_session"). */
  | { type: "externalFailed"; reason: string }
  /** S5: the app wrote the sealed answers file. */
  | { type: "sealed"; attempt_id: string; ok: boolean }
  /** 2026-10-22 (Mac): an update install is on its way ("downloading", then "installing"). */
  | { type: "updateProgress"; stage: string }
  /** 2026-10-22 (Mac): the update was not installed (see UpdateFailure). */
  | { type: "updateFailed"; reason: string };

/** The attempt the app is locking for: it sends its own signed beats with these. */
export interface ExamSessionParams {
  attempt_id: string;
  token: string;
  session: string;
  /** 2026-10-14: app ids (macOS bundle ids and Windows process names) kept open while
   *  locked; every other windowed app is closed. */
  allowed_apps?: string[];
  /** 2026-10-23 (exam tools): host names the exam's websites pane may show. The app allows
   *  only these hosts (and their subdomains) inside sub-frames of the exam page; [] = none.
   *  Sent only when the page passes it, so older message shapes stay as they were. */
  allowed_sites?: string[];
}

/** Flatten an attempt's allowed apps into the ids both apps understand. */
export function allowedAppIds(apps: { mac: string[]; windows: string[] }[] | undefined): string[] {
  const out: string[] = [];
  for (const a of apps ?? []) for (const id of [...a.mac, ...a.windows]) if (!out.includes(id)) out.push(id);
  return out;
}

/** Signed proof for POST start (CONTRACT.md "App session"). */
export interface ClientProof {
  device_id: string;
  nonce: string;
  signature: string;
  version: string;
  build: string;
}

/** Slice 3: open an external-website exam inside the lockdown (CONTRACT.md "Native bridge (added)"). */
export interface OpenExternalParams {
  attempt_id: string;
  url: string;
  allowed_domains: string[];
  finish_url_contains: string | null;
  /** Canvas mode: "canvas" opens the quiz inside the restricted Canvas browser. */
  kind?: "canvas";
  finish_url_regex?: string | null;
  /** Canvas mode: the course and item the student may reach in Canvas. */
  canvas_scope?: CanvasScopeView | null;
  deadline_at: string;
  server_time: string;
  title: string;
  /** Student bearer token: the app sends heartbeats/events itself while the web page is gone. */
  token: string;
  /** App session of the attempt (X-FairPlay-Session); "" for an attempt still offline. */
  session: string;
  allowed_apps?: string[];
}

type OutMessage =
  | ({ action: "lock"; offline?: boolean } & Partial<ExamSessionParams>)
  | ({ action: "session" } & ExamSessionParams)
  | { action: "attest"; nonce: string }
  | { action: "unlock" }
  | { action: "prelock" }
  | { action: "preunlock" }
  | { action: "hello" }
  | { action: "flushEvents" }
  | { action: "preflight"; allowed_apps?: string[] }
  | { action: "quitApps"; allowed_apps?: string[] }
  | ({ action: "openExternal" } & OpenExternalParams)
  | ({ action: "seal" } & SealParams)
  | { action: "sealedToken"; token: string }
  | { action: "openSettings"; pane: string }
  | { action: "installUpdate" }
  | { action: "deviceTestStart"; seconds: number }
  | { action: "deviceTestLeave" }
  | ({ action: "openHomeworkLogin" } & HomeworkLoginParams)
  | { action: "clearHomeworkLogin" }
  | { action: "openDownloadPage" };

/** S5: what the app seals when the submit at time-up failed. answers_json is the exact
 *  string the app hashes (SHA-256 of its UTF-8 bytes). */
export interface SealParams {
  attempt_id: string;
  answers_json: string;
  client_seq: number;
}

interface ProctoraHandler {
  postMessage(msg: OutMessage): void;
}

/** WebView2 (Windows app) transport: window.chrome.webview.postMessage(json). */
interface WebView2 {
  postMessage(msg: unknown): void;
  addEventListener(type: "message", listener: (e: unknown) => void): void;
}

declare global {
  interface Window {
    // macOS app (WKWebView).
    webkit?: { messageHandlers?: { proctora?: ProctoraHandler } };
    // Windows app (WebView2). Named chrome.webview by the runtime.
    chrome?: { webview?: WebView2 };
  }
}

function macHandler(): ProctoraHandler | undefined {
  return typeof window !== "undefined" ? window.webkit?.messageHandlers?.proctora : undefined;
}

function winHandler(): WebView2 | undefined {
  return typeof window !== "undefined" ? window.chrome?.webview : undefined;
}

export function isNative(): boolean {
  return !!macHandler() || !!winHandler();
}

/** Which native host we're inside, for platform-specific copy/download links. */
export function nativePlatform(): "mac" | "windows" | "ipad" | null {
  if (macHandler()) {
    // The iPad app says so in its "state"; before that answer, its user agent tells.
    if (getNativeState()?.platform === "ipad") return "ipad";
    return typeof navigator !== "undefined" && navigator.userAgent.includes("FairPlay-iPad") ? "ipad" : "mac";
  }
  if (winHandler()) return "windows";
  return null;
}

function post(msg: OutMessage): void {
  // Both hosts receive the same message shape; only the transport differs. The Windows
  // shell dispatches replies through the identical 'proctora-native' CustomEvent, so the
  // receive side below is shared.
  const mac = macHandler();
  if (mac) {
    try {
      mac.postMessage(msg);
    } catch {
      /* ignore */
    }
    return;
  }
  const win = winHandler();
  if (win) {
    try {
      win.postMessage(msg);
    } catch {
      /* ignore */
    }
  }
}

// Audit W-10: the native host may put a per-launch nonce on the page before any page
// script runs (window.__fairplayNonce) and stamp every 'proctora-native' event with it as `bridge_nonce` (not `nonce`: attestation
// messages already carry the server challenge in `nonce`).
// Taken once here and removed from window, so later page scripts can't read it; when a host
// set one, events without it (dispatched by page JS) are ignored. Hosts that don't set one
// (older builds) work as before.
let expectedNonce: string | null = null;
if (typeof window !== "undefined") {
  const w = window as unknown as { __fairplayNonce?: unknown };
  if (typeof w.__fairplayNonce === "string" && w.__fairplayNonce.length >= 16) expectedNonce = w.__fairplayNonce;
  try {
    delete w.__fairplayNonce;
  } catch {
    /* ignore */
  }
}

// Last known lock state, kept up to date from native messages.
let locked = false;
// Last "state" message from native (null until the Mac app answers "hello").
let nativeState: NativeState | null = null;
const listeners = new Set<(msg: NativeMessage) => void>();

function onWindowEvent(e: Event) {
  const detail = (e as CustomEvent).detail as (NativeMessage & { bridge_nonce?: unknown }) | undefined;
  if (!detail || typeof detail !== "object" || typeof detail.type !== "string") return;
  if (expectedNonce !== null && detail.bridge_nonce !== expectedNonce) return;   // audit W-10
  if (detail.type === "state") {
    locked = !!detail.locked;
    nativeState = stateOf(detail);
    offerSealedToken(tokenSource());
  }
  else if (detail.type === "locked") locked = true;
  else if (detail.type === "unlocked" || detail.type === "lockFailed") locked = false;
  listeners.forEach((l) => l(detail));
}

function stateOf(d: NativeState): NativeState {
  const out: NativeState = { locked: !!d.locked, trusted: !!d.trusted, version: String(d.version ?? "") };
  if (typeof d.app_version === "string" && d.app_version) out.app_version = d.app_version;
  if (typeof d.platform === "string" && d.platform) out.platform = d.platform;
  if (typeof d.sealed_pending === "boolean") out.sealed_pending = d.sealed_pending;
  if (typeof d.prelock === "boolean") out.prelock = d.prelock;   // 2026-10-23
  if (d.deviceTest === true) out.deviceTest = true;
  if (d.homeworkLogin === true) out.homeworkLogin = true;
  const update = parseUpdate((d as { update?: unknown }).update);
  if (update) out.update = update;
  return out;
}

/** The state's `update` field (it crosses a trust boundary): {version: "X.Y.Z"} or null. */
export function parseUpdate(v: unknown): AppUpdate | null {
  if (!v || typeof v !== "object" || Array.isArray(v)) return null;
  const version = (v as { version?: unknown }).version;
  if (typeof version !== "string" || version.length > 32 || !/^\d+\.\d+\.\d+$/.test(version)) return null;
  return { version };
}

/** Why the Mac or Windows app did not install an update. */
export type UpdateFailure = "download" | "verify" | "not_writable" | "locked" | "busy" | "no_update" | "unknown";

/** Mac and Windows reasons. Windows also says "failed" (= download) and "not_installed"
 *  (the zip copy can't update itself: = download page). */
export function parseUpdateFailure(reason: unknown): UpdateFailure {
  if (reason === "failed") return "download";
  if (reason === "not_installed") return "not_writable";
  return reason === "download" || reason === "verify" || reason === "not_writable" || reason === "locked" ||
    reason === "busy" || reason === "no_update"
    ? reason
    : "unknown";
}

/** Public page with every FairPlay download. */
export const DOWNLOAD_PAGE = "https://github.com/avazbekkhamidovedu-creator/fairplay-download/releases/latest";

/** 2026-10-22: ask the Mac or Windows app to download, verify and install the update it offered
 *  (state.update). It answers updateProgress / updateFailed, or quits and reopens updated.
 *  Returns false outside the Mac app (nothing is sent). */
export function installUpdate(): boolean {
  const p = nativePlatform();
  if (p !== "mac" && p !== "windows") return false;
  post({ action: "installUpdate" });
  return true;
}

/** Ask the app to show the lock preview for `seconds`. Returns false in a plain browser. */
export function deviceTestStart(seconds: number): boolean {
  if (!isNative()) return false;
  post({ action: "deviceTestStart", seconds });
  return true;
}

export function deviceTestLeave(): boolean {
  if (!isNative()) return false;
  post({ action: "deviceTestLeave" });
  return true;
}

/** Subscribe to the app's deviceTest messages. Returns an unsubscribe function. */
export function onDeviceTest(listener: (msg: DeviceTestMessage) => void): () => void {
  return onNative((m) => {
    if (m.type === "deviceTest") listener(m);
  });
}

/** True inside an app that said (in its state) it can open the homework login window. */
export function homeworkLoginSupported(): boolean {
  return isNative() && getNativeState()?.homeworkLogin === true;
}

/** Ask the app to open the separate, non-locking DKU OJ login window. Its answer is a
 *  homeworkLoginResult message. Returns false when the app can't (nothing is sent). */
export function openHomeworkLogin(p: HomeworkLoginParams): boolean {
  if (!homeworkLoginSupported()) return false;
  post({
    action: "openHomeworkLogin",
    state: p.state,
    start_url: p.start_url,
    allowed_hosts: [...p.allowed_hosts],
    finish_url: p.finish_url,
    resume_paths: [...p.resume_paths],
  });
  return true;
}

/** Sign-out: the app clears the login window's cookies and data. Nothing is sent to an app
 *  without homework login support. */
export function clearHomeworkLogin(): boolean {
  if (!homeworkLoginSupported()) return false;
  post({ action: "clearHomeworkLogin" });
  return true;
}

/** Ask the Mac app to open DOWNLOAD_PAGE in the browser (the exam window only shows the
 *  exam server). Returns false outside the Mac app: follow the link normally there. */
export function openDownloadPage(): boolean {
  if (nativePlatform() !== "mac") return false;
  post({ action: "openDownloadPage" });
  return true;
}

let installed = false;
function install() {
  if (installed || typeof window === "undefined") return;
  installed = true;
  window.addEventListener("proctora-native", onWindowEvent);
}

/** Subscribe to native messages. Returns an unsubscribe function. */
export function onNative(listener: (msg: NativeMessage) => void): () => void {
  install();
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function isLocked(): boolean {
  return isNative() && locked;
}

/** Last state the Mac app reported (null outside the app or before it answered). */
export function getNativeState(): NativeState | null {
  return isNative() ? nativeState : null;
}

export function hello(): void {
  install();
  post({ action: "hello" });
}

/** Review 2026-10-14: this page forwards integrity events now; the app sends the ones it
 *  held while no such page was listening, and the next ones as they happen. */
export function requestEvents(): void {
  post({ action: "flushEvents" });
}

/** 2026-10-22: System Settings panes the Mac app can open for a pre-exam problem. */
export type SettingsPane = "sharing" | "datetime" | "displays" | "keyboard" | "accessibility";

/** Which pane fixes a pre-exam problem text from the Mac app, or null. */
export function settingsPaneFor(problem: string): SettingsPane | null {
  const p = problem.toLowerCase();
  if (p.includes("general > sharing")) return "sharing";
  if (p.includes("date & time") || p.includes("date and time")) return "datetime";
  if (p.includes("screen") || p.includes("display") || p.includes("mirroring")) return "displays";
  if (p.includes("keyboard")) return "keyboard";
  if (p.includes("accessibility")) return "accessibility";
  return null;
}

/** Ask the Mac app to open that System Settings pane (other apps ignore it). */
export function openSettings(pane: SettingsPane): boolean {
  if (nativePlatform() !== "mac") return false;
  post({ action: "openSettings", pane });
  return true;
}

/** The invigilator exit gesture of the host app, as the student should read it. */
export function exitShortcut(platform: "mac" | "windows" | "ipad" | null = nativePlatform()): string {
  if (platform === "windows") return "Ctrl+Alt+E";
  if (platform === "ipad") return "a 3-second press on the lock bar at the top";
  return "Ctrl-Opt-Cmd-E";
}

export function unlock(): void {
  post({ action: "unlock" });
}

/**
 * Ask the Mac app to open an external-website exam (it must already be locked; the app
 * locks first if needed). Returns false outside the Mac app (nothing is sent).
 */
export function openExternal(params: OpenExternalParams): boolean {
  if (!isNative()) return false;
  post({
    action: "openExternal",
    attempt_id: params.attempt_id,
    url: params.url,
    allowed_domains: [...params.allowed_domains],
    finish_url_contains: params.finish_url_contains ?? null,
    ...(params.kind === "canvas" ? { kind: "canvas" as const, finish_url_regex: params.finish_url_regex ?? null } : {}),
    ...(params.kind === "canvas" && params.canvas_scope ? { canvas_scope: { ...params.canvas_scope } } : {}),
    deadline_at: params.deadline_at,
    server_time: params.server_time,
    title: params.title,
    token: params.token,
    session: params.session,
    allowed_apps: [...(params.allowed_apps ?? [])],
  });
  return true;
}

/**
 * Tell the app which attempt it is locked for, so it sends its own signed beats and
 * checks with the server before unlocking. Call again when the session changes.
 */
export function setExamSession(params: ExamSessionParams): void {
  if (!params.session) return;
  post({
    action: "session",
    attempt_id: params.attempt_id,
    token: params.token,
    session: params.session,
    allowed_apps: [...(params.allowed_apps ?? [])],
    ...sitesOf(params),
  });
}

/** 2026-10-23: `allowed_sites` for lock / session, when the page gave it (a copy). */
function sitesOf(p: Partial<ExamSessionParams> | undefined): { allowed_sites?: string[] } {
  return p?.allowed_sites ? { allowed_sites: [...p.allowed_sites] } : {};
}

/**
 * Ask the app to sign a start challenge. Resolves null outside the app or when the app
 * cannot sign (old build): the server then refuses the start.
 */
export function attest(nonce: string, timeoutMs = 5000): Promise<ClientProof | null> {
  install();
  if (!isNative()) return Promise.resolve(null);
  return new Promise((resolve) => {
    let done = false;
    const finish = (r: ClientProof | null) => {
      if (done) return;
      done = true;
      clearTimeout(timer);
      off();
      resolve(r);
    };
    const off = onNative((msg) => {
      if (msg.type === "attestation" && msg.nonce === nonce)
        finish({
          device_id: String(msg.device_id),
          nonce,
          signature: String(msg.signature),
          version: String(msg.version ?? ""),
          build: String(msg.build ?? ""),
        });
      else if (msg.type === "attestationFailed" && msg.nonce === nonce) finish(null);
    });
    const timer = setTimeout(() => finish(null), timeoutMs);
    post({ action: "attest", nonce });
  });
}

export type LockResult = { ok: true } | { ok: false; reason: string };

/**
 * Ask the Mac app to lock the computer and wait for "locked" or "lockFailed".
 * Outside the Mac app resolves immediately with ok: there is nothing to lock, and the
 * server only lets a browser get this far when FAIRPLAY_REQUIRE_APP=0 (demo mode); real
 * exams refuse browser starts and only trust the app's own signed beats (audit #1, #2).
 */
export function requestLock(timeoutMs = 10000, exam?: Partial<ExamSessionParams>): Promise<LockResult> {
  install();
  if (!isNative()) return Promise.resolve({ ok: true });
  return new Promise((resolve) => {
    let done = false;
    const finish = (r: LockResult) => {
      if (done) return;
      done = true;
      clearTimeout(timer);
      off();
      resolve(r);
    };
    const off = onNative((msg) => {
      // 2026-10-23 review: while the pre-exam lock is on, a "state" (or a late prelock
      // "locked") says locked:true before the exam lock exists. Only the exam lock counts.
      if (msg.type === "locked" && !msg.prelock) finish({ ok: true });
      else if (msg.type === "lockFailed") finish({ ok: false, reason: msg.reason || "unknown" });
      else if (msg.type === "state" && msg.locked && !msg.prelock) finish({ ok: true });
      else if (msg.type === "lockPending") {
        // The iPad app waits for the student to allow Assessment Mode: wait with it.
        clearTimeout(timer);
        timer = setTimeout(() => finish({ ok: false, reason: "timeout" }), timeoutMs);
      }
    });
    let timer = setTimeout(() => finish({ ok: false, reason: "timeout" }), timeoutMs);
    // Audit M-06: the app locks only for a valid exam session, or when the page says
    // this is an offline start (no server session yet; the app then can't unlock by itself
    // until the attempt reaches the server).
    post(
      exam && exam.session
        ? { action: "lock", ...exam, ...sitesOf(exam) }
        : { action: "lock", offline: true, allowed_apps: [...(exam?.allowed_apps ?? [])], ...sitesOf(exam) },
    );
  });
}

/** 2026-10-23: result of the pre-exam lock. "unsupported": outside the app, or an app build
 *  that doesn't know "prelock" (the exam then locks after the start code, as before). */
export type PreLockResult = { ok: true } | { ok: false; reason: string } | { ok: false; reason: "unsupported" };

/**
 * 2026-10-23: lock the computer BEFORE the start code ("Before you start" -> "Lock and
 * continue"). No exam is attached yet, so the student may still leave with preUnlock().
 * The exam's own requestLock() later turns this into the exam lock without unlocking.
 */
export function requestPreLock(timeoutMs = 10000): Promise<PreLockResult> {
  install();
  if (!isNative()) return Promise.resolve({ ok: false, reason: "unsupported" });
  return new Promise((resolve) => {
    let done = false;
    let answered = false;
    const finish = (r: PreLockResult) => {
      if (done) return;
      done = true;
      clearTimeout(timer);
      off();
      resolve(r);
    };
    const off = onNative((msg) => {
      if (msg.type === "locked") finish({ ok: true });
      else if (msg.type === "lockFailed") finish({ ok: false, reason: msg.reason || "unknown" });
      else if (msg.type === "lockPending") {
        // iPad: iPadOS asks the student to allow Assessment Mode; wait with it.
        answered = true;
        clearTimeout(timer);
        timer = setTimeout(() => finish({ ok: false, reason: "timeout" }), timeoutMs);
      }
    });
    // An older app ignores "prelock": no answer at all within PRELOCK_SILENT_MS -> unsupported.
    let timer = setTimeout(() => finish(answered ? { ok: false, reason: "timeout" } : { ok: false, reason: "unsupported" }), Math.min(timeoutMs, PRELOCK_SILENT_MS));
    post({ action: "prelock" });
  });
}

/** How long requestPreLock waits for any answer before treating the app as too old. */
export const PRELOCK_SILENT_MS = 4000;

/** 2026-10-23: leave the pre-exam lock ("Not ready? Leave and unlock"). The app refuses it
 *  once an exam is attached. Resolves true when the app unlocked (or there is no app). */
export function preUnlock(timeoutMs = 5000): Promise<boolean> {
  install();
  if (!isNative()) return Promise.resolve(true);
  return new Promise((resolve) => {
    let done = false;
    const finish = (ok: boolean) => {
      if (done) return;
      done = true;
      clearTimeout(timer);
      off();
      resolve(ok);
    };
    const off = onNative((msg) => {
      if (msg.type === "unlocked") finish(true);
      else if (msg.type === "preunlockRefused") finish(false);
      else if (msg.type === "state" && !msg.locked) finish(true);
    });
    const timer = setTimeout(() => finish(!isLocked()), timeoutMs);
    post({ action: "preunlock" });
  });
}

/**
 * Audit M-11: ask the app which problems must be fixed BEFORE locking (Remote Login,
 * extra display, VM, Accessibility off...). [] = ready. Outside the app, or an app build
 * that doesn't know "preflight", resolves [] after the timeout (the lock's own checks
 * still run).
 */
export async function preflight(timeoutMs = 3000): Promise<string[]> {
  return (await preflightReport(timeoutMs))?.problems ?? [];
}

/**
 * 2026-10-22: ask the app to close the apps it listed in `apps_to_close` (the student saw
 * the list and saved their work). Resolves with what is still open; [] outside the app or
 * when an older app doesn't answer (the lock itself closes them anyway).
 */
export function quitApps(allowedApps: string[] = [], timeoutMs = 9000): Promise<string[]> {
  install();
  if (!isNative()) return Promise.resolve([]);
  return new Promise((resolve) => {
    let done = false;
    const finish = (r: string[]) => {
      if (done) return;
      done = true;
      clearTimeout(timer);
      off();
      resolve(r);
    };
    const off = onNative((msg) => {
      if (msg.type === "appsQuit") finish(names((msg as { still_running?: unknown }).still_running));
    });
    const timer = setTimeout(() => finish([]), timeoutMs);
    post({ action: "quitApps", allowed_apps: allowedApps });
  });
}

/** App names from the app (a trust boundary): strings only, short, at most 30. */
export function names(v: unknown): string[] {
  if (!Array.isArray(v)) return [];
  const out: string[] = [];
  for (const x of v.slice(0, 30)) {
    if (typeof x !== "string") continue;
    const t = x.trim().slice(0, 80);
    if (t && !out.includes(t)) out.push(t);
  }
  return out;
}

/** S1: one row of the app's device check. `info` rows (VPN, battery) never block. */
export interface DeviceCheck {
  id: string;
  ok: boolean;
  message: string;
  info?: boolean;
}

export interface PreflightReport {
  problems: string[];
  /** 2026-10-22: windowed apps the lock would close (shown so the student can save first). */
  apps_to_close: string[];
  /** [] from older apps and Windows builds that only send `problems`. */
  checks: DeviceCheck[];
  platform: string;
  app_version: string;
  os_version: string;
}

const text = (v: unknown, max = 300): string => (typeof v === "string" ? v.slice(0, max) : "");

/** Read the app's "preflight" answer defensively (it crosses a trust boundary). */
export function parsePreflight(msg: Record<string, unknown>): PreflightReport {
  const checks: DeviceCheck[] = [];
  for (const c of Array.isArray(msg.checks) ? msg.checks.slice(0, 40) : []) {
    if (!c || typeof c !== "object") continue;
    const r = c as Record<string, unknown>;
    const id = text(r.id);
    if (!id) continue;
    const row: DeviceCheck = { id, ok: r.ok === true, message: text(r.message) };
    if (r.info === true) row.info = true;
    checks.push(row);
  }
  return {
    problems: Array.isArray(msg.problems) ? msg.problems.map(String) : [],
    apps_to_close: names(msg.apps_to_close),
    checks,
    platform: text(msg.platform) || nativePlatform() || "",
    app_version: text(msg.app_version),
    os_version: text(msg.os_version),
  };
}

/** S1: the whole preflight answer (null outside the app or when it doesn't answer). */
export function preflightReport(timeoutMs = 3000, allowedApps?: string[]): Promise<PreflightReport | null> {
  install();
  if (!isNative()) return Promise.resolve(null);
  return new Promise((resolve) => {
    let done = false;
    const finish = (r: PreflightReport | null) => {
      if (done) return;
      done = true;
      clearTimeout(timer);
      off();
      resolve(r);
    };
    const off = onNative((msg) => {
      if (msg.type === "preflight") finish(parsePreflight(msg as unknown as Record<string, unknown>));
    });
    const timer = setTimeout(() => finish(null), timeoutMs);
    post(allowedApps ? { action: "preflight", allowed_apps: allowedApps } : { action: "preflight" });
  });
}

/**
 * S5: seal the answers on this computer because the hand-in at time-up failed. The app
 * signs them with the attempt's offline key, keeps the file and uploads it once online
 * (POST .../sealed-submit). Resolves true when the app says the file is written; false
 * outside the app, for an app that doesn't know "seal", or on {ok:false}.
 */
export function seal(params: SealParams, timeoutMs = 8000): Promise<boolean> {
  install();
  if (!isNative()) return Promise.resolve(false);
  return new Promise((resolve) => {
    let done = false;
    const finish = (ok: boolean) => {
      if (done) return;
      done = true;
      clearTimeout(timer);
      off();
      resolve(ok);
    };
    const off = onNative((msg) => {
      if (msg.type === "sealed" && msg.attempt_id === params.attempt_id) finish(msg.ok === true);
    });
    const timer = setTimeout(() => finish(false), timeoutMs);
    post({ action: "seal", attempt_id: params.attempt_id, answers_json: params.answers_json, client_seq: params.client_seq });
  });
}

// S5: student tokens last 8 h, so at its next launch the app may hold sealed answers but no
// valid token. When the app says sealed_pending and a student is signed in, the page hands
// it the token once per page load; the app then uploads its sealed files with it.
let tokenSource: () => string | null = () => null;
let sealedTokenSent = false;

/** client.ts registers how to read the student token (bridge can't import client.ts). */
export function setSealedTokenSource(source: () => string | null): void {
  tokenSource = source;
}

/** Send {action:"sealedToken"} if the app holds sealed answers; true when sent. */
export function offerSealedToken(token: string | null): boolean {
  if (sealedTokenSent || !token || !getNativeState()?.sealed_pending) return false;
  sealedTokenSent = true;
  post({ action: "sealedToken", token });
  return true;
}

/** Test helper: pretend the host set this nonce at load (null = none). */
export function _setNonceForTests(nonce: string | null): void {
  expectedNonce = nonce;
}

/** Test helper: reset module state. */
export function _resetBridgeForTests(): void {
  locked = false;
  nativeState = null;
  sealedTokenSent = false;
  listeners.clear();
}
