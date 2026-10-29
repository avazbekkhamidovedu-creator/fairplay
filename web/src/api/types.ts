// Types mirror CONTRACT.md exactly. Do not add fields that are not in the contract.

export type Role = "teacher" | "student";
export type QuestionType = "mcq" | "numeric" | "short" | "essay" | "external" | "code" | "fill_blank" | "file_upload";
export type ExamStatus = "draft" | "live" | "closed";
export type AttemptStatus = "in_progress" | "submitted";
/** Slice 3: where students answer — in FairPlay or on another website (SEB-style). */
export type ExamMode = "questions" | "external";
/** Canvas mode: an external exam whose quiz is a Canvas quiz or assignment. */
export type ExternalKind = "canvas";
export type CanvasQuizType = "classic" | "new" | "assignment";

/** The course and item a Canvas exam link points at (derived by the server from the link). */
export interface CanvasScope {
  course_id: string;
  item_kind: "quizzes" | "assignments";
  item_id: string;
}

/** CanvasScope plus the teacher's open-book choice, as the app receives it. */
export interface CanvasScopeView extends CanvasScope {
  open_book: boolean;
  /** Sign-in sites in force for this exam (exact hosts or *.host), the teacher's removals left out. */
  sign_in_hosts?: string[];
  /** New Quizzes hosts in force for this exam (empty for Classic Quizzes and assignments). */
  quiz_lti_hosts?: string[];
}

/** Canvas settings as the server returns them. The access code is never returned. */
export interface CanvasInfo {
  quiz_type: CanvasQuizType;
  /** null when the link is not a plain quiz or assignment link. */
  scope?: CanvasScope | null;
  /** Students may also open the course's pages, modules and files. */
  open_book?: boolean;
  /** Teacher view: sign-in sites the teacher removed, and the sites the server adds by default. */
  removed_hosts?: string[];
  sign_in_hosts?: string[];
}

/** Canvas settings as the teacher sends them. */
export interface CanvasInput {
  quiz_type: CanvasQuizType;
  /** Automatic sign-in sites the teacher removed. */
  removed_hosts?: string[];
  open_book?: boolean;
}

/** Slice 3 exam fields (external-website exams). null/[] when mode is "questions". */
export interface ExternalExamFields {
  mode: ExamMode;
  external_url: string | null;
  allowed_domains: string[];
  finish_url_contains: string | null;
}

/** mcq -> option index, numeric -> number, short -> string. Missing key = not answered.
 *  More types: mcq multiple -> sorted option indexes,
 *  fill_blank -> one string per blank, file_upload -> upload ids, code with several
 *  languages -> {language, source}. */
export type AnswerValue = number | string | number[] | string[] | CodeAnswer;

/** A coding answer of a question with several languages (language key, e.g. "PY3"). */
export interface CodeAnswer {
  language: string;
  source: string;
}
export type AnswerMap = Record<string, AnswerValue>;

export interface LoginResponse {
  token: string;
  role: Role;
  name: string;
  resume?: boolean;
}

/** Bridge v2 §4: a class login answers with a short-lived setup token instead of a session. */
export interface ClassLoginSetupResponse {
  setup_token: string;
  class: { name: string; course: string };
}

/** What POST /api/auth/login returns: a normal session, or the start of class-login set-up. */
export type LoginResult = LoginResponse | ClassLoginSetupResponse;

export function isClassLoginSetup(r: LoginResult): r is ClassLoginSetupResponse {
  return typeof (r as ClassLoginSetupResponse).setup_token === "string";
}

/** Bridge v2 §4 step 2: the student was found on the class list. */
export interface SetupIdentifyResponse {
  identity_token: string;
  name: string;
  netid: string;
}

export interface Me {
  username: string;
  name: string;
  role: Role;
  /** the DKU OJ NetID, when the account has one */
  netid?: string;
}

export interface Question {
  id: string;
  type: QuestionType;
  prompt: string;
  points: number;
  options: string[];
  correct_index: number | null;
  answer_number: number | null;
  tolerance: number | null;
  accepted_answers: string[];
  rubric: RubricItem[];
  min_words: number | null;
  max_words: number | null;
}

export interface RubricItem {
  label: string;
  points: number;
}

export type QuestionInput = Omit<Question, "id">;

export interface StudentQuestion {
  id: string;
  type: QuestionType;
  prompt: string;
  points: number;
  options: string[];
  rubric?: RubricItem[];
  min_words?: number | null;
  max_words?: number | null;
}

export interface Exam {
  id: string;
  title: string;
  /** Live exams: code for the app's emergency exit (teacher only). */
  invigilator_code?: string | null;
  duration_min: number;
  status: ExamStatus;
  start_code: string | null;
  questions: Question[];
  created_at: string;
  question_count: number;
  total_points: number;
  course: string;
  room: string;
  scheduled_at: string | null;
  shuffle_questions: boolean;
  grades_released: boolean;
  released_at: string | null;
  closed_at: string | null;
  pending_grading: number;
  // slice 3 (always sent by the server; optional so older fixtures still type-check)
  mode?: ExamMode;
  external_url?: string | null;
  allowed_domains?: string[];
  finish_url_contains?: string | null;
  external_kind?: ExternalKind | null;
  canvas?: CanvasInfo | null;
  /** Teacher view: plain-language note about the Canvas setup, or null. */
  canvas_notice?: string | null;
  finish_url_regex?: string | null;
  /** 2026-10-14: apps allowed during the exam (catalog keys); everything else is closed. */
  allowed_apps?: string[];
  /** S3: a student coming back after FairPlay closed waits for the teacher (default true). */
  resume_needs_approval?: boolean;
  /** S6: only the strongest lock counts (default false). */
  require_strong_lock?: boolean;
  /** 2026-10-23 (exam tools); missing from older servers = off. */
  calculator?: CalculatorMode;
  open_book?: boolean;
  websites?: string[];
}

