import type { AllowedAppOption,
  ReadinessRow,
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
  LiveRoom,
  LoginResult,
  Me,
  Results,
  Role,
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

/** The exam a code sign-in is tied to (6th field of the token), or null for a normal sign-in.
 *  Read without checking the signature, like tokenUserId. */
export function tokenScope(token: string | null): string | null {
  if (!token) return null;
  try {
    const payload = token.split(".")[0] ?? "";
    const b64 = payload.replace(/-/g, "+").replace(/_/g, "/");
    const parts = atob(b64 + "=".repeat((4 - (b64.length % 4)) % 4)).split(":");
    return parts.length > 5 && parts[5] ? parts[5] : null;
  } catch {
    return null;
  }
}

let onScopedEnd: (() => void) | null = null;

/** Registered by lib/studentSession: wipes the student's local state when a code sign-in ends. */
export function setScopedEndHandler(fn: (() => void) | null): void {
  onScopedEnd = fn;
}

export function clearToken(role: Role): void {
  if (role === "student" && tokenScope(getToken("student"))) onScopedEnd?.();
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

export function errorOf(status: number, body: unknown, fallback: string): ApiError {
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

let headerProvider: (path: string) => Record<string, string> = () => ({});

/** The student side registers its extra request headers (app version, platform, attempt
 *  session) when it loads. */
export function setHeaderProvider(fn: (path: string) => Record<string, string>): void {
  headerProvider = fn;
}

export function withAppHeaders(headers: Record<string, string>, path: string): Record<string, string> {
  return { ...headers, ...headerProvider(path) };
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

export function handleUnauthorized(role: Role): void {
  onUnauthorized(role);
}

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

  let res: Response;
  try {
    res = await fetch(path, {
      method,
      headers: withAppHeaders(headers, path),
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

export function announceUpgrade(minVersion: string | null): void {
  try {
    window.dispatchEvent(new CustomEvent(UPGRADE_EVENT, { detail: { min_app_version: minVersion } }));
  } catch {
    /* no window (tests outside jsdom) */
  }
}

const enc = encodeURIComponent;

// ---------- auth ----------

export const auth = {
  /** Does not send a token; a 401 here means wrong credentials, never a redirect.
   *  Bridge v2: a class login answers {setup_token, class} instead of a session. */
  login(username: string, password: string): Promise<LoginResult> {
    return request<LoginResult>(null, "POST", "/api/auth/login", { username, password });
  },
  me(role: Role): Promise<Me> {
    return request<Me>(role, "GET", "/api/me");
  },
};

// ---------- student side (separate chunk: the calls load src/api/student.ts on first use) ----------

type Fn = (...args: never[]) => Promise<unknown>;
type StudentModule = typeof import("./student");

function lazy<F extends Fn>(pick: (m: StudentModule) => F): F {
  return ((...args: unknown[]) =>
    import("./student").then((m) => (pick(m) as unknown as (...a: unknown[]) => unknown)(...args))) as unknown as F;
}

export const codeLoginApi = {
  challenge: lazy((m) => m.codeLoginApi.challenge),
  enter: lazy((m) => m.codeLoginApi.enter),
  confirm: lazy((m) => m.codeLoginApi.confirm),
};

export const setupApi = {
  identify: lazy((m) => m.setupApi.identify),
  account: lazy((m) => m.setupApi.account),
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

export const studentApi = {
  listExams: lazy((m) => m.studentApi.listExams),
  start: lazy((m) => m.studentApi.start),
  getAttempt: lazy((m) => m.studentApi.getAttempt),
  saveAnswers: lazy((m) => m.studentApi.saveAnswers),
  heartbeat: lazy((m) => m.studentApi.heartbeat),
  events: lazy((m) => m.studentApi.events),
  submit: lazy((m) => m.studentApi.submit),
  grades: lazy((m) => m.studentApi.grades),
  trialInfo: lazy((m) => m.studentApi.trialInfo),
  startTrial: lazy((m) => m.studentApi.startTrial),
  trialStop: lazy((m) => m.studentApi.trialStop),
  trialKey: lazy((m) => m.studentApi.trialKey),
  swapRequest: lazy((m) => m.studentApi.swapRequest),
  deviceCheck: lazy((m) => m.studentApi.deviceCheck),
  feedback: lazy((m) => m.studentApi.feedback),
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

import type { TopicCount, UploadResult } from "./types";

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
  examPackage: lazy((m) => m.offlineApi.examPackage),
  packageKey: lazy((m) => m.offlineApi.packageKey),
  startAfterOffline: lazy((m) => m.offlineApi.startAfterOffline),
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
  PracticeBankQuestion,
  PracticeGenerateInput,
  PracticeGenerateResult,
  PracticeItemEdit,
  PracticeSet,
  PracticeSetSummary,
  PracticeStudents,
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
  mySets: lazy((m) => m.studentPracticeApi.mySets),
  mySet: lazy((m) => m.studentPracticeApi.mySet),
  answer: lazy((m) => m.studentPracticeApi.answer),
};

// ---------- audit 2026-10-12 S-03: classes ----------

import type { AddMembersResult, ClassDetail, SchoolClass } from "./types";

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
  list: lazy((m) => m.studentClassesApi.list),
  join: lazy((m) => m.studentClassesApi.join),
  leave: lazy((m) => m.studentClassesApi.leave),
};

// ---------- 2026-10-23 (exam tools): open-book materials ----------

import type { MaterialFile, StudentMaterials } from "./types";

/** GET a file's bytes (the student's attempt session goes along, like request()). */
export async function fetchBlob(role: Role, path: string): Promise<Blob> {
  const headers: Record<string, string> = {};
  const token = getToken(role);
  if (token) headers.Authorization = `Bearer ${token}`;
  let res: Response;
  try {
    res = await fetch(path, { headers: withAppHeaders(headers, path) });
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
  list: lazy((m) => m.studentMaterialsApi.list),
  upload: lazy((m) => m.studentMaterialsApi.upload),
  remove: lazy((m) => m.studentMaterialsApi.remove),
  attemptFiles: lazy((m) => m.studentMaterialsApi.attemptFiles),
  attemptFile: lazy((m) => m.studentMaterialsApi.attemptFile),
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
  /** 2026-10-24: sites the lock also opens so this one works (Duke Box), or null */
  companions?: { name: string; sites: string[] } | null;
}

export const toolsApi = {
  siteCheck: (host: string) =>
    request<SiteCheck>("teacher", "GET", `/api/tools/site-check?host=${encodeURIComponent(host)}`),
};

// ---------- question content, answer files ----------

export const contentApi = {
  answerFile: lazy((m) => m.studentContentApi.answerFile),
  attemptAsset: lazy((m) => m.studentContentApi.attemptAsset),
  gradeAsset: lazy((m) => m.studentContentApi.gradeAsset),
  /** The same picture for the teacher. */
  teacherAsset: (examId: string, name: string) => fetchBlob("teacher", `/api/exams/${enc(examId)}/assets/${enc(name)}`),
  uploadAnswerFile: lazy((m) => m.studentContentApi.uploadAnswerFile),
};
