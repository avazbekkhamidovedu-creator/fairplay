// Phase 4: coding questions in the teacher editor (draft logic + form), the student's
// exam screen (split layout, fixed language, sample tests, custom input, write-only), the
// grading page panel and bulk run, and the results card.
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { EditorView } from "@codemirror/view";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import type { Exam, GradingItem, GradingQuestion, StudentQuestion } from "../api/types";
import { CodeQuestion } from "../components/student/CodeQuestion";
import { FeedbackQuestionCard } from "../components/student/FeedbackQuestion";
import { CodeQuestionEditor } from "../components/teacher/CodeQuestionEditor";
import { BulkCodeRun, CodeAnswerPanel, canAutoRun } from "../components/teacher/CodeGrading";
import type { RunResult, Runner } from "../lib/code/runner";
import { setRunnerForTests } from "../lib/code/runners";
import {
  copyQuestions,
  draftFromExam,
  newQuestion,
  newTestRow,
  questionMeta,
  testPointsSum,
  toExamInput,
  validateDraft,
  type DraftQuestion,
} from "../pages/teacher/examDraft";
import { initialForm, isPending } from "../pages/teacher/gradingLogic";

const api = vi.hoisted(() => ({ saveGrade: vi.fn(), grading: vi.fn(async () => ({ items: [] as GradingItem[] })) }));
vi.mock("../api/client", async (importOriginal) => {
  const orig = await importOriginal<typeof import("../api/client")>();
  return { ...orig, teacherApi: { ...orig.teacherApi, ...api } };
});

function fakeRunner(fn: (code: string, stdin: string) => RunResult, lang: "python" | "javascript" = "python"): Runner & { calls: string[] } {
  const calls: string[] = [];
  return {
    language: lang,
    calls,
    prepare: () => Promise.resolve(),
    run: (code, stdin) => {
      calls.push(stdin);
      return Promise.resolve(fn(code, stdin));
    },
    dispose: () => undefined,
  };
}

/** A "max of a list" program: right unless the input has a negative number. */
const maxRunner = () =>
  fakeRunner((_code, stdin) => {
    if (stdin.includes("loop")) return { status: "timeout", stdout: "", stderr: "Time limit exceeded (1 s).", timeMs: 1000 };
    const nums = stdin.split(/\s+/).slice(1).filter(Boolean).map(Number);
    return { status: "ok", stdout: `${nums.some((n) => n < 0) ? 0 : Math.max(...nums)}\n`, stderr: "", timeMs: 4 };
  });

beforeAll(() => {
  // jsdom has no layout: CodeMirror measures text ranges
  const rect = { x: 0, y: 0, top: 0, left: 0, right: 0, bottom: 0, width: 0, height: 0, toJSON: () => ({}) };
  Range.prototype.getClientRects ??= (() => ({ length: 0, item: () => null, [Symbol.iterator]: [][Symbol.iterator] })) as never;
  Range.prototype.getBoundingClientRect ??= (() => rect) as never;
});

afterEach(() => {
  setRunnerForTests("python", null);
  setRunnerForTests("javascript", null);
  api.saveGrade.mockReset();
});

// ---------------------------------------------------------------- teacher: draft logic

function codeQ(over: Partial<DraftQuestion> = {}): DraftQuestion {
  return { ...newQuestion("code"), prompt: "Print the largest number.", ...over };
}
const draftWith = (...qs: DraftQuestion[]) => ({ ...draftFromExam(), title: "Coding quiz", questions: qs });

