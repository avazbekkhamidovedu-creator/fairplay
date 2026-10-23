// Exams from an external source: the web against the server as built (CONTRACT.md integrations —
// FairPlay server as implemented"): answer-file endpoint, shuffled
// options (saved with the original indexes), answer_files, package not here yet, bare
// string code answers.
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import type { AnswerValue, Exam, Results, StudentQuestion } from "../api/types";
import { ApiError, getToken, contentApi, setToken, teacherApi } from "../api/client";
import { AnswerInput } from "../components/student/AnswerInput";
import { CodeQuestion } from "../components/student/CodeQuestion";
import { FileUploadInput } from "../components/student/QuestionInputs";
import { setAttemptSession } from "../lib/appSession";
import { optionOrder } from "../lib/answerTypes";
import TeacherExamPage from "../pages/teacher/TeacherExamPage";

beforeAll(() => {
  const rect = { x: 0, y: 0, top: 0, left: 0, right: 0, bottom: 0, width: 0, height: 0, toJSON: () => ({}) };
  Range.prototype.getClientRects ??= (() => ({ length: 0, item: () => null, [Symbol.iterator]: [][Symbol.iterator] })) as never;
  Range.prototype.getBoundingClientRect ??= (() => rect) as never;
});
afterEach(() => vi.restoreAllMocks());

describe("answer files endpoint", () => {
  it("POSTs only the file to .../files?question_id= with the sign-in and session", async () => {
    setToken("student", "S");
    setAttemptSession("a_1", "sess-1");
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({ id: "f_1", question_id: "q_abc", name: "r.pdf", size: 3, sha256: "ab", created_at: "2026-11-27T01:10:00Z" }),
        { status: 201, headers: { "Content-Type": "application/json" } },
      ),
    );
    const up = await contentApi.uploadAnswerFile("a_1", "q_abc", new File(["abc"], "r.pdf"));
    expect(up).toEqual({ id: "f_1", question_id: "q_abc", name: "r.pdf", size: 3, sha256: "ab", created_at: "2026-11-27T01:10:00Z" });
    const [url, init] = fetchMock.mock.calls[0]!;
    expect(url).toBe("/api/student/attempts/a_1/files?question_id=q_abc");
    const headers = (init as RequestInit).headers as Record<string, string>;
    expect(headers.Authorization).toBe("Bearer S");
    expect(headers["X-FairPlay-Session"]).toBe("sess-1");
    const body = (init as RequestInit).body as FormData;
    expect([...body.keys()]).toEqual(["file"]);
  });

  it("shows file names from the attempt's answer_files", () => {
    const q = { id: "q_abc", type: "file_upload", prompt: "Upload", points: 10, options: [], prompt_format: "markdown",
      instructions_md: "One **PDF** please" } as StudentQuestion;
    render(
      <FileUploadInput question={q} value={["f_1"]} onChange={() => undefined} attemptId="a_1" upload={null}
        knownFiles={[{ id: "f_1", question_id: "q_abc", name: "report.pdf", size: 2048 }]} />,
    );
    expect(screen.getByText("report.pdf")).toBeInTheDocument();
    expect(screen.getByText("2 KB")).toBeInTheDocument();
    expect(screen.getByText("PDF").tagName).toBe("STRONG");
  });
});

