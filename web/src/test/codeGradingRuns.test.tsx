// 2026-10-23 (review fixes): the Python time limit counts only the student's code; runs can
// be stopped; the bulk run keeps grades entered by hand while it runs; copied questions keep
// the source exam's default language.
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import type { Exam, GradingItem, GradingQuestion } from "../api/types";
import { BulkCodeRun, CodeAnswerPanel, scoreCode } from "../components/teacher/CodeGrading";
import { PythonRunner } from "../lib/code/pythonRunner";
import type { RunResult, Runner } from "../lib/code/runner";
import { setRunnerFactoryForTests, setRunnerForTests } from "../lib/code/runners";
import { TestsAborted, runTests } from "../lib/code/runTests";
import { copyQuestions } from "../pages/teacher/examDraft";

const api = vi.hoisted(() => ({ saveGrade: vi.fn(), grading: vi.fn() }));
vi.mock("../api/client", async (importOriginal) => {
  const orig = await importOriginal<typeof import("../api/client")>();
  return { ...orig, teacherApi: { ...orig.teacherApi, ...api } };
});

beforeAll(() => {
  // jsdom has no layout: CodeMirror measures text ranges
  const rect = { x: 0, y: 0, top: 0, left: 0, right: 0, bottom: 0, width: 0, height: 0, toJSON: () => ({}) };
  Range.prototype.getClientRects ??= (() => ({ length: 0, item: () => null, [Symbol.iterator]: [][Symbol.iterator] })) as never;
  Range.prototype.getBoundingClientRect ??= (() => rect) as never;
});