/** 2026-10-23: calculator setting of an exam. */
export type CalculatorMode = "off" | "scientific" | "graphing";

/** 2026-10-23: what the student gets inside FairPlay (attempt view, exam list, offline package). */
export interface ExamTools {
  calculator: CalculatorMode;
  open_book: boolean;
  /** host names; each host and its subdomains may load in the websites pane */
  websites: string[];
}

/** 2026-10-23: one open-book file (no bytes). kind is what the server sniffed. */
export interface MaterialFile {
  id: string;
  name: string;
  kind: "pdf" | "png" | "jpg" | "txt" | "md";
  media_type: string;
  size: number;
  created_at: string;
}

/** GET /api/student/exams/{id}/materials */
export interface MaterialsInfo {
  open_book: boolean;
  /** false once the attempt exists (or the exam is not open book / closed) */
  can_change: boolean;
  reason: string | null;
  files: MaterialFile[];
  used_bytes: number;
  max_files: number;
  max_bytes: number;
  types: string[];
}

/** GET /api/exams/{id}/materials (teacher) */
export interface StudentMaterials {
  username: string;
  name: string;
  files: MaterialFile[];
}

/** 2026-10-14: an app a teacher may allow (GET /api/exams/allowed-apps). */
export interface AllowedAppOption {
  key: string;
  name: string;
  /** can open files or reach the internet on its own */
  risky: boolean;
  mac: boolean;
  windows: boolean;
}

/** An allowed app as the exam page hands it to FairPlay (ids per platform). */
export interface AllowedApp {
  key: string;
  name: string;
  mac: string[];
  windows: string[];
}

export interface ExamInput {
  title: string;
  duration_min: number;
  questions: QuestionInput[];
  course?: string;
  room?: string;
  scheduled_at?: string | null;
  shuffle_questions?: boolean;
  // slice 3: omitted mode = "questions". When "external", questions must be [].
  mode?: ExamMode;
  external_url?: string | null;
  allowed_domains?: string[];
  finish_url_contains?: string | null;
  external_kind?: ExternalKind | null;
  canvas?: CanvasInput | null;
  finish_url_regex?: string | null;
  /** slice 4: total points of an exam held on another website (default 100). */
  external_points?: number;
  allowed_apps?: string[];
  resume_needs_approval?: boolean;
  require_strong_lock?: boolean;
  calculator?: CalculatorMode;
  open_book?: boolean;
  websites?: string[];
}

export interface LiveStudent {
  attempt_id: string;
  username: string;
  name: string;
  status: AttemptStatus;
  started_at: string;
  submitted_at: string | null;
  deadline_at: string;
  answered: number;
  question_count: number;
  current_question: number;
  locked: boolean;
  /** 2026-10-23: 0..1 from the student's laptop, null/missing when unknown. */
  battery?: number | null;
  charging?: boolean | null;
  last_heartbeat_at: string | null;
  online: boolean;
  event_count: number;
  last_event: string | null;
  needs_look: boolean;
  /** Handed in after time (audit S-02). */
  late?: boolean;
  // Audit 2026-10-12: from the app's own signed beats (locked above too).
  app_online?: boolean;
  last_app_signal_at?: string | null;
  app_checks?: string;
  client?: "app" | "browser";
  entries?: number;
  /** S5: the app holds sealed answers for this student that are not uploaded yet. */
  sealed_pending?: boolean;
  /** S3: came back after FairPlay closed; "pending" waits for Approve. */
  approval?: Approval;
  pending_since?: string | null;
  /** S4: asked from another computer to continue there ("Approve swap"). */
  swap_requested?: boolean;
  /** S6: the exam requires the strongest lock and this computer's app doesn't use it. */
  weak_lock?: boolean;
}

/** S3: null = never had to wait. */
export type Approval = "pending" | "approved" | null;

export interface LiveFlag {
  event_id: number;
  attempt_id: string;
  username: string;
  name: string;
  type: string;
  detail: string;
  server_time: string;
}

export interface LiveRoom {
  exam_id: string;
  status: ExamStatus;
  start_code: string | null;
  /** Code for the app's emergency exit (Ctrl-Opt-Cmd-E), checked by the server. */
  invigilator_code?: string | null;
  duration_min: number;
  students: LiveStudent[];
  flags: LiveFlag[];
  counts: { writing: number; submitted: number; offline: number; needs_look: number };
  /** Audit W-13: the server's clock when this snapshot was made. */
  server_time?: string;
}

export type EventReview = "open" | "dismissed" | "noted";

export interface IntegrityEvent {
  type: string;
  detail: string;
  client_time: string | null;
}

export interface StoredIntegrityEvent extends IntegrityEvent {
  server_time: string;
  event_id: number;
  review: EventReview;
  note: string;
}