describe("shuffled options (server does not shuffle)", () => {
  it("the order is a fixed permutation per seed", () => {
    const a = optionOrder(6, "e_1:7:q_1");
    expect([...a].sort()).toEqual([0, 1, 2, 3, 4, 5]);
    expect(optionOrder(6, "e_1:7:q_1")).toEqual(a);
    const others = ["e_1:8:q_1", "e_1:9:q_1", "e_1:10:q_1", "e_2:7:q_1"].map((s) => optionOrder(6, s).join());
    expect(others.some((o) => o !== a.join())).toBe(true);
  });

  function Pick({ q }: { q: StudentQuestion }) {
    const [v, setV] = useState<AnswerValue | undefined>(undefined);
    return (
      <>
        <h1 id="p">x</h1>
        <AnswerInput question={q} value={v} onChange={setV} labelledBy="p" shuffleSeed="e_1:7" />
        <output data-testid="v">{JSON.stringify(v ?? null)}</output>
      </>
    );
  }
  const options = ["zero", "one", "two", "three", "four", "five"];

  it("single choice: shown in the seeded order, saved with the original index", () => {
    const q = { id: "q_1", type: "mcq", prompt: "x", points: 1, options, prompt_format: "markdown", shuffle: true } as StudentQuestion;
    render(<Pick q={q} />);
    const order = optionOrder(6, "e_1:7:q_1");
    const radios = screen.getAllByRole("radio");
    expect(radios.map((r) => r.querySelector(".stu-option-text")?.textContent)).toEqual(order.map((i) => options[i]));
    fireEvent.click(radios[0]!);
    expect(screen.getByTestId("v").textContent).toBe(String(order[0]));
    fireEvent.keyDown(radios[0]!, { key: "ArrowDown" });
    expect(screen.getByTestId("v").textContent).toBe(String(order[1]));
  });

  it("several answers: saved as sorted original indexes", () => {
    const q = { id: "q_2", type: "mcq", prompt: "x", points: 1, options, prompt_format: "markdown", shuffle: true, multiple: true } as StudentQuestion;
    render(<Pick q={q} />);
    const order = optionOrder(6, "e_1:7:q_2");
    const boxes = screen.getAllByRole("checkbox");
    fireEvent.click(boxes[0]!);
    fireEvent.click(boxes[1]!);
    expect(JSON.parse(screen.getByTestId("v").textContent!)).toEqual([order[0], order[1]].sort((a, b) => a! - b!));
  });

  it("no shuffle flag: original order", () => {
    const q = { id: "q_3", type: "mcq", prompt: "x", points: 1, options, prompt_format: "markdown" } as StudentQuestion;
    render(<Pick q={q} />);
    expect(screen.getAllByRole("radio").map((r) => r.querySelector(".stu-option-text")?.textContent)).toEqual(options);
  });
});

describe("bare string code answer", () => {
  it("is the first language's code", async () => {
    const q = { id: "q_9", type: "code", prompt: "x", points: 5, options: [], prompt_format: "markdown",
      languages: [{ key: "PY3", name: "Python 3", runner: "python" }, { key: "CPP17", name: "C++17", runner: "cpp" }],
      templates: { PY3: "pass\n" }, tests: [] } as unknown as StudentQuestion;
    render(<CodeQuestion question={q} value={"print('old')\n"} onChange={() => undefined} prompt={<h1>x</h1>} attemptId="a_9" />);
    await waitFor(() => expect(document.querySelector(".cm-content")?.textContent).toContain("print('old')"));
    expect(screen.getByTestId("code-language-picker")).toHaveValue("PY3");
  });
});

describe("external exam before its questions arrive", () => {
  it("Open exam waits for the package", async () => {
    setToken("teacher", "T");
    vi.spyOn(teacherApi, "getExam").mockResolvedValue({
      id: "e_1", title: "Midterm", duration_min: 90, status: "draft", start_code: null, questions: [], created_at: "",
      question_count: 0, total_points: 0, course: "", room: "", scheduled_at: null, shuffle_questions: false,
      grades_released: false, released_at: null, closed_at: null, pending_grading: 0,
      external: { source: "campusjudge", name: "Campus Judge", url: "https://judge.example.edu/contest/m", read_only: true, graded_externally: true, package_ready: false },
    } as Exam);
    render(
      <MemoryRouter initialEntries={["/teacher/exams/e_1"]}>
        <Routes>
          <Route path="/teacher/exams/:id" element={<TeacherExamPage />} />
        </Routes>
      </MemoryRouter>,
    );
    expect(await screen.findByTestId("external-package-waiting")).toHaveTextContent("24 hours before the start");
    expect(screen.getByRole("button", { name: "Open exam" })).toBeDisabled();
  });
});

