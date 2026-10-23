// DKU OJ bridge: answer types of DKU OJ questions (multiple answers, true/false, fill in
// the blank, file upload, coding with several languages) save and restore through the
// answer queue; the language picker and "runs after you hand in"; points on the question;
// the teacher's read-only / graded-in-DKU-OJ views; released DKU OJ results for students.
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { useState } from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import type { AnswerMap, AnswerUpload, AnswerValue, AttemptView, Exam, Results, StudentFeedback, StudentQuestion } from "../api/types";

const api = vi.hoisted(() => ({
  student: {
    getAttempt: vi.fn(),
    saveAnswers: vi.fn(),
    heartbeat: vi.fn(),
    events: vi.fn(),
    submit: vi.fn(),
    feedback: vi.fn(),
  },
  teacher: { getExam: vi.fn(), results: vi.fn(), grading: vi.fn() },
  oj: { attemptAsset: vi.fn(), teacherAsset: vi.fn(), uploadAnswerFile: vi.fn() },
}));
vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return {
    ...actual,
    studentApi: { ...actual.studentApi, ...api.student },
    teacherApi: { ...actual.teacherApi, ...api.teacher },
    ojApi: { ...actual.ojApi, ...api.oj },
  };
});

import { NetworkError, setToken } from "../api/client";
import { AnswerInput } from "../components/student/AnswerInput";
import { CodeQuestion, RUNS_AFTER_HAND_IN } from "../components/student/CodeQuestion";
import { FileUploadInput, FillBlankQuestion } from "../components/student/OjAnswers";
import { EDIT_IN_OJ, GRADED_IN_OJ } from "../components/teacher/OjNotices";
import { AnswerQueue } from "../lib/answerQueue";
import * as bridge from "../lib/bridge";
import type { RunResult, Runner } from "../lib/code/runner";
import { setRunnerForTests } from "../lib/code/runners";
import { codeDrafts, fileProblem, rememberUpload } from "../lib/ojAnswers";
import { clearExamDataOnSignOut } from "../lib/offline";
import ExamTakingPage from "../pages/student/ExamTakingPage";
import FeedbackPage from "../pages/student/FeedbackPage";
import ExamSetupPage from "../pages/teacher/ExamSetupPage";
import GradingPage from "../pages/teacher/GradingPage";
import TeacherExamPage from "../pages/teacher/TeacherExamPage";

beforeAll(() => {
  const rect = { x: 0, y: 0, top: 0, left: 0, right: 0, bottom: 0, width: 0, height: 0, toJSON: () => ({}) };
  Range.prototype.getClientRects ??= (() => ({ length: 0, item: () => null, [Symbol.iterator]: [][Symbol.iterator] })) as never;
  Range.prototype.getBoundingClientRect ??= (() => rect) as never;
});
afterEach(() => {
  vi.clearAllMocks();
  setRunnerForTests("python", null);
  setRunnerForTests("javascript", null);
});

const sq = (over: Partial<StudentQuestion>): StudentQuestion =>
  ({ id: "q1", type: "mcq", prompt: "Pick", points: 10, options: [], prompt_format: "oj_md", ...over }) as StudentQuestion;

/** Renders an answer control with its value kept like the exam page does. */
function Harness({ q, initial, kind }: { q: StudentQuestion; initial?: AnswerValue; kind: "input" | "fill" }) {
  const [value, setValue] = useState<AnswerValue | undefined>(initial);
  return (
    <>
      {kind === "input" ? (
        <>
          <h1 id="p">{q.prompt}</h1>
          <AnswerInput question={q} value={value} onChange={setValue} labelledBy="p" />
        </>
      ) : (
        <FillBlankQuestion question={q} value={value} onChange={setValue} labelledBy="p" />
      )}
      <output data-testid="value">{value === undefined ? "none" : JSON.stringify(value)}</output>
    </>
  );
}
const shown = () => screen.getByTestId("value").textContent;

// ---------------------------------------------------------------- answer queue

