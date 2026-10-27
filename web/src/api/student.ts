import type {
  AnswerMap,
  AnswerUpload,
  AttemptView,
  DeviceCheckInput,
  EventsResponse,
  HeartbeatResponse,
  IntegrityEvent,
  LoginResponse,
  MaterialFile,
  MaterialsInfo,
  OfflinePackage,
  PracticeAnswerResponse,
  SaveAnswersResponse,
  SetupIdentifyResponse,
  StudentClass,
  StudentExamSummary,
  StudentFeedback,
  StudentGradeSummary,
  StudentPracticeSet,
  StudentPracticeSummary,
  SubmitResponse,
  TrialInfo,
} from "./types";
import * as bridge from "../lib/bridge";
import { attemptIdOfPath, getAttemptSession, setAttemptSession } from "../lib/appSession";
import {
  ApiError,
  NetworkError,
  SwapNeededError,
  announceUpgrade,
  clearToken,
  errorOf,
  fetchBlob,
  getToken,
  handleUnauthorized,
  request,
  setHeaderProvider,
  withAppHeaders,
} from "./client";

const enc = encodeURIComponent;

/** X-FairPlay-Version / X-FairPlay-Platform for every /api request, once the app has
 *  reported both in its "state" message ({} before that). */
export function appHeaders(): Record<string, string> {
  const st = bridge.getNativeState();
  if (!st?.app_version || !st.platform) return {};
  return { "X-FairPlay-Version": st.app_version, "X-FairPlay-Platform": st.platform };
}

// Audit #3: the attempt's app session goes along with every request for it.
function requestHeaders(path: string): Record<string, string> {
  const aid = attemptIdOfPath(path);
  const sess = aid ? getAttemptSession(aid) : null;
  return { ...appHeaders(), ...(sess ? { "X-FairPlay-Session": sess } : {}) };
}

setHeaderProvider(requestHeaders);

/**
 * A call made with a one-off set-up token. The token is passed in and never stored (no
 * setToken, no localStorage/sessionStorage); every status, 401 and 503 included, comes back
 * as an ApiError so the set-up screens can say what happened (no sign-in redirect).
 */
async function setupRequest<T>(bearer: string, path: string, body: unknown): Promise<T> {
  const headers: Record<string, string> = {
    Accept: "application/json",
    "Content-Type": "application/json",
    Authorization: `Bearer ${bearer}`,
  };
  let res: Response;
  try {
    res = await fetch(path, { method: "POST", headers: withAppHeaders(headers, path), body: JSON.stringify(body) });
  } catch {
    throw new NetworkError();
  }
  let data: unknown = null;
  const text = await res.text();
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = text;
    }
  }
  if (!res.ok) {
    const err = errorOf(res.status, data, res.statusText || `Error ${res.status}`);
    if (res.status === 426) announceUpgrade(err.minAppVersion);
    throw err;
  }
  return data as T;
}

export const setupApi = {
  /** Step 1: who the student is, checked against the class list (setup token). */
  identify: (setupToken: string, input: { first_name: string; last_name: string; netid: string }) =>
    setupRequest<SetupIdentifyResponse>(setupToken, "/api/setup/identify", input),
  /** Step 2: the student's own login (identity token, single use) → the normal login response. */
  account: (identityToken: string, input: { username: string; password: string; password2: string }) =>
    setupRequest<LoginResponse>(identityToken, "/api/setup/account", input),
};

/**
 * POST start (audit #1): inside the FairPlay app it first gets a single-use challenge and
 * has the app sign it; in a browser it sends no proof (the server refuses unless
 * FAIRPLAY_REQUIRE_APP=0). Stores the returned app session for the attempt.
 */
export async function startAttempt(
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

export const offlineApi = {
  examPackage: (examId: string) =>
    request<OfflinePackage>("student", "GET", `/api/student/exams/${enc(examId)}/package`),
  /** Audit S-03: second half of the package key, only while the exam is live (class members only). */
  packageKey: (examId: string) =>
    request<{ exam_id: string; key: string }>("student", "GET", `/api/student/exams/${enc(examId)}/package-key`),
  /** Start (or resume) after working offline: the server sets the deadline from offline_started_at. */
  startAfterOffline: (examId: string, startCode: string, offlineStartedAt: string) =>
    startAttempt(examId, startCode, offlineStartedAt),
};

export const studentPracticeApi = {
  mySets: () => request<StudentPracticeSummary[]>("student", "GET", "/api/student/practice"),
  mySet: (id: string) => request<StudentPracticeSet>("student", "GET", `/api/student/practice/${enc(id)}`),
  answer: (id: string, itemId: string, answer: unknown) =>
    request<PracticeAnswerResponse>("student", "POST", `/api/student/practice/${enc(id)}/items/${enc(itemId)}/answer`, { answer }),
};

export const studentClassesApi = {
  list: () => request<StudentClass[]>("student", "GET", "/api/student/classes"),
  join: (code: string) => request<StudentClass>("student", "POST", "/api/student/classes/join", { code }),
  leave: (id: number | string) => request<void>("student", "DELETE", `/api/student/classes/${encodeURIComponent(String(id))}`),
};

export const studentMaterialsApi = {
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
        headers: withAppHeaders(token ? { Authorization: `Bearer ${token}`, Accept: "application/json" } : { Accept: "application/json" }, `/api/student/exams/${enc(examId)}/materials`),
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
        handleUnauthorized("student");
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
};

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

export const studentContentApi = {
  /** A file the student handed in (bytes). */
  answerFile: (aid: string, id: string) => fetchBlob("student", `/api/student/attempts/${enc(aid)}/files/${enc(id)}`),
  /** A picture (fp-asset:) of a question during the attempt (attempt owner, exam live). */
  attemptAsset: (aid: string, name: string) =>
    fetchBlob("student", `/api/student/attempts/${enc(aid)}/assets/${enc(name)}`),
  /** A picture of a released grade page (after the exam: sign-in only). */
  gradeAsset: (examId: string, name: string) =>
    fetchBlob("student", `/api/student/grades/${enc(examId)}/assets/${enc(name)}`),
  /** POST multipart "file" to .../files?question_id=: a file for a file_upload question
   *  (extension / size checked by the server too). The answer keeps the returned id; the
   *  server passes it on to an external source after the hand-in when there is one. */
  async uploadAnswerFile(aid: string, questionId: string, file: File): Promise<AnswerUpload> {
    const path = `/api/student/attempts/${enc(aid)}/files?question_id=${enc(questionId)}`;
    const headers: Record<string, string> = { Accept: "application/json" };
    const token = getToken("student");
    if (token) headers.Authorization = `Bearer ${token}`;
    const form = new FormData();
    form.append("file", file, file.name);
    let res: Response;
    try {
      res = await fetch(path, { method: "POST", headers: withAppHeaders(headers, path), body: form });
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
        handleUnauthorized("student");
      }
      throw errorOf(res.status, data, res.statusText || `Error ${res.status}`);
    }
    return toAnswerUpload(data, file);
  },
};