describe("coding question draft", () => {
  it("starts with one visible and one hidden test, the exam language and 2 s", () => {
    const q = newQuestion("code");
    expect(q.type).toBe("code");
    expect(q.points).toBe("10");
    expect(q.language).toBe("");
    expect(q.timeLimit).toBe("2000");
    expect(q.tests.map((t) => t.visible)).toEqual([true, false]);
    expect(questionMeta(q)).toBe("Coding · exam language · 2 tests · 10 pts");
    expect(questionMeta({ ...q, language: "cpp", writeOnly: true })).toBe("Coding · C++ · write only · 10 pts");
  });

  it("builds the API body (null language = exam default) and the exam default language", () => {
    const q = codeQ({
      starter: "n = int(input())\n",
      tests: [newTestRow(true, "3\n1 5 2\n", "5\n", "4"), newTestRow(false, "1\n-1\n", "-1", "6")],
    });
    const d = { ...draftWith(q), defaultLanguage: "javascript" as const };
    expect(validateDraft(d).byKey[q.key]).toBeUndefined();
    const input = toExamInput(d);
    expect(input.default_language).toBe("javascript");
    expect(input.questions[0]).toMatchObject({
      type: "code",
      language: null,
      starter_code: "n = int(input())\n",
      tests: [
        { input: "3\n1 5 2\n", expected: "5\n", visible: true, points: 4 },
        { input: "1\n-1\n", expected: "-1", visible: false, points: 6 },
      ],
      write_only: false,
      time_limit_ms: 2000,
      options: [],
      accepted_answers: [],
      rubric: [],
    });
    expect(toExamInput({ ...d, questions: [{ ...q, language: "java" }] }).questions[0]!.language).toBe("java");
  });

  it("checks test points, time limit and paper exams", () => {
    const q = codeQ({ tests: [newTestRow(true, "", "1", "3")] });
    expect(validateDraft(draftWith(q)).byKey[q.key]?.tests).toMatch(/add up to 3, but the question is worth 10/);
    expect(validateDraft(draftWith({ ...q, tests: [newTestRow(true, "", "1", "x")] })).byKey[q.key]?.tests).toMatch(/whole numbers/);
    // no test points at all is fine: points are shared by tests passed
    expect(validateDraft(draftWith({ ...q, tests: [newTestRow(true, "", "1", "")] })).byKey[q.key]).toBeUndefined();
    expect(validateDraft(draftWith({ ...q, tests: [], timeLimit: "50" })).byKey[q.key]?.timeLimit).toMatch(/100 to 10000/);
    expect(validateDraft(draftWith({ ...q, tests: [], timeLimit: "10001" })).byKey[q.key]?.timeLimit).toBeTruthy();
    const paper = { ...draftWith({ ...q, tests: [] }), delivery: "paper" as const };
    expect(validateDraft(paper).byKey[q.key]?.prompt).toMatch(/on computers/);
    expect(testPointsSum([newTestRow(true, "", "", "2"), newTestRow(false, "", "", ""), newTestRow(false, "", "", "x")])).toBe(2);
  });

  it("round-trips an exam and copies tests as new rows", () => {
    const exam = {
      id: "e_1",
      default_language: "cpp",
      questions: [
        {
          id: "q_1",
          type: "code",
          prompt: "P",
          points: 5,
          options: [],
          correct_index: null,
          answer_number: null,
          tolerance: null,
          accepted_answers: [],
          rubric: [],
          min_words: null,
          max_words: null,
          language: "python",
          starter_code: "pass",
          tests: [{ input: "a", expected: "b", visible: false, points: 5 }],
          write_only: true,
          time_limit_ms: 1500,
        },
      ],
    } as unknown as Exam;
    const d = draftFromExam({ ...draftExamDefaults, ...exam } as Exam);
    expect(d.defaultLanguage).toBe("cpp");
    expect(d.questions[0]).toMatchObject({ language: "python", starter: "pass", writeOnly: true, timeLimit: "1500" });
    expect(d.questions[0]!.tests[0]).toMatchObject({ input: "a", expected: "b", visible: false, points: "5" });
    const copy = copyQuestions({ ...draftExamDefaults, ...exam } as Exam)[0]!;
    expect(copy.tests[0]!.key).not.toBe(d.questions[0]!.tests[0]!.key);
  });
});

const draftExamDefaults: Partial<Exam> = {
  title: "T",
  duration_min: 10,
  status: "draft",
  start_code: null,
  created_at: "",
  question_count: 1,
  total_points: 5,
  course: "",
  room: "",
  scheduled_at: null,
  shuffle_questions: false,
  grades_released: false,
  released_at: null,
  closed_at: null,
  pending_grading: 0,
};

// ---------------------------------------------------------------- teacher: form

