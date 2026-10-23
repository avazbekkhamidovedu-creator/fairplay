import type { AllowedAppOption,
  AnswerMap,
  DeviceCheckInput,
  ReadinessRow,
  AttemptView,
  EventsResponse,
  Exam,
  ExamStudent,
  OpenTo,
  ExamInput,
  EventReview,
  GradeInput,
  GradeResponse,
  Gradebook,
  GradingQueue,
  GradingSummary,
  StudentFeedback,
  StudentGradeSummary,
  TrialInfo,
  HeartbeatResponse,
  IntegrityEvent,
  LiveRoom,
  LoginResponse,
  Me,
  Results,
  Role,
  SaveAnswersResponse,
  StudentExamSummary,
  SubmitResponse,
} from "./types";
import type {
  LmsConnection,
  LmsConnectionInput,
  LmsCourse,
  LmsCourseLink,
  LmsLinkResponse,
  LmsProvider,
  LmsPushResult,
} from "./types"; // slice 5

import * as bridge from "../lib/bridge";
import { attemptIdOfPath, getAttemptSession, setAttemptSession } from "../lib/appSession";

// ---------- token storage (one token per role) ----------

export const tokenKey = (role: Role) => `proctora:token:${role}`;

export function getToken(role: Role): string | null {
  try {
    return localStorage.getItem(tokenKey(role));
  } catch {
    return null;
  }
}

export function setToken(role: Role, token: string): void {
  try {
    localStorage.setItem(tokenKey(role), token);
  } catch {
    /* storage unavailable */
  }
  // S5: right after a student signs in, an app holding sealed answers gets the new token.
  if (role === "student") bridge.offerSealedToken(token);
}

bridge.setSealedTokenSource(() => getToken("student"));

/**
 * Account id inside a token ("<uid>:<role>:<exp>:<ver>:<kid>" base64url, then "." signature).
 * Read without checking the signature: it only keeps one student's local copies apart from
 * another's on a shared computer (audit 2026-10-13); the server checks every request.
 */
export function tokenUserId(token: string | null): string | null {
  if (!token) return null;
  try {
    const payload = token.split(".")[0] ?? "";
    const b64 = payload.replace(/-/g, "+").replace(/_/g, "/");
    const uid = atob(b64 + "=".repeat((4 - (b64.length % 4)) % 4)).split(":")[0] ?? "";
    return /^\d{1,18}$/.test(uid) ? uid : null;
  } catch {
    return null;
  }
}

/** The signed-in student's account id, or null. */
export function currentStudentId(): string | null {
  return tokenUserId(getToken("student"));
}

export function clearToken(role: Role): void {
  try {
    localStorage.removeItem(tokenKey(role));
  } catch {
    /* storage unavailable */
  }
}

// ---------- errors ----------

export class ApiError extends Error {
  status: number;
  /** Always text: an object detail {code, message} (S4 "swap_needed") gives its message. */
  detail: string;
  /** S4: machine-readable reason from an object detail ("swap_needed", "already_submitted"). */
  code: string | null;
  /** S2: on 426, the oldest app version the server accepts. */
  minAppVersion: string | null;
  /** 2026-10-23: on 429, seconds until the next try is allowed (for a countdown). */
  retryAfter: number | null;
  constructor(
    status: number,
    detail: string,
    code: string | null = null,
    minAppVersion: string | null = null,
    retryAfter: number | null = null,
  ) {
    super(detail);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
    this.code = code;
    this.minAppVersion = minAppVersion;
    this.retryAfter = retryAfter;
  }
}

/** S4: start refused because the attempt is bound to another computer (409 swap_needed).
 *  deviceId = this computer's app device id (null in a browser: it can't take over). */
export class SwapNeededError extends ApiError {
  deviceId: string | null;
  constructor(detail: string, deviceId: string | null) {
    super(409, detail, "swap_needed");
    this.name = "SwapNeededError";
    this.deviceId = deviceId;
  }
}

/** S2: an app older than the server's minimum (426). App.tsx shows the update screen. */
export const UPGRADE_EVENT = "fairplay-upgrade-required";

