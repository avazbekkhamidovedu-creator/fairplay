// C, C++ and Java through the exam server's runner: capability store, request mapping,
// error messages, pickers, the student notice and teacher grading. fetch is mocked.
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import type { GradingItem, GradingQuestion, StudentQuestion, StudentRunResponse, TeacherRunTestsResponse } from "../api/types";
import { CodeQuestion } from "../components/student/CodeQuestion";
import { BulkCodeRun, CodeAnswerPanel, canAutoRun, scoreCode } from "../components/teacher/CodeGrading";
import { CodeQuestionEditor } from "../components/teacher/CodeQuestionEditor";
import { _resetFeaturesForTests } from "../lib/features";
import {
  canRun,
  canRunInBrowser,
  canRunRemotely,
  remoteLanguages,
  runnableLanguages,
  setServerRunnerLanguages,
} from "../lib/code/languages";
import { MSG_OFFLINE, MSG_WAIT, RemoteRunner, _resetRunGateForTests } from "../lib/code/remoteRunner";
import { runTests } from "../lib/code/runTests";
import { _resetServerRunnerForTests, loadServerRunner } from "../lib/code/useServerRunner";
import { newQuestion } from "../pages/teacher/examDraft";

const api = vi.hoisted(() => ({ saveGrade: vi.fn(), grading: vi.fn() }));
vi.mock("../api/client", async (importOriginal) => {
  const orig = await importOriginal<typeof import("../api/client")>();
  return { ...orig, teacherApi: { ...orig.teacherApi, ...api } };
});

beforeAll(() => {
  const rect = { x: 0, y: 0, top: 0, left: 0, right: 0, bottom: 0, width: 0, height: 0, toJSON: () => ({}) };
  Range.prototype.getClientRects ??= (() => ({ length: 0, item: () => null, [Symbol.iterator]: [][Symbol.iterator] })) as never;
  Range.prototype.getBoundingClientRect ??= (() => rect) as never;
});

type Handler = (url: string, init: RequestInit) => { status?: number; body?: unknown } | Promise<{ status?: number; body?: unknown }>;
let handler: Handler;
let health: unknown = { code_runner: null };
function offer(langs: string[]) {
  health = { code_runner: { languages: langs } };
  setServerRunnerLanguages(langs);
}
const calls: Array<{ url: string; body: unknown; signal?: AbortSignal | null }> = [];

beforeEach(() => {
  calls.length = 0;
  health = { code_runner: null };
  _resetRunGateForTests();
  _resetServerRunnerForTests();
  _resetFeaturesForTests();
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init: RequestInit = {}) => {
      if (url === "/api/health") return new Response(JSON.stringify(health), { status: 200 });
      calls.push({ url, body: init.body ? JSON.parse(String(init.body)) : undefined, signal: init.signal });
      const r = await handler(url, init);
      const status = r.status ?? 200;
      return new Response(r.body === undefined ? null : JSON.stringify(r.body), { status });
    }),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
});

const okRun = (n: number, out = (i: number) => `out${i}\n`): StudentRunResponse => ({
  status: "ok",
  compile_output: "",
  results: Array.from({ length: n }, (_, i) => ({ status: "ok" as const, stdout: out(i), stderr: "", time_ms: 7, truncated: false })),
});

const RUN_URL = "/api/student/attempts/a1/questions/q_c/run";