describe("one student's page of an externally graded exam (the report link target)", () => {
  it("shows the answers with a Graded in banner and no grade inputs", async () => {
    setToken("teacher", "T");
    const external = { source: "campusjudge", name: "Campus Judge", url: "https://judge.example.edu/contest/m",
      read_only: true, graded_externally: true, package_ready: true };
    vi.spyOn(teacherApi, "getExam").mockResolvedValue({
      id: "e_1", title: "Midterm", duration_min: 90, status: "closed", start_code: null, created_at: "",
      questions: [{ id: "q_1", type: "short", prompt: "Explain **hashing**", points: 10, options: [], prompt_format: "markdown",
        accepted_answers: [], rubric: [], images: [], topics: [] }],
      question_count: 1, total_points: 10, course: "", room: "", scheduled_at: null, shuffle_questions: false,
      grades_released: false, released_at: null, closed_at: null, pending_grading: 0, external,
    } as unknown as Exam);
    vi.spyOn(teacherApi, "results").mockResolvedValue({
      exam_id: "e_1", title: "Midterm", course: "", total_points: 10, average_percent: null, median_percent: null,
      grades_released: false, released_at: null, pending_grading: 0, submitted_count: 1, attempt_count: 1,
      flags_total: 0, flags_open: 0, score_buckets: [], questions: [], external,
      attempts: [{ attempt_id: "a_1", username: "ax19", name: "Avazbek", status: "submitted", auto_submitted: false,
        score: 0, total_points: 10, percent: 0, submitted_at: "2026-11-27T02:00:00Z", receipt: "R1", events: [],
        needs_grading: 0, overall_comment: "", flag_count: 0,
        answers: [{ question_id: "q_1", given: "It spreads keys.", correct: false, points_awarded: 0, max_points: 10,
          manual: false, pending: true, comment: "", rubric_scores: null }] }],
    } as unknown as Results);
    const { default: SubmissionReviewPage } = await import("../pages/teacher/SubmissionReviewPage");
    render(
      <MemoryRouter initialEntries={["/teacher/exams/e_1/submissions/a_1"]}>
        <Routes>
          <Route path="/teacher/exams/:id/submissions/:attemptId" element={<SubmissionReviewPage />} />
        </Routes>
      </MemoryRouter>,
    );
    expect(await screen.findByTestId("external-graded-banner")).toHaveTextContent("Graded in Campus Judge");
    expect(screen.getByText("It spreads keys.", { exact: false })).toBeInTheDocument();
    expect(screen.queryByRole("spinbutton")).toBeNull();
    expect(screen.queryByRole("button", { name: /save/i })).toBeNull();
  });
});

describe("pictures on a released grade page", () => {
  it("come from the grade asset route of the exam", async () => {
    setToken("student", "S");
    const blob = new Blob(["png"], { type: "image/png" });
    const get = vi.spyOn(contentApi, "gradeAsset").mockResolvedValue(blob);
    const had = URL.createObjectURL;
    URL.createObjectURL = (() => "blob:cell") as typeof URL.createObjectURL;
    try {
      const { ExternalFeedbackQuestionCard } = await import("../components/student/FeedbackQuestion");
      render(
        <ExternalFeedbackQuestionCard
          examId="e_9"
          q={{ question_id: "p1", index: 0, type: "short", prompt: "See ![cell](fp-asset:cell.png)", options: [],
            points: 10, points_awarded: 5, status: "partial", given: "x", correct_answer: null, tolerance: null,
            comment: "", rubric: [], rubric_scores: null, prompt_format: "markdown", comment_format: "markdown" } as never}
        />,
      );
      await waitFor(() => expect(document.querySelector("img")?.getAttribute("src")).toBe("blob:cell"));
      expect(get).toHaveBeenCalledWith("e_9", "cell.png");
    } finally {
      URL.createObjectURL = had;
    }
  });
});