describe("answer queue with DKU OJ answers", () => {
  const values: Record<string, AnswerValue> = {
    multi: [0, 2],
    tf: 1,
    blanks: ["hash table", "1"],
    files: ["up_1", "up_2"],
    code: { language: "CPP17", source: "int main(){}" },
  };

  it("keeps every new answer shape across a reload and sends it as is", async () => {
    const put = vi.fn(async (_a: AnswerMap, seq: number) => ({ saved: true, client_seq: seq, server_time: "" }));
    const q = new AnswerQueue("a_1", put, { debounceMs: 0, owner: "7" });
    q.init({});
    for (const [k, v] of Object.entries(values)) q.set(k, v);
    await q.flush();
    expect(put).toHaveBeenLastCalledWith(values, 5);
    q.stop();
    // reload: the local copy comes back
    const again = new AnswerQueue("a_1", put, { debounceMs: 0, owner: "7" });
    again.init({});
    expect(again.getAnswers()).toEqual(values);
    again.stop();
  });

  it("a local copy equal to the server's (by value) counts as saved: no resend", () => {
    const put = vi.fn(async (_a: AnswerMap, seq: number) => ({ saved: true, client_seq: seq, server_time: "" }));
    const q = new AnswerQueue("a_2", put, { debounceMs: 0, owner: "7" });
    q.init({});
    q.set("code", { language: "PY3", source: "print(1)" });
    q.stop();
    const server = JSON.parse(JSON.stringify({ code: { language: "PY3", source: "print(1)" } })) as AnswerMap;
    const again = new AnswerQueue("a_2", put, { debounceMs: 0, owner: "7" });
    again.init(server);
    expect(again.status()).toBe("synced");
    again.stop();
  });
});

// ---------------------------------------------------------------- choice

describe("multiple answers (checkboxes)", () => {
  const q = sq({ multiple: true, options: ["Array", "Hash **table**", "Trie", "~O(1)~ list"] });

  it("saves the chosen options as a sorted list and clears to no answer", () => {
    render(<Harness q={q} kind="input" />);
    const boxes = within(screen.getByTestId("mcq-multi")).getAllByRole("checkbox");
    expect(boxes).toHaveLength(4);
    expect(boxes[1]!.querySelector("strong")?.textContent).toBe("table"); // option markdown
    expect(boxes[3]!.querySelector(".katex")).not.toBeNull();
    fireEvent.click(boxes[2]!);
    fireEvent.click(boxes[0]!);
    expect(shown()).toBe("[0,2]");
    expect(boxes[0]).toHaveAttribute("aria-checked", "true");
    fireEvent.click(boxes[0]!);
    fireEvent.click(boxes[2]!);
    expect(shown()).toBe("none");
  });

  it("restores the saved choice", () => {
    render(<Harness q={q} kind="input" initial={[1, 3]} />);
    const boxes = screen.getAllByRole("checkbox");
    expect(boxes.map((b) => b.getAttribute("aria-checked"))).toEqual(["false", "true", "false", "true"]);
  });
});

describe("true or false", () => {
  const q = sq({ true_false: true, options: ["True", "False"] });
  it("shows two big options, saves the index and restores it", () => {
    const { unmount } = render(<Harness q={q} kind="input" />);
    const radios = within(screen.getByTestId("true-false")).getAllByRole("radio");
    expect(radios.map((r) => r.textContent)).toEqual(["True", "False"]);
    fireEvent.click(radios[1]!);
    expect(shown()).toBe("1");
    fireEvent.keyDown(radios[1]!, { key: "ArrowLeft" });
    expect(shown()).toBe("0");
    unmount();
    render(<Harness q={q} kind="input" initial={1} />);
    expect(screen.getAllByRole("radio")[1]).toHaveAttribute("aria-checked", "true");
  });
});

// ---------------------------------------------------------------- fill in the blank