describe("capability store", () => {
  it("is empty until the server lists languages; browser languages stay browser-only", () => {
    expect(remoteLanguages()).toEqual([]);
    expect(canRunRemotely("cpp")).toBe(false);
    expect(canRun("cpp")).toBe(false);
    expect(canRun("python")).toBe(true);
    expect(runnableLanguages()).toEqual(["python", "javascript", "typescript", "sql"]);
    setServerRunnerLanguages(["c", "java", "python", "bogus"]);
    expect(remoteLanguages()).toEqual(["c", "java"]);
    expect(canRunRemotely("c")).toBe(true);
    expect(canRunRemotely("cpp")).toBe(false);
    expect(canRunRemotely("python")).toBe(false);
    expect(canRunInBrowser("c")).toBe(false);
    expect(canRun("java")).toBe(true);
    expect(runnableLanguages()).toEqual(["python", "javascript", "typescript", "sql", "c", "java"]);
    setServerRunnerLanguages(null);
    expect(runnableLanguages()).toEqual(["python", "javascript", "typescript", "sql"]);
  });

  it("loads from /api/health once and tolerates failures", async () => {
    const stub = (f: () => Promise<Response>) => {
      const m = vi.fn(f);
      vi.stubGlobal("fetch", m);
      return m;
    };
    const fresh = () => {
      _resetServerRunnerForTests();
      _resetFeaturesForTests();
    };
    const good = stub(async () => new Response(JSON.stringify({ features: [], code_runner: { languages: ["c", "cpp", "java"] } })));
    await loadServerRunner();
    await loadServerRunner();
    expect(good).toHaveBeenCalledTimes(1);
    expect(remoteLanguages()).toEqual(["c", "cpp", "java"]);

    fresh();
    stub(async () => new Response(JSON.stringify({ features: [], code_runner: null })));
    await loadServerRunner();
    expect(remoteLanguages()).toEqual([]);

    fresh();
    stub(async () => new Response("{}", { status: 500 }));
    await loadServerRunner();
    expect(remoteLanguages()).toEqual([]);

    fresh();
    stub(() => Promise.reject(new TypeError("offline")));
    await loadServerRunner();
    expect(remoteLanguages()).toEqual([]);
  });
});