export interface ResultAnswer {
  question_id: string;
  given: AnswerValue | null;
  correct: boolean;
  points_awarded: number;
  max_points: number;
  manual: boolean;
  pending: boolean;
  comment: string;
  rubric_scores: number[] | null;
  /** Slice 6 D: scanned handwriting for paper exams. */
  image?: string | null;
}

export interface ResultAttempt {
  attempt_id: string;
  username: string;
  name: string;
  status: AttemptStatus;
  auto_submitted: boolean;
  /** null for external-website exams (graded on the other website). */
  score: number | null;
  total_points: number;
  /** null for external-website exams. */
  percent: number | null;
  submitted_at: string | null;
  receipt: string | null;
  answers: ResultAnswer[];
  events: StoredIntegrityEvent[];
  needs_grading: number;
  overall_comment: string;
  flag_count: number;
  /** Audit S-02: handed in after time. */
  late?: boolean;
  /** Answers sent with a submit after time: shown to the teacher, not graded. */
  late_answers?: Record<string, unknown>;
}

export interface ResultQuestion {
  question_id: string;
  prompt: string;
  type: QuestionType;
  percent_correct: number | null;
  points: number;
  avg_points: number | null;
}

export interface Results {
  exam_id: string;
  title: string;
  course: string;
  total_points: number;
  average_percent: number | null;
  median_percent: number | null;
  grades_released: boolean;
  released_at: string | null;
  pending_grading: number;
  submitted_count: number;
  attempt_count: number;
  flags_total: number;
  flags_open: number;
  score_buckets: { label: string; count: number }[];
  attempts: ResultAttempt[];
  questions: ResultQuestion[];
}

export interface Grade {
  points: number;
  comment: string;
  rubric_scores: number[] | null;
  graded_at: string;
}

export interface GradingQuestion {
  question_id: string;
  index: number;
  type: QuestionType;
  prompt: string;
  points: number;
  rubric: RubricItem[];
  to_grade: number;
  graded: number;
}

export interface GradingItem {
  attempt_id: string;
  anon_id: string;
  username: string;
  name: string;
  question_id: string;
  answer: AnswerValue | null;
  word_count: number | null;
  auto_points: number | null;
  grade: Grade | null;
  flag_count: number;
}

export interface GradingQueue {
  exam_id: string;
  title: string;
  course: string;
  status: ExamStatus;
  grades_released: boolean;
  questions: GradingQuestion[];
  items: GradingItem[];
}

export interface GradeInput {
  points: number;
  comment: string;
  rubric_scores: number[] | null;
}

export interface GradeResponse {
  attempt_id: string;
  question_id: string;
  grade: Grade | null;
  score: number;
  total_points: number;
  percent: number;
  needs_grading: number;
}

export interface GradingSummary {
  exam_id: string;
  title: string;
  course: string;
  class_id?: number | null;
  status: ExamStatus;
  pending: number;
  submitted: number;
  grades_released: boolean;
}

export type GradebookState =
  | "graded"
  | "needs_grading"
  | "in_progress"
  | "missing"
  | "not_taken"
  /** slice 3: external-website exam handed in (no score here). */
  | "submitted";

export interface Gradebook {
  courses: string[];
  exams: { exam_id: string; title: string; course: string; total_points: number; status: ExamStatus; grades_released: boolean }[];
  students: {
    username: string;
    name: string;
    average_percent: number | null;
    cells: Record<string, { state: GradebookState; score: number | null; percent: number | null; late?: boolean }>;
  }[];
}

export interface StudentExamSummary {
  id: string;
  title: string;
  duration_min: number;
  /** null while only the early upload window is open (nothing about the questions yet) */
  question_count: number | null;
  status: ExamStatus;
  course: string;
  room: string;
  scheduled_at: string | null;
  /** slice 3 (always sent by the server). */
  mode?: ExamMode;
  /** 2026-10-23: exam tools (open_book -> the upload area before the exam). */
  tools?: ExamTools;
  attempt: null | {
    attempt_id: string;
    status: AttemptStatus;
    submitted_at: string | null;
    receipt: string | null;
    released: boolean;
    percent: number | null;
    /** 2026-10-24: what the student may see after hand-in (external exams) */
    review?: ReviewLevel;
  };
}

export interface StudentGradeSummary {
  exam_id: string;
  title: string;
  course: string;
  submitted_at: string | null;
  released: boolean;
  score: number | null;
  total_points: number;
  percent: number | null;
}

export interface FeedbackQuestion {
  question_id: string;
  index: number;
  type: QuestionType;
  prompt: string;
  options: string[];
  points: number;
  /** null: answers-only review (no points shown) */
  points_awarded: number | null;
  /** "pending": not graded yet (external source); "answered": answers-only review */
  status: "correct" | "partial" | "wrong" | "unanswered" | "pending" | "answered";
  given: AnswerValue | null;
  correct_answer: AnswerValue | null;
  tolerance: number | null;
  comment: string;
  rubric: RubricItem[];
  rubric_scores: number[] | null;
}

export interface StudentFeedback {
  exam_id: string;
  title: string;
  course: string;
  submitted_at: string | null;
  receipt: string | null;
  /** null in an answers-only review */
  score: number | null;
  total_points: number;
  percent: number | null;
  class_average_percent: number | null;
  overall_comment: string;
  questions: FeedbackQuestion[];
}