describe("fill in the blank", () => {
  const q = sq({ type: "fill_blank", prompt: "A ___ maps keys to values; lookup is O(___).", blanks: 2, blank_points: [3, 3] });

  it("puts inputs at the blanks and saves one string per blank", () => {
    render(<Harness q={q} kind="fill" />);
    const prompt = document.getElementById("p")!;
    const inputs = within(prompt).getAllByRole("textbox");
    expect(inputs).toHaveLength(2);
    expect(screen.queryByTestId("blank-list")).toBeNull();
    fireEvent.change(inputs[1]!, { target: { value: "1" } });
    expect(shown()).toBe('["","1"]');
    fireEvent.change(inputs[0]!, { target: { value: "hash table" } });
    expect(shown()).toBe('["hash table","1"]');
    fireEvent.change(inputs[0]!, { target: { value: "" } });
    fireEvent.change(inputs[1]!, { target: { value: " " } });
    expect(shown()).toBe("none");
    expect(screen.getByText(/Each blank: 3 \/ 3 points/)).toBeInTheDocument();
  });

  it("restores the saved blanks", () => {
    render(<Harness q={q} kind="fill" initial={["map", "1"]} />);
    expect(screen.getByLabelText("Blank 1")).toHaveValue("map");
    expect(screen.getByLabelText("Blank 2")).toHaveValue("1");
  });

  it("uses numbered inputs under the text when the markers don't match", () => {
    const odd = sq({ type: "fill_blank", prompt: "Name the two structures.", blanks: 2 });
    render(<Harness q={odd} kind="fill" initial={["x"]} />);
    const list = screen.getByTestId("blank-list");
    const inputs = within(list).getAllByRole("textbox");
    expect(inputs).toHaveLength(2);
    expect(inputs[0]).toHaveValue("x");
    fireEvent.change(inputs[1]!, { target: { value: "y" } });
    expect(shown()).toBe('["x","y"]');
  });
});

// ---------------------------------------------------------------- file upload