function EditorHarness({ initial }: { initial: DraftQuestion }) {
  const [q, setQ] = useState(initial);
  return (
    <>
      <CodeQuestionEditor q={q} id={(s) => `qx-${s}`} errors={{}} onChange={(p) => setQ((x) => ({ ...x, ...p }))} defaultLanguage="python" />
      <output data-testid="state">{JSON.stringify({ language: q.language, tests: q.tests.map((t) => [t.input, t.expected, t.visible, t.points]), writeOnly: q.writeOnly, timeLimit: q.timeLimit })}</output>
    </>
  );
}

describe("coding question form", () => {
  const state = () => JSON.parse(screen.getByTestId("state").textContent ?? "{}");

  it("edits language, tests (visible / hidden, points), write only and time limit", async () => {
    const user = userEvent.setup();
    render(<EditorHarness initial={codeQ()} />);
    const lang = screen.getByLabelText("Language") as HTMLSelectElement;
    expect(lang.options[0]!.textContent).toBe("Exam default (Python 3)");
    expect([...lang.options].map((o) => o.value)).toEqual(["", "python", "javascript", "c", "cpp", "java"]);
    await user.selectOptions(lang, "cpp");
    expect(state().language).toBe("cpp");
    expect(screen.getByText(/C\+\+ cannot run inside FairPlay yet/)).toBeInTheDocument();

    const rows = screen.getAllByTestId("code-test-row");
    expect(rows).toHaveLength(2);
    await user.type(within(rows[0]!).getByLabelText("Input (stdin)"), "3");
    await user.type(within(rows[0]!).getByLabelText("Expected output"), "9");
    await user.type(within(rows[0]!).getByLabelText("Test 1 points"), "4");
    await user.click(within(rows[0]!).getByRole("button", { name: "Hidden" }));
    expect(state().tests[0]).toEqual(["3", "9", false, "4"]);
    await user.click(screen.getByRole("button", { name: "+ Visible test" }));
    expect(screen.getAllByTestId("code-test-row")).toHaveLength(3);
    await user.click(screen.getByRole("button", { name: "Remove test 2" }));
    expect(state().tests).toHaveLength(2);
    expect(screen.getByText(/1 visible · 1 hidden · test points 4 \/ 10/)).toBeInTheDocument();

    await user.click(screen.getByRole("switch"));
    expect(state().writeOnly).toBe(true);
    const tl = screen.getByLabelText("Time limit per test (milliseconds)");
    await user.clear(tl);
    await user.type(tl, "1500");
    expect(state().timeLimit).toBe("1500");
    // the starter code editor loads (CodeMirror, or the plain box when it cannot)
    await waitFor(() => expect(screen.queryByText("Loading the editor…")).toBeNull());
  });
});

// ---------------------------------------------------------------- student: exam screen

function sq(over: Partial<StudentQuestion> = {}): StudentQuestion {
  return {
    id: "q_code",
    type: "code",
    prompt: "Print the largest number.",
    points: 10,
    options: [],
    language: "python",
    starter_code: "n = int(input())\n",
    tests: [
      { input: "3\n1 5 2\n", expected: "5\n" },
      { input: "2\n-1 -3\n", expected: "-1" },
    ],
    write_only: false,
    time_limit_ms: 1000,
    ...over,
  };
}

function renderCode(q: StudentQuestion, onChange = vi.fn()) {
  render(<CodeQuestion question={q} value={undefined} onChange={onChange} prompt={<h1 id="question-prompt">{q.prompt}</h1>} />);
  return onChange;
}