export interface AttemptView {
  attempt_id: string;
  exam_id: string;
  title: string;
  course?: string;
  duration_min: number;
  started_at: string;
  deadline_at: string;
  server_time: string;
  questions: StudentQuestion[];
  answers: AnswerMap;
  status: AttemptStatus;
  // slice 3: external exams have questions [] and answers {}; allowed_domains always
  // includes the external_url host. null/[] for "questions" exams.
  mode?: ExamMode;
  external_url?: string | null;
  allowed_domains?: string[];
  finish_url_contains?: string | null;
  external_kind?: ExternalKind | null;
  canvas?: CanvasInfo | null;
  /** Canvas exams: where the app may go in Canvas; null when the link has no course and item. */
  canvas_scope?: CanvasScopeView | null;
  finish_url_regex?: string | null;
  /** Only in the POST start response: the app session (X-FairPlay-Session). */
  session?: string;
  /** 2026-10-14: apps the locked computer keeps open (everything else is closed). */
  allowed_apps?: AllowedApp[];
  /** S3: "pending" -> show "Wait for your teacher" instead of the questions. */
  approval?: Approval;
  /** S3: only in the POST start response: this start must wait for the teacher. */
  needs_approval?: boolean;
  /** S2: only in the POST start response. */
  min_app_version?: string | null;
  /** 2026-10-22: the built-in sample exam ("Stop practice", no score page). */
  trial?: boolean;
  /** 2026-10-23: calculator / materials / websites (missing from older servers: none). */
  tools?: ExamTools;
}

/** 2026-10-22: GET /api/student/trial, for the "Sample exam" card. */
export interface TrialInfo {
  exam_id: string;
  title: string;
  duration_min: number;
  question_count: number;
  attempt: { attempt_id: string; status: AttemptStatus } | null;
}

export interface SaveAnswersResponse {
  saved: boolean;
  client_seq: number;
  server_time: string;
}

export interface HeartbeatResponse {
  ok: boolean;
  status: AttemptStatus;
  server_time: string;
  deadline_at: string;
  /** S3 */
  approval?: Approval;
}

export interface EventsResponse {
  ok: boolean;
  stored: number;
}

export interface SubmitResponse {
  status: "submitted";
  submitted_at: string;
  receipt: string;
}

// ---------- slice 5: LMS sync ----------

export type LmsProviderId = "canvas" | "moodle" | "blackboard" | "brightspace";

/** Slice 5 exam fields (declaration merging keeps the Exam interface above untouched). */
export interface Exam {
  /** Linked LMS course (CourseLink id); when set only its students can start. */
  course_link_id?: number | null;
  /** LMS assignment created on the first grade push. */
  lms_assignment_id?: string | null;
}

export interface LmsProvider {
  id: LmsProviderId;
  name: string;
  auth: "token" | "oauth_token";
  status: "ready" | "beta";
  token_help: string;
  base_url_hint: string;
}

/** The token is never sent back by the server. */
export interface LmsConnection {
  id: number;
  provider: LmsProviderId;
  provider_name: string;
  status: "ready" | "beta";
  base_url: string;
  label: string;
  lms_user_name: string;
  created_at: string;
  last_sync_at: string | null;
  last_error: string | null;
  course_count: number;
}

export interface LmsConnectionInput {
  provider: LmsProviderId;
  base_url: string;
  token: string;
  label?: string;
}

/** A course the teacher teaches in the LMS; link_id set when already linked. */
export interface LmsCourse {
  lms_course_id: string;
  name: string;
  code: string;
  link_id: number | null;
}

export interface LmsCourseLink {
  id: number;
  connection_id: number;
  provider: LmsProviderId;
  provider_name: string;
  lms_course_id: string;
  name: string;
  code: string;
  member_count: number;
  exam_count: number;
  last_sync_at: string | null;
  last_error: string | null;
}

export interface LmsSyncResult {
  added: number;
  updated: number;
  removed: number;
  total: number;
}

export interface LmsLinkResponse extends LmsCourseLink {
  sync: LmsSyncResult;
}

export interface LmsPushResult {
  pushed: number;
  skipped: number;
  errors: { student: string; detail: string }[];
  lms_assignment_id?: string;
}

// ---------- slice 6: delivery, question images, topics, offline packages ----------

/** How an exam is delivered. "website" = mode "external"; "paper" is printed and scanned. */
export type Delivery = "computer" | "paper" | "website";

/** A picture shown above a question prompt. url = /api/uploads/<id> or https://… (or a data: URL inside an offline package). */
export interface QuestionImage {
  url: string;
  alt: string;
}

/** Slice 6 fields (declaration merging; optional so older fixtures still type-check). */
export interface Question {
  images?: QuestionImage[];
  /** 1–5 lower-case topic tags; required when saving. */
  topics?: string[];
}
export interface StudentQuestion {
  images?: QuestionImage[];
}
export interface FeedbackQuestion {
  images?: QuestionImage[];
}
export interface GradingQuestion {
  images?: QuestionImage[];
}
export interface Exam {
  delivery?: Delivery;
}
export interface ExamInput {
  delivery?: Delivery;
}
export interface StudentExamSummary {
  /** operator preview sign-in: listed for viewing only, never started */
  preview?: boolean;
  delivery?: Delivery;
  /** Audit W-02: id of the current offline package (live exams). */
  package_id?: string | null;
}