describe("remote runner", () => {
  it("sends one request for N visible tests and maps the results", async () => {
    handler = () => ({ body: okRun(3, (i) => `${i + 1}\n`) });
    const r = new RemoteRunner("cpp", "a1", "q_c");
    const out = await runTests(
      r,
      "int main(){}",
      [
        { input: "a", expected: "1" },
        { input: "b", expected: "2" },
        { input: "c", expected: "9" },
      ],
      1000,
    );
    const runs = calls.filter((c) => c.url === RUN_URL);
    expect(runs).toHaveLength(1);
    expect(runs[0]!.body).toEqual({ source: "int main(){}", stdins: ["a", "b", "c"] });
    expect(out.map((o) => o.status)).toEqual(["pass", "pass", "fail"]);
    expect(out[0]).toMatchObject({ stdout: "1\n", timeMs: 7, input: "a", expected: "1" });
  });

  it("maps limits, runtime errors and truncation", async () => {
    handler = () => ({
      body: {
        status: "ok",
        compile_output: "",
        results: [
          { status: "time_limit", stdout: "", stderr: "", time_ms: 1000, truncated: false },
          { status: "runtime_error", stdout: "x", stderr: "segfault", time_ms: 3, truncated: false },
          { status: "memory_limit", stdout: "", stderr: "", time_ms: 3, truncated: false },
          { status: "output_limit", stdout: "zz", stderr: "", time_ms: 3, truncated: true },
        ],
      } satisfies StudentRunResponse,
    });
    const res = await new RemoteRunner("c", "a1", "q_c").runBatch("x", ["", "", "", ""], 1000);
    expect(res.map((x) => x.status)).toEqual(["timeout", "error", "error", "error"]);
    expect(res[0]!.stderr).toContain("Time limit exceeded (1 s).");
    expect(res[1]!.stderr).toBe("segfault");
    expect(res[2]!.stderr).toContain("Memory limit exceeded.");
    expect(res[3]).toMatchObject({ truncated: true });
  });

  it("shows a compile error as stderr of every test", async () => {
    handler = () => ({ body: { status: "compile_error", compile_output: "main.c:1: error: expected ';'", results: [] } });
    const out = await runTests(new RemoteRunner("c", "a1", "q_c"), "bad", [{ input: "", expected: "1" }, { input: "", expected: "2" }], 1000);
    expect(out.map((o) => o.status)).toEqual(["error", "error"]);
    expect(out[0]!.stderr).toContain("expected ';'");
  });

  it("a custom run sends one stdin", async () => {
    handler = () => ({ body: okRun(1, () => "hi\n") });
    const res = await new RemoteRunner("java", "a1", "q_c").run("code", "in", 1000);
    expect(calls[0]!.body).toEqual({ source: "code", stdins: ["in"] });
    expect(res).toMatchObject({ status: "ok", stdout: "hi\n" });
  });

  it("answers 429 with a friendly message and a client-side gate (no second request within 3 s)", async () => {
    let t = 1_000_000;
    handler = () => ({ status: 429, body: { detail: "slow down" } });
    const r = new RemoteRunner("c", "a1", "q_c", undefined, () => t);
    const first = await r.run("x", "", 1000);
    expect(first).toMatchObject({ status: "unavailable", stderr: MSG_WAIT });
    const second = await r.run("x", "", 1000);
    expect(second.stderr).toBe(MSG_WAIT);
    expect(calls.filter((c) => c.url === RUN_URL)).toHaveLength(1);
    t += 3100;
    handler = () => ({ body: okRun(1) });
    expect((await r.run("x", "", 1000)).status).toBe("ok");
    expect(calls.filter((c) => c.url === RUN_URL)).toHaveLength(2);
  });

  it("shows the network message on 503 and on a network failure", async () => {
    let t = 5_000_000;
    const r = new RemoteRunner("c", "a1", "q_c", undefined, () => (t += 5000));
    handler = () => ({ status: 503, body: { detail: "The code runner is not available right now" } });
    expect(await r.run("x", "", 1000)).toMatchObject({ status: "unavailable", stderr: MSG_OFFLINE });
    vi.stubGlobal("fetch", vi.fn(async () => Promise.reject(new TypeError("offline"))));
    expect(await r.run("x", "", 1000)).toMatchObject({ status: "unavailable", stderr: MSG_OFFLINE });
  });

  it("shows the server's text for 400 and aborts the request on dispose", async () => {
    handler = () => ({ status: 400, body: { detail: "This language cannot be run here" } });
    expect((await new RemoteRunner("c", "a1", "q_c").run("x", "", 1000)).stderr).toBe("This language cannot be run here");

    _resetRunGateForTests();
    let seen: AbortSignal | null | undefined;
    vi.stubGlobal(
      "fetch",
      vi.fn((_u: string, init: RequestInit) => {
        seen = init.signal;
        return new Promise<Response>((_res, rej) => init.signal?.addEventListener("abort", () => rej(new DOMException("x", "AbortError"))));
      }),
    );
    const r = new RemoteRunner("c", "a1", "q_c");
    const p = r.run("x", "", 1000);
    r.dispose();
    expect(seen?.aborted).toBe(true);
    expect((await p).stderr).toBe("Stopped.");
  });
});

function sq(over: Partial<StudentQuestion> = {}): StudentQuestion {
  return {
    id: "q_c",
    type: "code",
    prompt: "Sum",
    points: 10,
    options: [],
    language: "cpp",
    tests: [
      { input: "1 2", expected: "3" },
      { input: "2 2", expected: "4" },
    ],
    write_only: false,
    time_limit_ms: 1000,
    ...over,
  };
}

function renderStudent(q: StudentQuestion) {
  return render(<CodeQuestion question={q} value={undefined} onChange={vi.fn()} prompt={<h1>{q.prompt}</h1>} attemptId="a1" />);
}