describe("student coding question", () => {
  it("shows the fixed language, the examples and runs the samples (no score)", async () => {
    const r = maxRunner();
    setRunnerForTests("python", r);
    const user = userEvent.setup();
    renderCode(sq({ id: "q_a" }));
    expect(screen.getByTestId("code-language")).toHaveTextContent("Python 3");
    expect(screen.queryByRole("combobox")).toBeNull(); // students never choose the language
    expect(screen.getByText("Example 1")).toBeInTheDocument();
    expect(screen.getByText("Time limit 1 s")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /Run samples/ }));
    expect(await screen.findByTestId("samples-summary")).toHaveTextContent("1 of 2 sample tests passed");
    expect(r.calls).toEqual(["3\n1 5 2\n", "2\n-1 -3\n"]);
    expect(screen.getByText("Passed")).toBeInTheDocument();
    expect(screen.getByText("Wrong answer")).toBeInTheDocument();
    expect(screen.queryByText(/points?\b/i)).toBeNull();
  });

  it("runs custom input and shows a time-out", async () => {
    setRunnerForTests("python", maxRunner());
    const user = userEvent.setup();
    renderCode(sq({ id: "q_b" }));
    await user.click(screen.getByRole("tab", { name: "Custom input" }));
    fireEvent.change(screen.getByLabelText("Input"), { target: { value: "loop" } });
    await user.click(screen.getByRole("button", { name: "Run" }));
    const out = await screen.findByTestId("custom-output");
    expect(within(out).getByText("Time limit exceeded")).toBeInTheDocument();
    expect(within(out).getByText(/Time limit exceeded \(1 s\)\./)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Input"), { target: { value: "2\n4 7" } });
    await user.click(screen.getByRole("button", { name: "Run" }));
    await waitFor(() => expect(screen.getByTestId("custom-output").querySelector("pre")!.textContent).toBe("7\n"));
  });

  it("starts from the starter code and saves every edit as the answer", async () => {
    const onChange = renderCode(sq({ id: "q_s" }));
    await waitFor(() => expect(document.querySelector(".cm-content")).not.toBeNull());
    const content = document.querySelector<HTMLElement>(".cm-content")!;
    expect(content).toHaveAttribute("aria-label", "Your Python 3 code");
    expect(content.textContent).toContain("n = int(input())");
    const view = EditorView.findFromDOM(content)!;
    view.dispatch({ changes: { from: view.state.doc.length, insert: "print(n)" } });
    expect(onChange).toHaveBeenLastCalledWith("n = int(input())\nprint(n)");
    view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: "  " } });
    expect(onChange).toHaveBeenLastCalledWith(undefined); // blank = not answered
  });

  it("write-only: editor, no Run, the note", () => {
    renderCode(sq({ id: "q_c", write_only: true }));
    expect(screen.getByTestId("code-no-run")).toHaveTextContent("Write your answer. It is checked after the exam.");
    expect(screen.queryByRole("button", { name: /Run/ })).toBeNull();
    expect(screen.queryByRole("tab")).toBeNull();
  });

  it("C++: no Run, checked after the exam", () => {
    renderCode(sq({ id: "q_d", language: "cpp", tests: [] }));
    expect(screen.getByTestId("code-language")).toHaveTextContent("C++");
    expect(screen.getByTestId("code-no-run")).toHaveTextContent(
      "Run isn't available for C++ in this exam. Your code is checked after the exam.",
    );
  });

  it("shows a load problem instead of results when Python cannot load", async () => {
    setRunnerForTests("python", {
      language: "python",
      prepare: () => Promise.reject(new Error("Python could not load. It needs the connection to the exam server the first time.")),
      run: () => Promise.reject(new Error("no")),
      dispose: () => undefined,
    });
    const user = userEvent.setup();
    renderCode(sq({ id: "q_e" }));
    await user.click(screen.getByRole("button", { name: /Run samples/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/Python could not load/);
    expect(screen.getByRole("button", { name: /Run samples/ })).not.toBeDisabled();
  });
});

// ---------------------------------------------------------------- teacher: grading

const gq: GradingQuestion = {
  question_id: "q_code",
  index: 0,
  type: "code",
  prompt: "Print the largest number.",
  points: 10,
  rubric: [],
  to_grade: 1,
  graded: 0,
  language: "python",
  time_limit_ms: 1000,
  tests: [
    { input: "3\n1 5 2\n", expected: "5", visible: true, points: 4 },
    { input: "2\n-1 -3\n", expected: "-1", visible: false, points: 6 },
  ],
};

function item(over: Partial<GradingItem> = {}): GradingItem {
  return {
    attempt_id: "a1",
    anon_id: "#A1B2",
    username: "alice",
    name: "Alice",
    question_id: "q_code",
    answer: "print(max(...))",
    word_count: null,
    auto_points: null,
    grade: null,
    flag_count: 0,
    ...over,
  };
}