export interface UploadResult {
  id: string;
  url: string;
  width: number;
  height: number;
}

export interface TopicCount {
  topic: string;
  question_count: number;
}

/** GET /api/student/exams/{id}/package — AES-GCM, key = PBKDF2-SHA256(start_code + exam_id, salt, 100 000). base64 fields. */
export interface OfflinePackage {
  exam_id: string;
  version: number;
  /** Audit W-02: changes when the start code or duration changes (re-download then). */
  package_id?: string;
  alg: "AES-GCM";
  /** PBKDF2 iterations (version 3+); older packages: 100 000 */
  iterations?: number;
  salt: string;
  iv: string;
  ciphertext: string;
}

/** Decrypted package plaintext: the student exam view (no answer keys). */
export interface OfflineExam {
  exam_id: string;
  title: string;
  course: string;
  duration_min: number;
  mode: ExamMode;
  shuffle_questions: boolean;
  questions: StudentQuestion[];
  /** 2026-10-14 (packages built since): apps kept open while locked */
  allowed_apps?: AllowedApp[];
  /** 2026-10-23 (packages built since) */
  tools?: ExamTools;
  generated_at: string;
}

// ---------- slice 6 F/G: class insights + student profiles ----------

export interface InsightsExam {
  exam_id: string;
  title: string;
  course: string;
  date: string | null;
  delivery?: string;
}

export interface TopicExamCell {
  exam_id: string;
  avg_percent: number;
  n_answers: number;
}

export interface TopicInsight {
  topic: string;
  avg_percent: number;
  n_answers: number;
  n_students: number;
  /** Last exam's average minus the average of earlier exams (null with one exam). */
  trend: number | null;
  /** Answers to questions without topics (old exams). */
  untagged?: boolean;
  by_exam: TopicExamCell[];
}

export interface HardestQuestion {
  exam_id: string;
  exam_title: string;
  course: string;
  question_id: string;
  question_index: number;
  type: string;
  prompt: string;
  topics: string[];
  avg_percent: number;
  lost_points: number;
  n_answers: number;
}

export interface MistakeExample {
  exam_id: string;
  exam_title: string;
  attempt_id: string;
  question_id: string;
  question_index: number;
  username: string;
  name: string;
  text: string;
  source: "tag" | "teacher" | "ai";
}

export interface MistakeCluster {
  label: string;
  count: number;
  examples: MistakeExample[];
  topics: string[];
  sources?: { tag: number; teacher: number; ai: number };
}

/** GET /api/insights?course=&exam_ids= */
export interface Insights {
  exams: InsightsExam[];
  all_exams: InsightsExam[];
  courses: string[];
  topics: TopicInsight[];
  hardest: HardestQuestion[];
  mistakes: MistakeCluster[];
  attempt_count: number;
}

/** GET /api/students */
export interface StudentSummary {
  username: string;
  name: string;
  exam_count: number;
  average_percent: number | null;
  weakest_topic: { topic: string; avg_percent: number } | null;
  last_exam_at: string | null;
}

export interface ProfileTopic {
  topic: string;
  avg_percent: number;
  n: number;
  untagged?: boolean;
  trend: { date: string | null; percent: number; exam_id: string; title: string }[];
}

export interface ProfileExam {
  exam_id: string;
  attempt_id: string;
  title: string;
  course: string;
  delivery: string;
  date: string | null;
  score: number | null;
  total_points: number;
  percent: number | null;
  state: "graded" | "needs_grading";
  /** Minutes from start to submit (computer exams only). Context only — never a cheating signal. */
  time_on_task_min: number | null;
}

/** Practice results, when the practice feature provides them (shape owned by that feature). */
export interface ProfilePractice {
  id?: string;
  title?: string;
  status?: string;
  topics?: string[];
  percent?: number | null;
  correct?: number;
  total?: number;
  date?: string | null;
  [key: string]: unknown;
}

/** GET /api/students/{username}/profile */
export interface StudentProfile {
  student: { username: string; name: string };
  topics: ProfileTopic[];
  strengths: string[];
  weak: string[];
  exams: ProfileExam[];
  average_percent: number | null;
  thresholds?: { strength_min: number; weak_below: number; min_answers: number };
  practice?: ProfilePractice[];
  courses?: string[];
}

// ---------- slice 6 H: practice assignments (formative; never part of grades) ----------

export interface PracticeTopicScore {
  topic: string;
  avg_percent: number;
  n: number;
}

export interface PracticeStudent {
  username: string;
  name: string;
  topics: PracticeTopicScore[];
  weak: PracticeTopicScore[];
  /** true when no topic is < 60 % with ≥ 2 answers and the lowest topics are used instead */
  weak_is_fallback: boolean;
}

export interface PracticeStudents {
  students: PracticeStudent[];
  topics: { topic: string; question_count: number }[];
  ai_enabled: boolean;
}

export type PracticeItemStatus = "verified" | "ready" | "needs_answer";
export type PracticeItemKind = "perturbed" | "ai_rewrite" | "shuffled" | "original" | "bank" | "teacher_edit";