describe("student screen", () => {
  it("keeps today's notice when the server offers no runner", () => {
    handler = () => ({ body: { code_runner: null } });
    renderStudent(sq());
    expect(screen.getByTestId("code-no-run")).toHaveTextContent("Run isn't available for C++");
    expect(screen.queryByTestId("code-remote-hint")).toBeNull();
  });

  it("runs the samples in one request and shows the hint", async () => {
    offer(["cpp"]);
    handler = () => ({ body: okRun(2, (i) => (i === 0 ? "3\n" : "5\n")) });
    const user = userEvent.setup();
    renderStudent(sq());
    expect(screen.getByTestId("code-remote-hint")).toHaveTextContent("Runs on the exam server");
    await user.click(screen.getByRole("button", { name: "Run samples" }));
    expect(await screen.findByTestId("samples-summary")).toHaveTextContent("1 of 2 sample tests passed");
    const runs = calls.filter((c) => c.url === RUN_URL);
    expect(runs).toHaveLength(1);
    expect((runs[0]!.body as { stdins: string[] }).stdins).toEqual(["1 2", "2 2"]);
  });

  it("shows a compile error in the output area", async () => {
    offer(["cpp"]);
    handler = () => ({ body: { status: "compile_error", compile_output: "error: boom", results: [] } });
    const user = userEvent.setup();
    renderStudent(sq());
    await user.click(screen.getByRole("button", { name: "Run samples" }));
    expect(await screen.findAllByText("error: boom")).toHaveLength(2);
  });

  it("aborts a run in flight when the question unmounts", async () => {
    offer(["cpp"]);
    handler = () => new Promise(() => undefined);
    const user = userEvent.setup();
    const { unmount } = renderStudent(sq());
    await user.click(screen.getByRole("button", { name: "Run samples" }));
    await waitFor(() => expect(calls.some((c) => c.url === RUN_URL)).toBe(true));
    const sig = calls.find((c) => c.url === RUN_URL)!.signal;
    expect(sig?.aborted).toBe(false);
    unmount();
    expect(sig?.aborted).toBe(true);
  });

  it("re-renders when the capability arrives", () => {
    handler = () => ({ body: { code_runner: null } });
    renderStudent(sq());
    expect(screen.getByTestId("code-no-run")).toBeInTheDocument();
    act(() => offer(["cpp"]));
    expect(screen.queryByTestId("code-no-run")).toBeNull();
    expect(screen.getByRole("button", { name: "Run samples" })).toBeInTheDocument();
  });
});

describe("teacher language picker", () => {
  function Picker() {
    const q = { ...newQuestion("code") };
    return <CodeQuestionEditor q={q} id={(s) => `qx-${s}`} errors={{}} onChange={() => undefined} defaultLanguage="python" />;
  }
  const optionOf = (name: RegExp) => screen.getByRole("option", { name }) as HTMLOptionElement;

  it("disables C, C++ and Java without a runner", () => {
    render(<Picker />);
    expect(optionOf(/^C \(runner coming soon\)/).disabled).toBe(true);
    expect(optionOf(/^C\+\+ \(runner coming soon\)/).disabled).toBe(true);
    expect(optionOf(/^Java \(runner coming soon\)/).disabled).toBe(true);
  });

  it("enables exactly the languages the server offers", () => {
    offer(["c", "java"]);
    render(<Picker />);
    expect(optionOf(/^C$/).disabled).toBe(false);
    expect(optionOf(/^Java$/).disabled).toBe(false);
    expect(optionOf(/^C\+\+ \(runner coming soon\)/).disabled).toBe(true);
  });
});

const gq: GradingQuestion = {
  question_id: "q_c",
  index: 0,
  type: "code",
  prompt: "Sum",
  points: 10,
  rubric: [],
  to_grade: 1,
  graded: 0,
  language: "java",
  time_limit_ms: 1000,
  tests: [
    { input: "1 2", expected: "3", visible: true, points: 4 },
    { input: "5 5", expected: "10", visible: false, points: 6 },
  ],
};

const teacherRes = (passed: boolean[]): TeacherRunTestsResponse => ({
  status: "ok",
  compile_output: "",
  tests: passed.map((p, i) => ({
    index: i,
    visible: i === 0,
    points: i === 0 ? 4 : 6,
    passed: p,
    status: "ok" as const,
    stdout: p ? "ok" : "bad",
    stderr: "",
    time_ms: 9,
  })),
  earned: 0,
  total: 10,
});