describe("grading coding answers", () => {
  it("runs all tests (hidden too) and fills in the points of the passed ones", async () => {
    const r = maxRunner();
    setRunnerForTests("python", r);
    const onScored = vi.fn();
    const user = userEvent.setup();
    render(<CodeAnswerPanel question={gq} code="print(max(...))" onScored={onScored} />);
    expect(screen.getByText(/2 tests \(1 hidden\)/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Run all tests" }));
    await waitFor(() => expect(onScored).toHaveBeenCalled());
    expect(onScored.mock.calls[0]![0]).toMatchObject({ points: 4, comment: "Passed 1 of 2 tests (samples 1 of 1, hidden 0 of 1)." });
    expect(r.calls).toHaveLength(2);
    expect(screen.getByText("hidden")).toBeInTheDocument();
    expect(screen.getByText("4 / 4 pts")).toBeInTheDocument();
    expect(screen.getByText("0 / 6 pts")).toBeInTheDocument();
  });

  it("C/C++/Java and blank answers: no Run button", () => {
    render(<CodeAnswerPanel question={{ ...gq, language: "java" }} code="class A {}" onScored={vi.fn()} />);
    expect(screen.queryByRole("button", { name: "Run all tests" })).toBeNull();
    expect(screen.getByText(/Java cannot run inside FairPlay yet/)).toBeInTheDocument();
    expect(canAutoRun({ ...gq, language: "java" })).toBe(false);
    expect(canAutoRun({ ...gq, tests: [] })).toBe(false);
    expect(canAutoRun(gq)).toBe(true);
  });

  it("blank code answers count as graded; the form starts empty for written answers", () => {
    expect(isPending(item({ answer: "  " }), gq)).toBe(false);
    expect(isPending(item(), gq)).toBe(true);
    expect(initialForm(item(), gq).points).toBe("");
  });

  it("bulk run scores every ungraded coding answer and saves it", async () => {
    setRunnerForTests("python", maxRunner());
    api.saveGrade.mockImplementation(async (_e: string, aid: string, qid: string, input: { points: number; comment: string }) => ({
      attempt_id: aid,
      question_id: qid,
      grade: { ...input, rubric_scores: null, graded_at: "2026-10-23T00:00:00Z" },
    }));
    const graded = vi.fn();
    const user = userEvent.setup();
    const items = [item(), item({ attempt_id: "a2", anon_id: "#B", answer: "" }), item({ attempt_id: "a3", grade: { points: 1, comment: "", rubric_scores: null, graded_at: "" } }), item({ attempt_id: "a4" })];
    render(<BulkCodeRun examId="e_1" questions={[gq]} items={items} onItemGraded={graded} />);
    await user.click(screen.getByRole("button", { name: "Run all code tests (2)" }));
    await user.click(screen.getByRole("button", { name: "Run and save" }));
    await waitFor(() => expect(graded).toHaveBeenCalledTimes(2));
    expect(api.saveGrade.mock.calls.map((c) => c[1])).toEqual(["a1", "a4"]);
    expect(api.saveGrade.mock.calls[0]![3]).toEqual({ points: 4, comment: "Passed 1 of 2 tests (samples 1 of 1, hidden 0 of 1).", rubric_scores: null });
    expect(await screen.findByRole("status")).toHaveTextContent("Scored 2 answers from the tests");
  });

  it("bulk run is hidden when no coding question can run", () => {
    const { container } = render(<BulkCodeRun examId="e_1" questions={[{ ...gq, language: "cpp" }]} items={[item()]} onItemGraded={vi.fn()} />);
    expect(container).toBeEmptyDOMElement();
  });
});

// ---------------------------------------------------------------- student: results

describe("results card", () => {
  it("shows the student's code and the points", () => {
    render(
      <FeedbackQuestionCard
        q={{
          question_id: "q_code",
          index: 1,
          type: "code",
          prompt: "Print the largest number.",
          options: [],
          points: 10,
          points_awarded: 4,
          status: "partial",
          given: "print(5)\n",
          correct_answer: null,
          tolerance: null,
          comment: "Passed 1 of 2 tests.",
          rubric: [],
          rubric_scores: null,
          language: "python",
        }}
      />,
    );
    expect(screen.getByText("Question 2 · Coding")).toBeInTheDocument();
    expect(screen.getByTestId("feedback-code")).toHaveTextContent("YOUR CODE · Python 3");
    expect(screen.getByText("print(5)")).toBeInTheDocument();
    expect(screen.getByText("4 / 10 · partial")).toBeInTheDocument();
    expect(screen.queryByText("CORRECT ANSWER")).toBeNull();
  });
});