export interface PracticeItem {
  id: string;
  type: "mcq" | "numeric" | "short" | "essay";
  prompt: string;
  options: string[];
  correct_index: number | null;
  answer_number: number | null;
  tolerance: number | null;
  accepted_answers: string[];
  math: boolean;
  rubric: { label: string; points: number }[];
  explanation: string;
  topics: string[];
  images: QuestionImage[];
  status: PracticeItemStatus;
  kind: PracticeItemKind;
  verification: { method: string; how?: string; formula?: string; [k: string]: unknown } | null;
  note: string;
  suggested_answer?: string;
  source: { exam_id: string | null; exam_title: string; question_id: string | null };
  student_result?: { answer: unknown; correct: boolean | null; tries: number; ever_correct: boolean } | null;
}

export interface PracticeProgress {
  answered: number;
  correct: number;
  total: number;
}

export interface PracticeSetSummary {
  id: string;
  username: string | null;
  name: string | null;
  status: "draft" | "approved" | "sent";
  title: string;
  topics: string[];
  formative: true;
  item_count: number;
  needs_answer: number;
  verified: number;
  created_at: string | null;
  approved_at: string | null;
  sent_at: string | null;
  progress: PracticeProgress;
}

export interface PracticeSet extends PracticeSetSummary {
  items: PracticeItem[];
  dropped?: { question_id: string | null; reason: string }[];
}

export interface PracticeGenerateInput {
  username?: string;
  usernames?: string[];
  topics?: string[];
  count: number;
}

export interface PracticeGenerateResult {
  sets: PracticeSet[];
  skipped: { username: string; reason: string }[];
}

export interface PracticeItemEdit {
  prompt?: string;
  options?: string[];
  correct_index?: number;
  answer_number?: number;
  tolerance?: number;
  accepted_answers?: string[];
  explanation?: string;
}

export interface PracticeBankQuestion {
  exam_id: string;
  exam_title: string;
  question_id: string;
  type: string;
  prompt: string;
  topics: string[];
}

export interface StudentPracticeSummary {
  id: string;
  title: string;
  topics: string[];
  formative: true;
  item_count: number;
  answered: number;
  correct: number;
  progress: PracticeProgress;
  sent_at: string | null;
  teacher_name: string;
}

export interface StudentPracticeResult {
  given: unknown;
  correct: boolean | null;
  tries: number;
  ever_correct: boolean;
  explanation: string;
  /** mcq: {index, text}; numeric/short: string; essay: null. Shown after a right answer or 2 tries. */
  correct_answer: { index: number | null; text: string | null } | string | null;
  answered_at: string | null;
}

export interface StudentPracticeItem {
  id: string;
  type: "mcq" | "numeric" | "short" | "essay";
  prompt: string;
  options: string[];
  images: QuestionImage[];
  topics: string[];
  math: boolean;
  rubric?: { label: string; points: number }[];
  result: StudentPracticeResult | null;
}

export interface StudentPracticeSet {
  id: string;
  title: string;
  topics: string[];
  formative: true;
  sent_at: string | null;
  items: StudentPracticeItem[];
  progress: PracticeProgress;
}

export interface PracticeAnswerResponse {
  item_id: string;
  correct: boolean | null;
  readable: boolean;
  feedback: string;
  explanation?: string;
  correct_answer?: StudentPracticeResult["correct_answer"];
  tries?: number;
  result: StudentPracticeResult | null;
  progress: PracticeProgress;
}

// ---------- audit 2026-10-12 S-03: classes ----------
export interface Exam {
  /** The class that takes this exam (required before it can open, unless LMS-linked). */
  class_id?: number | null;
}
export interface ExamInput {
  class_id?: number | null;
}

export interface SchoolClass {
  id: number;
  name: string;
  /** Free text on the class card, e.g. "Fall 2026 · Section 2" ("" when not set). */
  term?: string;
  teacher_name?: string;
  /** "K7QM-3XPA" — students type it to join. */
  join_code: string;
  member_count: number;
  exam_count: number;
  created_at: string;
}

export interface ClassMemberRow {
  username: string;
  name: string;
  joined_at: string;
}

export interface ClassDetail extends SchoolClass {
  members: ClassMemberRow[];
}

export interface AddMembersResult {
  added: number;
  already: number;
  unknown: string[];
}

/** 2026-10-24: a class/course the student is in (FairPlay class or DKU OJ class). Optional
 *  fields may be missing on older servers; the UI copes. */
export interface StudentClassNextExam {
  title: string;
  starts_at: string | null;
  status: "draft" | "live" | "closed" | string;
  /** 2026-10-24 (newer servers) */
  duration_min?: number | null;
  question_count?: number | null;
}

export interface StudentClass {
  /** FairPlay classes: a number; DKU OJ classes: "dkuoj:<id>". */
  id: number | string;
  name: string;
  course?: string | null;
  source?: "dkuoj" | "fairplay" | string;
  term?: string;
  teacher?: string | null;
  joined_at?: string;
  upcoming_exams?: number;
  next_exam?: StudentClassNextExam | null;
  can_leave?: boolean;
}

// ---------- S1 (2026-10-20): device check ----------

export interface DeviceCheckInput {
  platform: string;
  app_version: string;
  os_version: string;
  checks: { id: string; ok: boolean; message: string; info?: boolean }[];
  exam_id?: string | null;
}