function errorOf(status: number, body: unknown, fallback: string): ApiError {
  const b = body && typeof body === "object" ? (body as { detail?: unknown; min_app_version?: unknown }) : {};
  const d = b.detail;
  const code = d && typeof d === "object" && !Array.isArray(d) && typeof (d as { code?: unknown }).code === "string"
    ? (d as { code: string }).code
    : null;
  const min = typeof b.min_app_version === "string" ? b.min_app_version : null;
  const ra = d && typeof d === "object" && !Array.isArray(d) ? (d as { retry_after?: unknown }).retry_after : undefined;
  const retryAfter = typeof ra === "number" && Number.isFinite(ra) && ra > 0 ? Math.ceil(ra) : null;
  return new ApiError(status, detailOf(body, fallback), code, min, retryAfter);
}

/** Every /api request carries the app's version and platform once the bridge knows them. */
function withAppHeaders(headers: Record<string, string>): Record<string, string> {
  return { ...headers, ...bridge.appHeaders() };
}

/** Network failure (offline, server down). Callers treat it as "try again later". */
export class NetworkError extends Error {
  constructor(message = "Cannot reach the server") {
    super(message);
    this.name = "NetworkError";
  }
}

/** 0.6.2: the server answered "busy, try again" (503) or a proxy timed out (504). Still a
 *  NetworkError for every caller that retries later; callers that would otherwise go
 *  OFFLINE (starting an exam from the downloaded package) retry online instead. */
export class ServerBusyError extends NetworkError {
  constructor(message = "The server is busy, trying again") {
    super(message);
    this.name = "ServerBusyError";
  }
}

// ---------- 401 handling ----------

type UnauthorizedHandler = (role: Role) => void;

let onUnauthorized: UnauthorizedHandler = (role) => {
  window.location.assign(`/${role}/login`);
};

/** Override the 401 behaviour (the app uses router navigation; tests use a spy). */
export function setUnauthorizedHandler(handler: UnauthorizedHandler): void {
  onUnauthorized = handler;
}

// ---------- core request ----------

function detailOf(body: unknown, fallback: string): string {
  if (body && typeof body === "object" && "detail" in body) {
    const d = (body as { detail: unknown }).detail;
    if (typeof d === "string") return d;
    // S4: {"code": "swap_needed", "message": "..."} (code only: use the fallback)
    if (d && typeof d === "object" && !Array.isArray(d)) {
      const m = (d as { message?: unknown }).message;
      return typeof m === "string" && m ? m : fallback;
    }
    if (Array.isArray(d) && d.length > 0) {
      // FastAPI 422 validation list
      return d
        .map((e) => (e && typeof e === "object" && "msg" in e ? String((e as { msg: unknown }).msg) : String(e)))
        .join("; ");
    }
  }
  return fallback;
}