afterEach(() => {
  setRunnerForTests("python", null);
  setRunnerFactoryForTests("python", null);
  api.saveGrade.mockReset();
  api.grading.mockReset();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

const ok = (stdout: string): RunResult => ({ status: "ok", stdout, stderr: "", timeMs: 1 });

/** A runner whose runs finish when the test says so (or when disposed). */
function manualRunner() {
  const pending: Array<(r: RunResult) => void> = [];
  let disposed = 0;
  const r: Runner = {
    language: "python",
    prepare: () => Promise.resolve(),
    run: () => new Promise<RunResult>((res) => pending.push(res)),
    dispose: () => {
      disposed += 1;
      for (const p of pending.splice(0)) p({ status: "unavailable", stdout: "", stderr: "Stopped.", timeMs: 0 });
    },
  };
  return { r, pending, disposed: () => disposed };
}

const gq: GradingQuestion = {
  question_id: "q_code",
  index: 0,
  type: "code",
  prompt: "Max",
  points: 10,
  rubric: [],
  to_grade: 1,
  graded: 0,
  language: "python",
  time_limit_ms: 1000,
  tests: [{ input: "1\n", expected: "1", visible: true, points: 10 }],
} as GradingQuestion;

function item(over: Partial<GradingItem> = {}): GradingItem {
  return {
    attempt_id: "a1",
    anon_id: "#A",
    username: "alice",
    name: "Alice",
    question_id: "q_code",
    answer: "print(1)",
    word_count: null,
    auto_points: null,
    grade: null,
    flag_count: 0,
    ...over,
  };
}

// ------------------------------------------------------------------ Python time limit

type Listener = (ev: { data: unknown }) => void;

/** A fake Pyodide worker: "load" → ready; "run" → started after `setupMs`, done `codeMs`
 *  later, after a reset of `resetMs` (between "ended" and "done"). */
function fakePyWorker(setupMs: number, codeMs: number, resetMs: number) {
  const made: FakeWorker[] = [];
  class FakeWorker {
    onmessage: Listener | null = null;
    onerror: ((ev: unknown) => void) | null = null;
    terminated = false;
    constructor() {
      made.push(this);
    }
    private send(data: unknown, ms: number) {
      setTimeout(() => !this.terminated && this.onmessage?.({ data }), ms);
    }
    postMessage(m: { type: string; id?: number }) {
      if (m.type === "load") this.send({ type: "ready" }, 0);
      if (m.type === "run") {
        const id = m.id;
        this.send({ type: "started", id }, setupMs);
        if (codeMs === Infinity) return;
        this.send({ type: "ended", id }, setupMs + codeMs);
        this.send({ type: "done", id, stdout: "hi\n", stderr: "", failed: false, timeMs: codeMs }, setupMs + codeMs + resetMs);
      }
    }
    terminate() {
      this.terminated = true;
    }
  }
  vi.stubGlobal("Worker", FakeWorker);
  return made;
}

describe("Python time limit", () => {
  it("does not count the worker's set-up or the reset after the run", async () => {
    fakePyWorker(150, 20, 200);
    const r = await new PythonRunner().run("print('hi')", "", 100);
    expect(r).toMatchObject({ status: "ok", stdout: "hi\n" });
  });

  it("still stops a program that runs past the limit", async () => {
    const made = fakePyWorker(10, Infinity, 0);
    const r = await new PythonRunner().run("while True: pass", "", 80);
    expect(r.status).toBe("timeout");
    expect(made[0]!.terminated).toBe(true);
  });

  it("dispose() ends a run in progress at once", async () => {
    fakePyWorker(10, Infinity, 0);
    const runner = new PythonRunner();
    const p = runner.run("while True: pass", "", 60_000);
    await new Promise((r) => setTimeout(r, 30));
    runner.dispose();
    expect(await p).toMatchObject({ status: "unavailable", stderr: "Stopped." });
  });
});

// ------------------------------------------------------------------ aborting

describe("stopping runs", () => {
  it("runTests rejects at once when aborted, and scoreCode disposes its runner", async () => {
    const m = manualRunner();
    const ac = new AbortController();
    const p = runTests(m.r, "x", [{ input: "", expected: "" }, { input: "", expected: "" }], 1000, undefined, ac.signal);
    ac.abort();
    await expect(p).rejects.toBeInstanceOf(TestsAborted);

    const m2 = manualRunner();
    setRunnerFactoryForTests("python", () => m2.r);
    const ac2 = new AbortController();
    const s = scoreCode(gq, "print(1)", undefined, ac2.signal);
    await Promise.resolve();
    ac2.abort();
    await expect(s).rejects.toBeInstanceOf(TestsAborted);
    expect(m2.disposed()).toBeGreaterThan(0);
  });

  it("per-answer run: Stop, and moving to another answer, dispose the runner", async () => {
    const runners: ReturnType<typeof manualRunner>[] = [];
    setRunnerFactoryForTests("python", () => {
      const m = manualRunner();
      runners.push(m);
      return m.r;
    });
    const onScored = vi.fn();
    const user = userEvent.setup();
    const { rerender } = render(<CodeAnswerPanel question={gq} code="print(1)" onScored={onScored} />);
    await user.click(screen.getByRole("button", { name: "Run all tests" }));
    await waitFor(() => expect(runners).toHaveLength(1));
    await user.click(screen.getByRole("button", { name: "Stop" }));
    expect(runners[0]!.disposed()).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: "Run all tests" })).toBeEnabled();

    await user.click(screen.getByRole("button", { name: "Run all tests" }));
    await waitFor(() => expect(runners).toHaveLength(2));
    rerender(<CodeAnswerPanel question={gq} code="print(2)" onScored={onScored} />);
    expect(runners[1]!.disposed()).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: "Run all tests" })).toBeEnabled();
    expect(onScored).not.toHaveBeenCalled();
  });

  it("per-answer run is stopped when the panel unmounts", async () => {
    const m = manualRunner();
    setRunnerFactoryForTests("python", () => m.r);
    const user = userEvent.setup();
    const { unmount } = render(<CodeAnswerPanel question={gq} code="print(1)" onScored={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: "Run all tests" }));
    await waitFor(() => expect(m.pending).toHaveLength(1));
    unmount();
    expect(m.disposed()).toBeGreaterThan(0);
  });
});

// ------------------------------------------------------------------ bulk run