/** GET /api/exams/{id}/readiness: one per student; null fields = never checked. */
export interface ReadinessRow {
  student_id: number;
  name: string;
  platform: string | null;
  app_version: string | null;
  os_version: string | null;
  ok: boolean | null;
  failed: string[] | null;
  checked_at: string | null;
}

// ---------- Phase 4: coding questions ----------

/** Languages a coding question can use. The teacher picks; students never choose. */
export type CodeLanguage = "python" | "javascript" | "c" | "cpp" | "java";

/** One test: stdin in, expected stdout out. Hidden tests (visible false) never reach students. */
export interface CodeTest {
  input: string;
  expected: string;
  visible: boolean;
  points: number;
}

/** A visible test as students get it (no points, no visible flag). */
export interface StudentCodeTest {
  input: string;
  expected: string;
}

/** Fields of a "code" question (teacher view). language null = the exam's default_language. */
export interface Question {
  language?: CodeLanguage | null;
  starter_code?: string;
  tests?: CodeTest[];
  write_only?: boolean;
  time_limit_ms?: number;
}
export interface StudentQuestion {
  /** resolved: the question's own language or the exam default */
  language?: CodeLanguage;
  starter_code?: string;
  /** visible tests only */
  tests?: StudentCodeTest[];
  write_only?: boolean;
  time_limit_ms?: number;
}
export interface Exam {
  default_language?: CodeLanguage;
}
export interface ExamInput {
  default_language?: CodeLanguage;
}
export interface GradingQuestion {
  /** code questions: resolved language and every test (hidden ones too) */
  language?: CodeLanguage;
  tests?: CodeTest[];
  time_limit_ms?: number;
  write_only?: boolean;
}
export interface FeedbackQuestion {
  language?: CodeLanguage;
}

// ---------- rich questions and exams from an external source (integrations) ----------

/** Where an exam comes from: "fairplay", or the id of an integration. */
export type ExamSource = string;

/** How a prompt is written: plain text, markdown (with ~…~ / $$…$$ math), or sanitized HTML. */
export type PromptFormat = "plain" | "markdown" | "html";

/** One language a coding question allows. runner null = no in-app runner. */
export interface CodeLanguageOption {
  key: string;
  name: string;
  runner: CodeLanguage | null;
}

/** Optional question fields (rich text, more answer types). Missing = the classic question. */
export interface RichQuestionFields {
  prompt_format?: PromptFormat;
  /** display label, e.g. "Q3" */
  label?: string;
  title?: string;
  /** partial credit is given */
  partial?: boolean;
  source?: ExamSource;
  // mcq
  multiple?: boolean;
  true_false?: boolean;
  /** show the options in a per-student order (the answer keeps the original indexes) */
  shuffle?: boolean;
  // fill_blank
  blanks?: number;
  blank_points?: number[];
  case_sensitive?: boolean;
  // file_upload
  allowed_extensions?: string[];
  max_file_size?: number;
  /** markdown shown above the file picker */
  instructions_md?: string;
  /** files per answer: always 1 (one file, replaced when a new one is chosen) */
  max_files?: number;
  // code with several languages
  languages?: CodeLanguageOption[];
  /** starter code per language key */
  templates?: Record<string, string>;
  memory_limit_kb?: number;
  /** fp-asset name -> URL already available (data: URLs); otherwise the page fetches the
   *  attempt (or teacher) asset URL. */
  asset_urls?: Record<string, string>;
}
export interface Question extends RichQuestionFields {}
export interface StudentQuestion extends RichQuestionFields {}
export interface GradingQuestion extends RichQuestionFields {}
export interface FeedbackQuestion extends RichQuestionFields {
  /** "markdown": `comment` is markdown */
  comment_format?: "plain" | "markdown";
}

/** An exam that comes from another system (null/missing: a FairPlay exam). */
export interface ExternalInfo {
  /** integration id */
  source: string;
  /** display name, e.g. the other system's product name */
  name: string;
  /** the exam in the other system */
  url: string | null;
  /** questions and settings cannot be changed in FairPlay (PUT -> 409) */
  read_only: boolean;
  /** grades come from the other system */
  graded_externally: boolean;
  /** the questions have arrived (until then Open exam -> 409) */
  package_ready?: boolean;
  start_time?: string | null;
  end_time?: string | null;
}

export interface Exam {
  external?: ExternalInfo | null;
}
export interface AttemptView {
  source?: ExamSource;
  external_name?: string;
  /** files handed in so far for file_upload questions */
  answer_files?: AnswerUpload[];
  total_points?: number;
}
export interface OfflineExam {
  source?: ExamSource;
  external_name?: string;
}
export interface Results {
  external?: ExternalInfo | null;
}
export interface GradingQueue {
  external?: ExternalInfo | null;
}

/** Result of one attempt released by the external source. */
export interface ExternalResult {
  released?: boolean;
  total?: number;
  total_points?: number;
  updated_at?: string | null;
  questions?: { question_id: string; points: number; feedback_md?: string }[];
}