describe("file upload", () => {
  const q = sq({ id: "q4", type: "file_upload", allowed_extensions: ["pdf", ".PNG"], max_file_size: 1024 });
  const file = (name: string, size: number) => new File([new Uint8Array(size)], name);

  function Up({ upload, initial }: { upload: ((f: File) => Promise<AnswerUpload>) | null; initial?: AnswerValue }) {
    const [value, setValue] = useState<AnswerValue | undefined>(initial);
    return (
      <>
        <FileUploadInput question={q} value={value} onChange={setValue} attemptId="a_9" upload={upload} onUploaded={(f) => rememberUpload("a_9", f)} />
        <output data-testid="value">{value === undefined ? "none" : JSON.stringify(value)}</output>
      </>
    );
  }
  const pick = (f: File) => fireEvent.change(screen.getByTestId("file-input"), { target: { files: [f] } });

  it("checks the extension and size before uploading", () => {
    expect(fileProblem(q, { name: "a.docx", size: 10 })).toMatch(/Only PDF or PNG files/);
    expect(fileProblem(q, { name: "a.pdf", size: 4096 })).toMatch(/This file is 4 KB\. The limit is 1 KB\./);
    expect(fileProblem(q, { name: "a.PDF", size: 0 })).toMatch(/empty/);
    expect(fileProblem(q, { name: "a.Pdf", size: 10 })).toBeNull();
    const upload = vi.fn();
    render(<Up upload={upload} />);
    expect(screen.getByText(/PDF or PNG files, up to 1 KB/)).toBeInTheDocument();
    pick(file("report.docx", 10));
    expect(screen.getByRole("alert")).toHaveTextContent(/Only PDF or PNG/);
    expect(upload).not.toHaveBeenCalled();
  });

  it("uploads, shows the file, replaces and removes it; the answer is the upload ids", async () => {
    let n = 0;
    const upload = vi.fn(async (f: File) => ({ id: `up_${++n}`, name: f.name, size: f.size }));
    render(<Up upload={upload} />);
    pick(file("report.pdf", 100));
    expect(await screen.findByText("report.pdf")).toBeInTheDocument();
    expect(shown()).toBe('["up_1"]');
    fireEvent.click(screen.getByRole("button", { name: "Replace" }));
    pick(file("better.png", 50));
    expect(await screen.findByText("better.png")).toBeInTheDocument();
    expect(shown()).toBe('["up_2"]');
    fireEvent.click(screen.getByRole("button", { name: "Remove better.png" }));
    expect(shown()).toBe("none");
    expect(screen.getByRole("button", { name: "Choose a file" })).toBeInTheDocument();
  });

  it("restores the file name after a reload and explains an offline upload", async () => {
    rememberUpload("a_9", { id: "up_7", name: "scan.pdf", size: 900 });
    const upload = vi.fn().mockRejectedValue(new NetworkError());
    render(<Up upload={upload} initial={["up_7"]} />);
    expect(screen.getByText("scan.pdf")).toBeInTheDocument();
    expect(screen.getByText("900 B")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Replace" }));
    pick(file("new.pdf", 10));
    expect(await screen.findByRole("alert")).toHaveTextContent(/Uploading needs the connection/);
    expect(shown()).toBe('["up_7"]');
  });
});

// ---------------------------------------------------------------- coding with several languages

function fakeRunner(lang: "python" | "javascript"): Runner {
  return {
    language: lang,
    prepare: () => Promise.resolve(),
    run: (_code: string, stdin: string): Promise<RunResult> =>
      Promise.resolve({ status: "ok", stdout: stdin.trim().split(" ").reverse().join(" ") + "\n", stderr: "", timeMs: 2 }),
    dispose: () => undefined,
  };
}

describe("coding question with several languages", () => {
  const q = sq({
    id: "q5",
    type: "code",
    prompt: "Reverse a list",
    points: 30,
    languages: [
      { key: "PY3", name: "Python 3", runner: "python" },
      { key: "CPP17", name: "C++17", runner: "cpp" },
      { key: "RUST", name: "Rust", runner: null },
    ],
    templates: { PY3: "def reverse(xs):\n    pass\n", CPP17: "int main() {}\n" },
    tests: [{ input: "1 2 3\n", expected: "3 2 1\n" }],
    time_limit_ms: 2000,
  });

  function Code({ initial }: { initial?: AnswerValue }) {
    const [value, setValue] = useState<AnswerValue | undefined>(initial);
    return (
      <>
        <CodeQuestion question={q} value={value} onChange={setValue} prompt={<h1>Reverse a list</h1>} attemptId="a_5" />
        <output data-testid="value">{value === undefined ? "none" : JSON.stringify(value)}</output>
      </>
    );
  }
  const editorText = () => document.querySelector(".cm-content")?.textContent ?? "";

  it("offers the languages, starts with the first one's template and runs the samples", async () => {
    setRunnerForTests("python", fakeRunner("python"));
    render(<Code />);
    const picker = await screen.findByTestId("code-language-picker");
    expect([...(picker as HTMLSelectElement).options].map((o) => o.textContent)).toEqual(["Python 3", "C++17", "Rust"]);
    expect(picker).toHaveValue("PY3");
    await waitFor(() => expect(editorText()).toContain("def reverse(xs):"));
    fireEvent.click(screen.getByRole("button", { name: /Run samples/ }));
    expect(await screen.findByTestId("samples-summary")).toHaveTextContent("1 of 1 sample test passed");
  });

  it("switching language shows that language's template, keeps each draft and saves {language, source}", async () => {
    render(<Code initial={{ language: "PY3", source: "print('mine')\n" }} />);
    const picker = await screen.findByTestId("code-language-picker");
    await waitFor(() => expect(editorText()).toContain("print('mine')"));
    fireEvent.change(picker, { target: { value: "CPP17" } });
    await waitFor(() => expect(editorText()).toContain("int main() {}"));
    expect(JSON.parse(shown()!)).toEqual({ language: "CPP17", source: "int main() {}\n" });
    expect(codeDrafts("a_5", "q5").PY3).toBe("print('mine')\n");
    // C++ has no runner in the app
    expect(screen.getByTestId("code-no-run")).toHaveTextContent(RUNS_AFTER_HAND_IN);
    expect(screen.queryByRole("button", { name: /Run samples/ })).toBeNull();
    fireEvent.change(picker, { target: { value: "PY3" } });
    await waitFor(() => expect(editorText()).toContain("print('mine')"));
    expect(JSON.parse(shown()!)).toEqual({ language: "PY3", source: "print('mine')\n" });
    // no template and no runner: empty editor, no answer yet
    fireEvent.change(picker, { target: { value: "RUST" } });
    expect(shown()).toBe("none");
    expect(screen.getByTestId("code-no-run")).toHaveTextContent("This language runs after you hand in.");
  });

  it("restores the saved language", async () => {
    render(<Code initial={{ language: "CPP17", source: "// saved\n" }} />);
    expect(await screen.findByTestId("code-language-picker")).toHaveValue("CPP17");
    await waitFor(() => expect(editorText()).toContain("// saved"));
    expect(screen.getByTestId("code-no-run")).toBeInTheDocument();
  });

  it("drafts and file names go at sign-out", () => {
    rememberUpload("a_5", { id: "up_1", name: "x.pdf", size: 1 });
    localStorage.setItem("fairplay:code-drafts:a_5", JSON.stringify({ q5: { PY3: "x" } }));
    clearExamDataOnSignOut();
    expect(localStorage.getItem("fairplay:oj-files:a_5")).toBeNull();
    expect(localStorage.getItem("fairplay:code-drafts:a_5")).toBeNull();
  });
});

// ---------------------------------------------------------------- exam page

describe("exam page with a DKU OJ exam", () => {
  const now = Date.now();
  const view = (answers: AnswerMap = {}): AttemptView =>
    ({
      attempt_id: "a_1",
      exam_id: "e_1",
      title: "CS 101 Midterm",
      duration_min: 90,
      started_at: new Date(now - 60_000).toISOString(),
      deadline_at: new Date(now + 90 * 60_000).toISOString(),
      server_time: new Date(now).toISOString(),
      source: "dkuoj",
      questions: [
        sq({ id: "p000125", type: "fill_blank", label: "Q2", title: "Fill", points: 6, partial: true, prompt: "A ___ maps keys.", blanks: 1 }),
        sq({ id: "p000123", type: "mcq", label: "Q1", points: 10, prompt: "See ![cell](fp-asset:cell.png) ~O(1)~", options: ["Array", "Hash table"] }),
      ],
      answers,
      status: "in_progress",
      tools: { calculator: "off", open_book: false, websites: [] },
    }) as AttemptView;

  beforeEach(() => {
    setToken("student", "S");
    localStorage.setItem("fairplay:session:a_1", "sess-1");
    api.student.heartbeat.mockResolvedValue({ ok: true, status: "in_progress", server_time: new Date().toISOString(), deadline_at: view().deadline_at });
    api.student.saveAnswers.mockImplementation(async (_a: string, _x: unknown, seq: number) => ({ saved: true, client_seq: seq, server_time: "" }));
    api.student.events.mockResolvedValue({ ok: true, stored: 0 });
    api.oj.attemptAsset.mockResolvedValue(new Blob(["png"]));
    Object.defineProperty(URL, "createObjectURL", { value: vi.fn(() => "blob:cell"), configurable: true, writable: true });
  });
  afterEach(() => bridge._resetBridgeForTests());

  function renderExam() {
    return render(
      <MemoryRouter initialEntries={["/student/exam/a_1"]}>
        <Routes>
          <Route path="/student/exam/:attemptId" element={<ExamTakingPage />} />
        </Routes>
      </MemoryRouter>,
    );
  }

  it("shows label, points and partial credit; saves a blank through the queue; loads pictures", async () => {
    api.student.getAttempt.mockResolvedValue(view());
    renderExam();
    const blank = await screen.findByLabelText("Blank 1");
    expect(screen.getByTestId("question-points")).toHaveTextContent("Q2 · 6 points · Fill in the blank · partial credit");
    fireEvent.change(blank, { target: { value: "hash map" } });
    await waitFor(() =>
      expect(api.student.saveAnswers).toHaveBeenCalledWith("a_1", { p000125: ["hash map"] }, expect.any(Number)),
      { timeout: 3000 },
    );
    // pictures of every question are fetched while online (asset URL through the API)
    await waitFor(() => expect(api.oj.attemptAsset).toHaveBeenCalledWith("a_1", "cell.png"));
    fireEvent.click(screen.getByRole("button", { name: /Next/ }));
    expect(screen.getByTestId("question-points")).toHaveTextContent("Q1 · 10 points");
    expect(document.querySelector("#question-prompt .katex")).not.toBeNull();
    await waitFor(() => expect(screen.getByAltText("cell")).toHaveAttribute("src", "blob:cell"));
  });

  it("restores saved answers after a reload", async () => {
    api.student.getAttempt.mockResolvedValue(view({ p000125: ["dictionary"] }));
    renderExam();
    expect(await screen.findByLabelText("Blank 1")).toHaveValue("dictionary");
    act(() => undefined);
  });
});

// ---------------------------------------------------------------- teacher

const ojExam = (over: Partial<Exam> = {}): Exam =>
  ({
    id: "e_1",
    title: "CS 101 Midterm",
    duration_min: 90,
    status: "draft",
    start_code: null,
    questions: [
      { id: "p1", type: "mcq", prompt: "Which is **O(1)**?", points: 10, options: ["Array", "Hash table"], correct_index: null,
        answer_number: null, tolerance: null, accepted_answers: [], rubric: [], min_words: null, max_words: null,
        prompt_format: "oj_md", label: "Q1", title: "Hash maps", partial: false },
    ],
    created_at: "2026-11-20T09:14:00Z",
    question_count: 1,
    total_points: 10,
    course: "CS 101",
    room: "",
    scheduled_at: null,
    shuffle_questions: false,
    grades_released: false,
    released_at: null,
    closed_at: null,
    pending_grading: 0,
    source: "dkuoj",
    oj_url: "https://oj.example.edu/contest/cs101a-midterm",
    ...over,
  }) as Exam;

function renderTeacher(path: string, route: string, el: JSX.Element) {
  setToken("teacher", "T");
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path={route} element={el} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("teacher views of a DKU OJ exam", () => {
  it("the editor is read-only with the Edit in DKU OJ banner and link", async () => {
    api.teacher.getExam.mockResolvedValue(ojExam());
    renderTeacher("/teacher/exams/e_1/edit", "/teacher/exams/:id/edit", <ExamSetupPage />);
    const banner = await screen.findByTestId("oj-edit-banner");
    expect(banner).toHaveTextContent(EDIT_IN_OJ);
    expect(within(banner).getByRole("link", { name: "Open in DKU OJ" })).toHaveAttribute("href", "https://oj.example.edu/contest/cs101a-midterm");
    expect(screen.queryByRole("form", { name: "Exam setup" })).toBeNull();
    expect(screen.queryByRole("link", { name: "Edit questions" })).toBeNull();
    expect(screen.getByText("O(1)").tagName).toBe("STRONG");     // markdown prompt
    expect(screen.getByText(/partial|10 pts/)).toBeInTheDocument();
  });

  it("the exam page has no Edit or Delete, shows the banner, and the results say Graded in DKU OJ", async () => {
    api.teacher.getExam.mockResolvedValue(ojExam({ status: "closed" }));
    api.teacher.results.mockResolvedValue({
      exam_id: "e_1", title: "CS 101 Midterm", course: "CS 101", total_points: 10, average_percent: null, median_percent: null,
      grades_released: false, released_at: null, pending_grading: 0, submitted_count: 1, attempt_count: 1, flags_total: 0,
      flags_open: 0, score_buckets: [], questions: [], source: "dkuoj",
      attempts: [{ attempt_id: "a_1", username: "ax19", name: "Avazbek K", status: "submitted", auto_submitted: false, score: null,
        total_points: 10, percent: null, submitted_at: "2026-11-27T02:00:00Z", receipt: "K7Q2-M9XD", answers: [], events: [],
        needs_grading: 0, overall_comment: "", flag_count: 2 }],
    } satisfies Results);
    renderTeacher("/teacher/exams/e_1", "/teacher/exams/:id", <TeacherExamPage />);
    expect(await screen.findByTestId("oj-edit-banner")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Edit" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Delete" })).toBeNull();
    const graded = await screen.findByTestId("oj-graded-banner");
    expect(graded).toHaveTextContent(GRADED_IN_OJ);
    expect(screen.getByText("K7Q2-M9XD")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Release grades" })).toBeNull();
  });

  it("the grading page says Graded in DKU OJ", async () => {
    api.teacher.getExam.mockResolvedValue(ojExam({ status: "closed" }));
    api.teacher.grading.mockResolvedValue({ exam_id: "e_1", title: "x", course: "", status: "closed", grades_released: false, questions: [], items: [] });
    renderTeacher("/teacher/exams/e_1/grading", "/teacher/exams/:id/grading", <GradingPage />);
    expect(await screen.findByTestId("oj-graded-banner")).toHaveTextContent(GRADED_IN_OJ);
  });
});

// ---------------------------------------------------------------- student grades

describe("released DKU OJ results", () => {
  it("shows the total, points per question and the feedback markdown", async () => {
    setToken("student", "S");
    api.student.feedback.mockResolvedValue({
      exam_id: "e_1", title: "CS 101 Midterm", course: "CS 101", submitted_at: "2026-11-27T02:00:00Z", receipt: "K7Q2-M9XD",
      score: 82, total_points: 100, percent: 82, class_average_percent: null, overall_comment: "", source: "dkuoj",
      questions: [
        { question_id: "p1", index: 0, type: "mcq", prompt: "Which is ~O(1)~?", options: ["Array", "Hash table"], points: 10,
          points_awarded: 10, status: "correct", given: 1, correct_answer: null, tolerance: null, comment: "", rubric: [],
          rubric_scores: null, label: "Q1", title: "Hash maps", prompt_format: "oj_md", feedback_md: "" },
        { question_id: "p5", index: 4, type: "code", prompt: "Reverse", options: [], points: 30, points_awarded: 12,
          status: "partial", given: { language: "PY3", source: "print(1)" }, correct_answer: null, tolerance: null, comment: "",
          rubric: [], rubric_scores: null, label: "Q5", prompt_format: "oj_md", feedback_md: "Passed **4 of 10** tests." },
      ],
    } as StudentFeedback);
    render(
      <MemoryRouter initialEntries={["/student/grades/e_1"]}>
        <Routes>
          <Route path="/student/grades/:examId" element={<FeedbackPage />} />
        </Routes>
      </MemoryRouter>,
    );
    expect(await screen.findByText("Graded in DKU OJ.")).toBeInTheDocument();
    const cards = screen.getAllByTestId("oj-feedback-question");
    expect(cards).toHaveLength(2);
    expect(within(cards[0]!).getByRole("heading", { name: "Q1 · Hash maps" })).toBeInTheDocument();
    expect(within(cards[0]!).getByText(/10 \/ 10/)).toBeInTheDocument();
    expect(within(cards[1]!).getByText("12 / 30 · partial")).toBeInTheDocument();
    expect(within(cards[1]!).getByTestId("oj-feedback").querySelector(".fp-md strong")?.textContent).toBe("4 of 10");
    expect(within(cards[1]!).getByText("print(1)")).toBeInTheDocument();
    expect(document.querySelector(".stu-fb-score b")).toHaveTextContent("82 / 100");
  });
});