describe("bulk run", () => {
  it("Cancel stops the run; no second run while one runs; unmount stops it", async () => {
    const runners: ReturnType<typeof manualRunner>[] = [];
    setRunnerFactoryForTests("python", () => {
      const m = manualRunner();
      runners.push(m);
      return m.r;
    });
    const user = userEvent.setup();
    const items = [item(), item({ attempt_id: "a2" })];
    const { unmount } = render(<BulkCodeRun examId="e_1" questions={[gq]} items={items} onItemGraded={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: "Run all code tests (2)" }));
    await user.click(screen.getByRole("button", { name: "Run and save" }));
    await waitFor(() => expect(runners).toHaveLength(1));
    expect(screen.queryByRole("button", { name: /Run all code tests/ })).toBeNull(); // no second run
    await user.click(screen.getByRole("button", { name: "Cancel run" }));
    expect(runners[0]!.disposed()).toBeGreaterThan(0);
    expect(await screen.findByRole("status")).toHaveTextContent(/Stopped\. Scored 0 answers/);
    expect(runners).toHaveLength(1); // the second answer never started
    expect(api.saveGrade).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "Run all code tests (2)" }));
    await user.click(screen.getByRole("button", { name: "Run and save" }));
    await waitFor(() => expect(runners).toHaveLength(2));
    unmount();
    expect(runners[1]!.disposed()).toBeGreaterThan(0);
  });

  it("keeps a grade entered by hand while the tests ran (on this page or on the server)", async () => {
    const runners: ReturnType<typeof manualRunner>[] = [];
    setRunnerFactoryForTests("python", () => {
      const m = manualRunner();
      runners.push(m);
      return m.r;
    });
    const manual = { points: 5, comment: "by hand", rubric_scores: null, graded_at: "2026-10-23T00:00:00Z" };
    // a3 was graded in another tab: only the server's queue knows
    api.grading.mockResolvedValue({ items: [item(), item({ attempt_id: "a2" }), item({ attempt_id: "a3", grade: manual })] });
    api.saveGrade.mockImplementation(async (_e: string, aid: string, qid: string, input: object) => ({
      attempt_id: aid,
      question_id: qid,
      grade: { ...input, graded_at: "2026-10-23T00:00:00Z" },
    }));
    const graded = vi.fn();
    const user = userEvent.setup();
    let items = [item(), item({ attempt_id: "a2" }), item({ attempt_id: "a3" })];
    const ui = (list: GradingItem[]) => <BulkCodeRun examId="e_1" questions={[gq]} items={list} onItemGraded={graded} />;
    const { rerender } = render(ui(items));
    await user.click(screen.getByRole("button", { name: "Run all code tests (3)" }));
    await user.click(screen.getByRole("button", { name: "Run and save" }));
    await waitFor(() => expect(runners).toHaveLength(1));
    // the teacher saves a1 by hand on this page while its tests run
    items = items.map((it) => (it.attempt_id === "a1" ? { ...it, grade: manual } : it));
    rerender(ui(items));
    await act(async () => runners[0]!.pending[0]!(ok("1\n")));
    await waitFor(() => expect(runners).toHaveLength(2));
    await act(async () => runners[1]!.pending[0]!(ok("1\n")));
    await waitFor(() => expect(runners).toHaveLength(3));
    await act(async () => runners[2]!.pending[0]!(ok("1\n")));
    expect(await screen.findByRole("status")).toHaveTextContent("Scored 1 answer from the tests, 2 graded by hand meanwhile (kept)");
    expect(api.saveGrade.mock.calls.map((c) => c[1])).toEqual(["a2"]);
    // the page learns about the grade from the other tab
    expect(graded).toHaveBeenCalledWith(expect.objectContaining({ attempt_id: "a3", grade: manual }));
  });
});

// ------------------------------------------------------------------ copying questions

describe("copying questions from another exam", () => {
  it("an 'exam default' code question takes the source exam's default language", () => {
    const src = {
      id: "e_src",
      title: "Src",
      duration_min: 10,
      default_language: "javascript",
      questions: [
        { id: "q1", type: "code", prompt: "A", points: 5, language: null, tests: [] },
        { id: "q2", type: "code", prompt: "B", points: 5, language: "python", tests: [] },
        { id: "q3", type: "essay", prompt: "C", points: 5 },
      ],
    } as unknown as Exam;
    const qs = copyQuestions(src);
    expect(qs.map((q) => q.language)).toEqual(["javascript", "python", ""]);
    // no default on the source: the built-in default (Python)
    const plain = copyQuestions({ ...src, default_language: undefined } as unknown as Exam);
    expect(plain[0]!.language).toBe("python");
  });
});