export async function request<T>(
  role: Role | null,
  method: string,
  path: string,
  body?: unknown,
): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const token = role ? getToken(role) : null;
  if (token) headers.Authorization = `Bearer ${token}`;
  if (role === "student") {
    // Audit #3: the attempt's app session goes along with every request for it.
    const aid = attemptIdOfPath(path);
    const sess = aid ? getAttemptSession(aid) : null;
    if (sess) headers["X-FairPlay-Session"] = sess;
  }

  let res: Response;
  try {
    res = await fetch(path, {
      method,
      headers: withAppHeaders(headers),
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new NetworkError();
  }

  let data: unknown = null;
  if (res.status !== 204) {
    const text = await res.text();
    if (text) {
      try {
        data = JSON.parse(text);
      } catch {
        data = text;
      }
    }
  }

  // 0.6.2: 503 is the server's "busy, try again" (admission line full for 10 s) and 504 a proxy
  // timeout: both mean "try again later", exactly like no network, so every caller's offline
  // path (queued saves, heartbeat retries, start retries) handles them.
  if (res.status === 503 || res.status === 504) throw new ServerBusyError();

  if (!res.ok) {
    if (res.status === 401 && role) {
      clearToken(role);
      onUnauthorized(role);
    }
    const err = errorOf(res.status, data, res.statusText || `Error ${res.status}`);
    if (res.status === 426) announceUpgrade(err.minAppVersion);
    throw err;
  }
  return data as T;
}

function announceUpgrade(minVersion: string | null): void {
  try {
    window.dispatchEvent(new CustomEvent(UPGRADE_EVENT, { detail: { min_app_version: minVersion } }));
  } catch {
    /* no window (tests outside jsdom) */
  }
}

const enc = encodeURIComponent;

// ---------- auth ----------

export const auth = {
  /** Does not send a token; a 401 here means wrong credentials, never a redirect. */
  login(username: string, password: string): Promise<LoginResponse> {
    return request<LoginResponse>(null, "POST", "/api/auth/login", { username, password });
  },
  me(role: Role): Promise<Me> {
    return request<Me>(role, "GET", "/api/me");
  },
};

// ---------- teacher ----------

/** ?course=…&class_id=… for the gradebook (2026-10-13: by class). */
function gbQuery(course: string, classId: number | null): string {
  const q = new URLSearchParams();
  if (course) q.set("course", course);
  if (classId != null) q.set("class_id", String(classId));
  const s = q.toString();
  return s ? `?${s}` : "";
}

export const teacherApi = {
  listExams: () => request<Exam[]>("teacher", "GET", "/api/exams"),
  allowedApps: () => request<AllowedAppOption[]>("teacher", "GET", "/api/exams/allowed-apps"),
  createExam: (input: ExamInput) => request<Exam>("teacher", "POST", "/api/exams", input),
  getExam: (id: string) => request<Exam>("teacher", "GET", `/api/exams/${enc(id)}`),
  updateExam: (id: string, input: ExamInput) => request<Exam>("teacher", "PUT", `/api/exams/${enc(id)}`, input),
  deleteExam: (id: string) => request<void>("teacher", "DELETE", `/api/exams/${enc(id)}`),
  /** openTo omitted = everyone (no body, as before). */
  openExam: (id: string, openTo?: OpenTo) =>
    request<Exam>("teacher", "POST", `/api/exams/${enc(id)}/open`, openTo === undefined ? undefined : { open_to: openTo }),
  /** 2026-10-23: the exam's students (class / course members or the external roster). */
  examStudents: (id: string) => request<ExamStudent[]>("teacher", "GET", `/api/exams/${enc(id)}/students`),
  /** 2026-10-23: change who may start: the whole selection, or students to add / remove. */
  updateOpenTo: (id: string, change: { open_to: OpenTo } | { add?: number[]; remove?: number[] }) =>
    request<Exam>("teacher", "PUT", `/api/exams/${enc(id)}/open-to`, change),
  closeExam: (id: string) => request<Exam>("teacher", "POST", `/api/exams/${enc(id)}/close`),
  /** Audit #13: new start code for a live exam. */
  rotateCode: (id: string) => request<Exam>("teacher", "POST", `/api/exams/${enc(id)}/rotate-code`),
  /** Let a student continue on another computer (the old app session stops working). */
  releaseDevice: (examId: string, aid: string) =>
    request<{ ok: boolean }>("teacher", "POST", `/api/exams/${enc(examId)}/attempts/${enc(aid)}/release-device`),
  live: (id: string) => request<LiveRoom>("teacher", "GET", `/api/exams/${enc(id)}/live`),
  /** S4: move the attempt to the computer the student asked from (like releaseDevice). */
  approveSwap: (examId: string, aid: string) =>
    request<{ ok: boolean }>("teacher", "POST", `/api/exams/${enc(examId)}/attempts/${enc(aid)}/approve-swap`),
  /** S3: let a student who came back after FairPlay closed continue (waiting time given back, max 15 min). */
  approveResume: (examId: string, aid: string) =>
    request<{ ok: boolean; minutes_added: number; deadline_at: string }>(
      "teacher", "POST", `/api/exams/${enc(examId)}/attempts/${enc(aid)}/approve-resume`),
  /** S1: each student's latest FairPlay device check. */
  readiness: (id: string) => request<ReadinessRow[]>("teacher", "GET", `/api/exams/${enc(id)}/readiness`),
  /** 8-digit exit code for one student's locked Mac that cannot reach the server
   *  (the Mac shows a Mac code). Only works on that Mac's current session; logged. */
  offlineExit: (id: string, aid: string, macCode: string) =>
    request<{ code: string }>("teacher", "POST", `/api/exams/${enc(id)}/attempts/${enc(aid)}/offline-exit`, {
      mac_code: macCode,
    }),
  results: (id: string) => request<Results>("teacher", "GET", `/api/exams/${enc(id)}/results`),
  extend: (id: string, minutes: number) =>
    request<Exam>("teacher", "POST", `/api/exams/${enc(id)}/extend`, { minutes }),
  reviewEvent: (id: string, eventId: number, status: Exclude<EventReview, "open">, note = "") =>
    request<{ event_id: number; review: EventReview; note: string }>(
      "teacher", "POST", `/api/exams/${enc(id)}/events/${eventId}/review`, { status, note }),
  grading: (id: string) => request<GradingQueue>("teacher", "GET", `/api/exams/${enc(id)}/grading`),
  saveGrade: (id: string, aid: string, qid: string, grade: GradeInput) =>
    request<GradeResponse>("teacher", "PUT", `/api/exams/${enc(id)}/attempts/${enc(aid)}/grades/${enc(qid)}`, grade),
  clearGrade: (id: string, aid: string, qid: string) =>
    request<GradeResponse>("teacher", "DELETE", `/api/exams/${enc(id)}/attempts/${enc(aid)}/grades/${enc(qid)}`),
  saveComment: (id: string, aid: string, comment: string) =>
    request<{ attempt_id: string; overall_comment: string }>(
      "teacher", "PUT", `/api/exams/${enc(id)}/attempts/${enc(aid)}/comment`, { comment }),
  release: (id: string, force = false) =>
    request<Exam>("teacher", "POST", `/api/exams/${enc(id)}/release`, { force }),
  unrelease: (id: string) => request<Exam>("teacher", "POST", `/api/exams/${enc(id)}/unrelease`),
  gradingSummary: () => request<GradingSummary[]>("teacher", "GET", "/api/grading"),
  gradebook: (course = "", classId: number | null = null) =>
    request<Gradebook>("teacher", "GET", `/api/gradebook${gbQuery(course, classId)}`),
  /** Downloads the CSV with the auth header and returns it as a Blob. */
  async gradebookCsv(course = "", classId: number | null = null): Promise<Blob> {
    const token = getToken("teacher");
    let res: Response;
    try {
      res = await fetch(`/api/gradebook.csv${gbQuery(course, classId)}`, {
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      });
    } catch {
      throw new NetworkError();
    }
    if (!res.ok) throw new ApiError(res.status, res.statusText || `Error ${res.status}`);
    return res.blob();
  },
};

// ---------- student ----------

/**
 * POST start (audit #1): inside the FairPlay app it first gets a single-use challenge and
 * has the app sign it; in a browser it sends no proof (the server refuses unless
 * FAIRPLAY_REQUIRE_APP=0). Stores the returned app session for the attempt.
 */
async function startAttempt(
  examId: string,
  startCode: string,
  offlineStartedAt?: string,
  trial = false,
): Promise<AttemptView> {
  // 2026-10-22: the sample exam has its own challenge/start (no class, no start code) but
  // the same app proof, session and lock as a real exam.
  const base = trial ? "/api/student/trial" : `/api/student/exams/${enc(examId)}`;
  const body: Record<string, unknown> = trial ? {} : { start_code: startCode };
  if (offlineStartedAt) body.offline_started_at = offlineStartedAt;
  let deviceId: string | null = null;
  if (bridge.isNative()) {
    const ch = await request<{ nonce: string }>("student", "GET", `${base}/challenge`);
    const proof = await bridge.attest(ch.nonce);
    if (proof) {
      body.client = proof;
      deviceId = proof.device_id;
    }
  }
  let view: AttemptView;
  try {
    view = await request<AttemptView>("student", "POST", `${base}/start`, body);
  } catch (e) {
    // S4: bound to another computer; this one can ask to take over (swap-request).
    if (e instanceof ApiError && e.code === "swap_needed") throw new SwapNeededError(e.detail, deviceId);
    throw e;
  }
  if (view.session) {
    setAttemptSession(view.attempt_id, view.session);
    const token = getToken("student");
    if (token) bridge.setExamSession({ attempt_id: view.attempt_id, token, session: view.session });
  }
  return view;
}

export const studentApi = {
  listExams: () => request<StudentExamSummary[]>("student", "GET", "/api/student/exams"),
  start: (examId: string, startCode: string) => startAttempt(examId, startCode),
  getAttempt: (aid: string) => request<AttemptView>("student", "GET", `/api/student/attempts/${enc(aid)}`),
  saveAnswers: (aid: string, answers: AnswerMap, clientSeq: number) =>
    request<SaveAnswersResponse>("student", "PUT", `/api/student/attempts/${enc(aid)}/answers`, {
      answers,
      client_seq: clientSeq,
    }),
  /** `locked` is informational only: the server takes lock state from the app's signed beats. */
  heartbeat: (aid: string, currentQuestion: number, locked: boolean, battery?: { level: number; charging: boolean } | null) =>
    request<HeartbeatResponse>("student", "POST", `/api/student/attempts/${enc(aid)}/heartbeat`, {
      current_question: currentQuestion,
      locked,
      // 2026-10-23: only when known (the server shows low batteries to the invigilator)
      ...(battery && battery.level >= 0 && battery.level <= 1
        ? { battery: Math.round(battery.level * 100) / 100, charging: battery.charging }
        : {}),
    }),
  events: (aid: string, events: IntegrityEvent[]) =>
    request<EventsResponse>("student", "POST", `/api/student/attempts/${enc(aid)}/events`, { events }),
  submit: (aid: string, answers: AnswerMap, clientSeq: number) =>
    request<SubmitResponse>("student", "POST", `/api/student/attempts/${enc(aid)}/submit`, {
      answers,
      client_seq: clientSeq,
    }),
  grades: () => request<StudentGradeSummary[]>("student", "GET", "/api/student/grades"),
  /** 2026-10-22: the sample exam ("Try a sample exam"). */
  trialInfo: () => request<TrialInfo>("student", "GET", "/api/student/trial"),
  startTrial: () => startAttempt("", "", undefined, true),
  /** "Stop practice": ends the sample attempt (refused for real exams); then unlock. */
  trialStop: (aid: string) =>
    request<{ status: string; submitted_at: string | null }>("student", "POST", `/api/student/attempts/${enc(aid)}/trial-stop`),
  /** Sample exam only: its offline exit key, kept in memory for an exit without the server. */
  trialKey: (aid: string) => request<{ key: string }>("student", "GET", `/api/student/attempts/${enc(aid)}/trial-key`),
  /** S4: ask the teacher to move the attempt to this computer (202). */
  swapRequest: (aid: string, deviceId: string) =>
    request<{ ok: boolean }>("student", "POST", `/api/student/attempts/${enc(aid)}/swap-request`, { device_id: deviceId }),
  /** S1: store this computer's device check so the teacher sees it before the exam. */
  deviceCheck: (body: DeviceCheckInput) =>
    request<{ ok: boolean; saved_at: string }>("student", "POST", "/api/student/device-check", body),
  feedback: (examId: string) =>
    request<StudentFeedback>("student", "GET", `/api/student/grades/${enc(examId)}`),
};

// ---------- teacher: LMS sync (slice 5) ----------

export const lmsApi = {
  providers: () => request<LmsProvider[]>("teacher", "GET", "/api/lms/providers"),
  connections: () => request<LmsConnection[]>("teacher", "GET", "/api/lms/connections"),
  connect: (input: LmsConnectionInput) =>
    request<LmsConnection>("teacher", "POST", "/api/lms/connections", input),
  disconnect: (id: number) => request<void>("teacher", "DELETE", `/api/lms/connections/${id}`),
  courses: (connectionId: number) =>
    request<LmsCourse[]>("teacher", "GET", `/api/lms/connections/${connectionId}/courses`),
  linkCourse: (connectionId: number, lmsCourseId: string) =>
    request<LmsLinkResponse>(
      "teacher", "POST", `/api/lms/connections/${connectionId}/courses/${enc(lmsCourseId)}/link`),
  resync: (linkId: number) => request<LmsLinkResponse>("teacher", "POST", `/api/lms/courses/${linkId}/sync`),
  linkedCourses: () => request<LmsCourseLink[]>("teacher", "GET", "/api/lms/courses"),
  linkExam: (examId: string, courseLinkId: number | null) =>
    request<Exam>("teacher", "PUT", `/api/exams/${enc(examId)}/lms`, { course_link_id: courseLinkId }),
  pushGrades: (examId: string) =>
    request<LmsPushResult>("teacher", "POST", `/api/exams/${enc(examId)}/lms/push`),
};

// ---------- slice 6: uploads, topics, offline packages ----------

import type { OfflinePackage, TopicCount, UploadResult } from "./types";

export const slice6Api = {
  /** POST /api/uploads (multipart). PNG/JPEG/GIF/WebP up to 5 MB. */
  async uploadImage(file: Blob, filename = "image"): Promise<UploadResult> {
    const token = getToken("teacher");
    const form = new FormData();
    form.append("file", file, filename);
    let res: Response;
    try {
      res = await fetch("/api/uploads", {
        method: "POST",
        headers: token ? { Authorization: `Bearer ${token}`, Accept: "application/json" } : { Accept: "application/json" },
        body: form,
      });
    } catch {
      throw new NetworkError();
    }
    let data: unknown = null;
    try {
      data = await res.json();
    } catch {
      data = null;
    }
    if (!res.ok) {
      if (res.status === 401) {
        clearToken("teacher");
        onUnauthorized("teacher");
      }
      throw new ApiError(res.status, detailOf(data, res.statusText || `Error ${res.status}`));
    }
    return data as UploadResult;
  },
  topics: () => request<TopicCount[]>("teacher", "GET", "/api/topics"),
  examPackage: (examId: string) =>
    request<OfflinePackage>("student", "GET", `/api/student/exams/${enc(examId)}/package`),
  /** Audit S-03: second half of the package key, only while the exam is live (class members only). */
  packageKey: (examId: string) =>
    request<{ exam_id: string; key: string }>("student", "GET", `/api/student/exams/${enc(examId)}/package-key`),
  /** Start (or resume) after working offline: the server sets the deadline from offline_started_at. */
  startAfterOffline: (examId: string, startCode: string, offlineStartedAt: string) =>
    startAttempt(examId, startCode, offlineStartedAt),
};

// ---------- slice 6 F/G: class insights + student profiles ----------

import type { Insights, StudentProfile, StudentSummary } from "./types";

function query(params: Record<string, string | undefined>): string {
  const parts = Object.entries(params)
    .filter(([, v]) => v)
    .map(([k, v]) => `${k}=${enc(v as string)}`);
  return parts.length ? `?${parts.join("&")}` : "";
}

export const insightsApi = {
  insights: (course = "", examIds: string[] = []) =>
    request<Insights>("teacher", "GET", `/api/insights${query({ course, exam_ids: examIds.join(",") })}`),
  students: (course = "") => request<StudentSummary[]>("teacher", "GET", `/api/students${query({ course })}`),
  profile: (username: string, course = "") =>
    request<StudentProfile>("teacher", "GET", `/api/students/${enc(username)}/profile${query({ course })}`),
};

// ---------- slice 6 H: practice assignments ----------

import type {
  PracticeAnswerResponse,
  PracticeBankQuestion,
  PracticeGenerateInput,
  PracticeGenerateResult,
  PracticeItemEdit,
  PracticeSet,
  PracticeSetSummary,
  PracticeStudents,
  StudentPracticeSet,
  StudentPracticeSummary,
} from "./types";

export const practiceApi = {
  students: () => request<PracticeStudents>("teacher", "GET", "/api/practice/students"),
  bank: (topic = "") => request<PracticeBankQuestion[]>("teacher", "GET", `/api/practice/bank${topic ? `?topic=${enc(topic)}` : ""}`),
  generate: (body: PracticeGenerateInput) => request<PracticeGenerateResult>("teacher", "POST", "/api/practice/generate", body),
  sets: (status = "") => request<PracticeSetSummary[]>("teacher", "GET", `/api/practice/sets${status ? `?status=${enc(status)}` : ""}`),
  set: (id: string) => request<PracticeSet>("teacher", "GET", `/api/practice/sets/${enc(id)}`),
  deleteSet: (id: string) => request<void>("teacher", "DELETE", `/api/practice/sets/${enc(id)}`),
  editItem: (id: string, itemId: string, body: PracticeItemEdit) =>
    request<PracticeSet>("teacher", "PUT", `/api/practice/sets/${enc(id)}/items/${enc(itemId)}`, body),
  removeItem: (id: string, itemId: string) =>
    request<PracticeSet>("teacher", "DELETE", `/api/practice/sets/${enc(id)}/items/${enc(itemId)}`),
  addItem: (id: string, examId: string, questionId: string) =>
    request<PracticeSet>("teacher", "POST", `/api/practice/sets/${enc(id)}/items`, { exam_id: examId, question_id: questionId }),
  approve: (id: string) => request<PracticeSet>("teacher", "POST", `/api/practice/sets/${enc(id)}/approve`),
  send: (id: string) => request<PracticeSet>("teacher", "POST", `/api/practice/sets/${enc(id)}/send`),
  // student
  mySets: () => request<StudentPracticeSummary[]>("student", "GET", "/api/student/practice"),
  mySet: (id: string) => request<StudentPracticeSet>("student", "GET", `/api/student/practice/${enc(id)}`),
  answer: (id: string, itemId: string, answer: unknown) =>
    request<PracticeAnswerResponse>("student", "POST", `/api/student/practice/${enc(id)}/items/${enc(itemId)}/answer`, { answer }),
};

// ---------- audit 2026-10-12 S-03: classes ----------

import type { AddMembersResult, ClassDetail, SchoolClass, StudentClass } from "./types";

export const classesApi = {
  list: () => request<SchoolClass[]>("teacher", "GET", "/api/classes"),
  create: (name: string, term = "") => request<SchoolClass>("teacher", "POST", "/api/classes", { name, term }),
  get: (id: number) => request<ClassDetail>("teacher", "GET", `/api/classes/${id}`),
  rename: (id: number, name: string) => request<SchoolClass>("teacher", "PUT", `/api/classes/${id}`, { name }),
  update: (id: number, name: string, term: string) =>
    request<SchoolClass>("teacher", "PUT", `/api/classes/${id}`, { name, term }),
  newJoinCode: (id: number) => request<SchoolClass>("teacher", "POST", `/api/classes/${id}/join-code`),
  addMembers: (id: number, usernames: string[]) =>
    request<AddMembersResult>("teacher", "POST", `/api/classes/${id}/members`, { usernames }),
  removeMember: (id: number, username: string) =>
    request<void>("teacher", "DELETE", `/api/classes/${id}/members/${enc(username)}`),
  /** Audit S-15: sign the student out on every device. */
  signOutMember: (id: number, username: string) =>
    request<{ ok: boolean }>("teacher", "POST", `/api/classes/${id}/members/${enc(username)}/sign-out`),
  remove: (id: number) => request<void>("teacher", "DELETE", `/api/classes/${id}`),
};

export const studentClassesApi = {
  list: () => request<StudentClass[]>("student", "GET", "/api/student/classes"),
  join: (code: string) => request<StudentClass>("student", "POST", "/api/student/classes/join", { code }),
  leave: (id: number) => request<void>("student", "DELETE", `/api/student/classes/${id}`),
};

// ---------- 2026-10-23 (exam tools): open-book materials ----------

import type { MaterialFile, MaterialsInfo, StudentMaterials } from "./types";

/** GET a file's bytes (the student's attempt session goes along, like request()). */
async function fetchBlob(role: Role, path: string): Promise<Blob> {
  const headers: Record<string, string> = {};
  const token = getToken(role);
  if (token) headers.Authorization = `Bearer ${token}`;
  if (role === "student") {
    const aid = attemptIdOfPath(path);
    const sess = aid ? getAttemptSession(aid) : null;
    if (sess) headers["X-FairPlay-Session"] = sess;
  }
  let res: Response;
  try {
    res = await fetch(path, { headers: withAppHeaders(headers) });
  } catch {
    throw new NetworkError();
  }
  if (!res.ok) {
    let data: unknown = null;
    try {
      data = await res.json();
    } catch {
      data = null;
    }
    if (res.status === 401) {
      clearToken(role);
      onUnauthorized(role);
    }
    throw errorOf(res.status, data, res.statusText || `Error ${res.status}`);
  }
  return res.blob();
}

export const materialsApi = {
  /** Own files + limits for an exam (before it starts). */
  list: (examId: string) => request<MaterialsInfo>("student", "GET", `/api/student/exams/${enc(examId)}/materials`),
  /** POST multipart "file". PDF / PNG / JPEG / TXT / MD, checked by the server from the bytes. */
  async upload(examId: string, file: File): Promise<MaterialFile> {
    const token = getToken("student");
    const form = new FormData();
    form.append("file", file, file.name);
    let res: Response;
    try {
      res = await fetch(`/api/student/exams/${enc(examId)}/materials`, {
        method: "POST",
        headers: withAppHeaders(token ? { Authorization: `Bearer ${token}`, Accept: "application/json" } : { Accept: "application/json" }),
        body: form,
      });
    } catch {
      throw new NetworkError();
    }
    let data: unknown = null;
    try {
      data = await res.json();
    } catch {
      data = null;
    }
    if (!res.ok) {
      if (res.status === 401) {
        clearToken("student");
        onUnauthorized("student");
      }
      throw errorOf(res.status, data, res.statusText || `Error ${res.status}`);
    }
    return data as MaterialFile;
  },
  remove: (examId: string, id: string) =>
    request<void>("student", "DELETE", `/api/student/exams/${enc(examId)}/materials/${enc(id)}`),
  /** During the attempt. */
  attemptFiles: (aid: string) =>
    request<{ files: MaterialFile[] }>("student", "GET", `/api/student/attempts/${enc(aid)}/materials`),
  attemptFile: (aid: string, id: string) => fetchBlob("student", `/api/student/attempts/${enc(aid)}/materials/${enc(id)}`),
  // teacher (read only)
  examMaterials: (examId: string) => request<StudentMaterials[]>("teacher", "GET", `/api/exams/${enc(examId)}/materials`),
  submissionFiles: (examId: string, aid: string) =>
    request<{ files: MaterialFile[] }>("teacher", "GET", `/api/exams/${enc(examId)}/attempts/${enc(aid)}/materials`),
  teacherFile: (examId: string, id: string) => fetchBlob("teacher", `/api/exams/${enc(examId)}/materials/${enc(id)}`),
};

/** 2026-10-23 (review): can an allowed website be shown inside the exam's websites pane?
 *  frameable null = could not be checked. */
export interface SiteCheck {
  host: string;
  frameable: boolean | null;
  reason: string;
}

export const toolsApi = {
  siteCheck: (host: string) =>
    request<SiteCheck>("teacher", "GET", `/api/tools/site-check?host=${encodeURIComponent(host)}`),
};

// ---------- question content, answer files, external live-room sign-in ----------

import type { AnswerUpload, ExternalLiveSignIn } from "./types";

/** Server answers for an uploaded file, read leniently (id / upload_id, name / filename, size / bytes). */
function toAnswerUpload(data: unknown, file: File): AnswerUpload {
  const d = (data && typeof data === "object" ? data : {}) as Record<string, unknown>;
  const id = typeof d.id === "string" ? d.id : typeof d.upload_id === "string" ? d.upload_id : "";
  if (!id) throw new ApiError(502, "The server did not return the uploaded file.");
  const name = typeof d.name === "string" ? d.name : typeof d.filename === "string" ? d.filename : file.name;
  const size = typeof d.size === "number" ? d.size : typeof d.bytes === "number" ? d.bytes : file.size;
  const out: AnswerUpload = { id, name, size };
  if (typeof d.question_id === "string") out.question_id = d.question_id;
  if (typeof d.sha256 === "string") out.sha256 = d.sha256;
  if (typeof d.created_at === "string") out.created_at = d.created_at;
  return out;
}

export const contentApi = {
  /** One-time code from an integration's "Open live room" link -> a teacher sign-in. */
  liveExchange: (code: string) => request<ExternalLiveSignIn>(null, "POST", "/api/integrations/live/exchange", { code }),
  /** A file the student handed in (bytes). */
  answerFile: (aid: string, id: string) => fetchBlob("student", `/api/student/attempts/${enc(aid)}/files/${enc(id)}`),
  /** A picture (fp-asset:) of a question during the attempt (attempt owner, exam live). */
  attemptAsset: (aid: string, name: string) =>
    fetchBlob("student", `/api/student/attempts/${enc(aid)}/assets/${enc(name)}`),
  /** A picture of a released grade page (after the exam: sign-in only). */
  gradeAsset: (examId: string, name: string) =>
    fetchBlob("student", `/api/student/grades/${enc(examId)}/assets/${enc(name)}`),
  /** The same picture for the teacher. */
  teacherAsset: (examId: string, name: string) => fetchBlob("teacher", `/api/exams/${enc(examId)}/assets/${enc(name)}`),
  /** POST multipart "file" to .../files?question_id=: a file for a file_upload question
   *  (extension / size checked by the server too). The answer keeps the returned id; the
   *  server passes it on to an external source after the hand-in when there is one. */
  async uploadAnswerFile(aid: string, questionId: string, file: File): Promise<AnswerUpload> {
    const path = `/api/student/attempts/${enc(aid)}/files?question_id=${enc(questionId)}`;
    const headers: Record<string, string> = { Accept: "application/json" };
    const token = getToken("student");
    if (token) headers.Authorization = `Bearer ${token}`;
    const sess = getAttemptSession(aid);
    if (sess) headers["X-FairPlay-Session"] = sess;
    const form = new FormData();
    form.append("file", file, file.name);
    let res: Response;
    try {
      res = await fetch(path, { method: "POST", headers: withAppHeaders(headers), body: form });
    } catch {
      throw new NetworkError();
    }
    let data: unknown = null;
    try {
      data = await res.json();
    } catch {
      data = null;
    }
    if (!res.ok) {
      if (res.status === 401) {
        clearToken("student");
        onUnauthorized("student");
      }
      throw errorOf(res.status, data, res.statusText || `Error ${res.status}`);
    }
    return toAnswerUpload(data, file);
  },
};