/** Sending one attempt's answers to the external source. dead = gave up (see the status page). */
export interface ExternalDelivery {
  status: "pending" | "sent" | "failed" | "dead";
  tries: number;
  last_error: string | null;
  receipt: string | null;
  next_at: string | null;
  sent_at: string | null;
}
export interface ResultAttempt {
  external_result?: ExternalResult | null;
  external_delivery?: ExternalDelivery | null;
}
export interface StudentExamSummary {
  source?: ExamSource;
  external_name?: string;
}
export interface StudentGradeSummary {
  source?: ExamSource;
  external_name?: string;
}
export interface StudentFeedback {
  source?: ExamSource;
  external_name?: string;
}

/** A file handed in for a file_upload question (POST /api/student/attempts/{aid}/files). */
export interface AnswerUpload {
  id: string;
  name: string;
  size: number;
  question_id?: string;
  sha256?: string;
  created_at?: string;
}

// ---------- 2026-10-23: open to everyone or selected students; automatic close ----------

/** "all" = every student of the exam (its class, LMS course or external roster). */
export type OpenTo = "all" | number[];

/** GET /api/exams/{id}/students: who may take the exam (the "Selected students" checklist). */
export interface ExamStudent {
  id: number;
  username: string;
  name: string;
  /** has an attempt (removing them never stops it) */
  started: boolean;
}

export interface Exam {
  /** Who may start ("all" or student ids). Older servers: absent = all. */
  open_to?: OpenTo;
  /** The exam closes by itself at this time (UTC). External exams: from their source, read-only. */
  closes_at?: string | null;
  /** The last close was the automatic one. */
  auto_closed?: boolean;
}
export interface ExamInput {
  /** Optional automatic close (FairPlay exams). null = only the teacher closes it. */
  closes_at?: string | null;
}
export interface LiveRoom {
  open_to?: OpenTo;
  /** The selected students with names (null when open to all). */
  selected?: ExamStudent[] | null;
  closes_at?: string | null;
}
export interface StudentExamSummary {
  /** false = the teacher opened it for selected students only (no start, no package). */
  open_for_me?: boolean;
  /** 2026-10-24: the exam's classes ("dkuoj:31" for DKU OJ, the class number as text for FairPlay). */
  class_ids?: string[];
}
export interface OfflineExam {
  /** 2026-10-23: the offline countdown never runs past this */
  closes_at?: string | null;
}

// ---------- 2026-10-24: student review after hand-in (set by the exam's source) ----------

/** "nothing": just "Handed in"; "score": the total once released; "breakdown": the total and the
 *  points per question once released (no question text); "answers": own answers, read-only;
 *  "answers_and_results": everything once released. FairPlay exams are always
 *  "answers_and_results". */
export type ReviewLevel = "nothing" | "score" | "breakdown" | "answers" | "answers_and_results";
/** Review "breakdown": one question's number and points (no text). */
export interface BreakdownPoint {
  index: number;
  label: string;
  points: number;
  points_awarded: number | null;
}
export interface StudentGradeSummary {
  review?: ReviewLevel;
  /** only with review "breakdown" once released */
  breakdown?: BreakdownPoint[];
}
/** Review "breakdown" on the grade page: per question its number, title and points only. */
export interface BreakdownQuestion extends BreakdownPoint {
  question_id: string;
  title: string;
  status: "correct" | "partial" | "wrong" | "unanswered" | "pending";
}
export interface StudentFeedback {
  /** "answers": only the student's answers (no score, points or feedback) */
  review?: ReviewLevel;
  released?: boolean;
}

// ---------- 2026-10-24: early upload window; allowed websites as full links ----------

/** An allowed website given as a full link: the page its websites tab opens first. Every
 *  page of `host` is allowed (the link is only the start page). */
export interface WebsiteLink {
  host: string;
  url: string;
  title?: string | null;
}
/** Sites the lock also opens so a service works (Duke Box: sign-in, scripts, previews). */
export interface SiteCompanions {
  /** the allowed entry they belong to */
  for: string;
  /** "Duke Box" */
  name: string;
  sites: string[];
}
export interface ExamTools {
  /** 2026-10-24: start pages of allowed websites given as links */
  website_links?: WebsiteLink[];
  /** 2026-10-24: websites + their companions: what the page hands the app (allowed_sites).
   *  Missing (older servers): the websites. */
  allowed_sites?: string[];
}
export interface Exam {
  website_links?: WebsiteLink[];
  companions?: SiteCompanions[];
  /** open book: students may upload their files this many days before scheduled_at (0 = off) */
  upload_opens_days?: number;
}
export interface ExamInput {
  upload_opens_days?: number;
}
export interface StudentExamSummary {
  /** 2026-10-24: a draft exam whose early upload window is open ("Upcoming — upload your
   *  files"): no questions, no start; uploads close at closes_at. Null otherwise. */
  upload_window?: { opens_at: string; closes_at: string } | null;
}
export interface MaterialsInfo {
  /** 2026-10-24: the early upload window (null when the exam has none) */
  upload_opens_at?: string | null;
  upload_closes_at?: string | null;
}

// ---------- 2026-10-28: sign-in with a per-student access code ----------

/** POST /api/student/code-login: who the code belongs to; nothing is stored until confirmed. */
export interface CodeLoginPrompt {
  first_name: string;
  last_name: string;
  netid: string;
  exam_title: string;
  confirm_token: string;
  /** true: the student already has an attempt in progress; nothing is started again. */
  resume?: boolean;
}
export interface StudentExamSummary {
  /** true: students enter this exam with a per-student code (no start code; online only). */
  access_code?: boolean;
}