const item = (over: Partial<GradingItem> = {}): GradingItem => ({
  attempt_id: "a1",
  anon_id: "#A",
  username: "u",
  name: "U",
  question_id: "q_c",
  answer: "class A {}",
  word_count: null,
  auto_points: null,
  grade: null,
  flag_count: 0,
  ...over,
});

describe("teacher grading through the run-tests route", () => {
  const URL = "/api/exams/e_1/questions/q_c/run-tests";

  it("canAutoRun follows the server's list", () => {
    expect(canAutoRun(gq)).toBe(false);
    offer(["java"]);
    expect(canAutoRun(gq)).toBe(true);
  });

  it("scores an answer from the route's results (hidden tests stay on the server)", async () => {
    offer(["java"]);
    handler = () => ({ body: teacherRes([true, false]) });
    const s = await scoreCode(gq, "class A {}", undefined, undefined, undefined, "e_1");
    expect(calls.filter((c) => c.url === URL)).toHaveLength(1);
    expect(calls.find((c) => c.url === URL)!.body).toEqual({ source: "class A {}" });
    expect(s.points).toBe(4);
    expect(s.comment).toBe("Passed 1 of 2 tests (samples 1 of 1, hidden 0 of 1).");
    expect(s.outcomes[0]).toMatchObject({ status: "pass", input: "1 2", expected: "3", visible: true });
    expect(s.outcomes[1]).toMatchObject({ status: "fail", visible: false });
  });

  it("panel: runs all tests and reports; a runner outage leaves the answer ungraded", async () => {
    offer(["java"]);
    const user = userEvent.setup();
    const onScored = vi.fn();
    handler = () => ({ status: 503, body: { detail: "The code runner is not available right now" } });
    render(
      <MemoryRouter>
        <CodeAnswerPanel question={gq} code="class A {}" onScored={onScored} examId="e_1" />
      </MemoryRouter>,
    );
    await user.click(screen.getByRole("button", { name: "Run all tests" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("The code runner is not available right now");
    expect(onScored).not.toHaveBeenCalled();

    handler = () => ({ body: teacherRes([true, true]) });
    await user.click(screen.getByRole("button", { name: "Run all tests" }));
    await waitFor(() => expect(onScored).toHaveBeenCalled());
    expect(onScored.mock.calls[0]![0]).toMatchObject({ points: 10 });
  });

  it("bulk run uses the route, at most 3 requests at once", async () => {
    offer(["java"]);
    let open = 0;
    let peak = 0;
    const release: Array<() => void> = [];
    handler = (url) => {
      if (url !== URL) return { body: {} };
      open += 1;
      peak = Math.max(peak, open);
      return new Promise((res) =>
        release.push(() => {
          open -= 1;
          res({ body: teacherRes([true, true]) });
        }),
      );
    };
    const user = userEvent.setup();
    const items = [1, 2, 3, 4, 5].map((n) => item({ attempt_id: `a${n}` }));
    const graded = vi.fn();
    api.saveGrade.mockImplementation(async (_e: string, aid: string, qid: string, input: object) => ({ attempt_id: aid, question_id: qid, grade: { ...input, graded_at: "t" } }));
    api.grading.mockResolvedValue({ items: [] });
    {
      render(<BulkCodeRun examId="e_1" questions={[gq]} items={items} onItemGraded={graded} />);
      await user.click(screen.getByRole("button", { name: "Run all code tests (5)" }));
      await user.click(screen.getByRole("button", { name: "Run and save" }));
      await waitFor(() => expect(release.length).toBe(3));
      expect(peak).toBe(3);
      while (graded.mock.calls.length < 5) {
        release.splice(0).forEach((f) => f());
        await new Promise((r) => setTimeout(r, 5));
      }
      expect(peak).toBe(3);
      expect(api.saveGrade).toHaveBeenCalledTimes(5);
      expect(api.saveGrade.mock.calls[0]![3]).toMatchObject({ points: 10 });
    }
  });
});
