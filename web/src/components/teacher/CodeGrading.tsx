// Phase 4: grading coding answers. The server never runs code; the tests (sample and
// hidden) run here in the teacher's browser with the same runners the students use, and
// the points of the passed tests fill the score (the teacher can still change it).
// 2026-10-23: each answer runs in a runner (worker) of its own that is thrown away after
// scoring: a program that tampers with its interpreter cannot change the next answer's score.
// 2026-10-23 (review): runs can be stopped (AbortController): Stop / Cancel buttons, leaving
// the page and moving to another answer end the program and free its worker. The bulk run
// never overwrites a grade entered while it runs: it checks the page's latest items and the
// server's queue right before saving each answer.
import { useEffect, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import { ApiError, NetworkError, teacherApi } from "../../api/client";
import type { CodeTest, GradingItem, GradingQuestion, TeacherRunTestsResponse } from "../../api/types";
import { CodeEditor } from "../code/LazyCodeEditor";
import { DEFAULT_TIME_LIMIT_MS, LANGUAGE_LABEL, canRun, canRunRemotely, resolveLanguage } from "../../lib/code/languages";
import { useServerRunnerLanguages } from "../../lib/code/useServerRunner";
import { fmtSeconds } from "../../lib/code/runner";
import { createRunner } from "../../lib/code/runners";
import { RunnerPool, gradingPoolSize } from "../../lib/code/runnerPool";
import type { Runner } from "../../lib/code/runner";
import { STATUS_LABEL, TestsAborted, isAborted, runTests, scoreOutcomes, testSummary, type TestOutcome } from "../../lib/code/runTests";
import { isBlankAnswer } from "../../pages/teacher/gradingLogic";
import { errorText } from "./util";
import "../../styles/code.css";

/** The score the tests give one answer: points and a comment for the student. */
export interface CodeScore {
  points: number;
  comment: string;
  outcomes: TestOutcome[];
}

export function canAutoRun(q: Pick<GradingQuestion, "type" | "language" | "tests">): boolean {
  return q.type === "code" && canRun(resolveLanguage(q.language)) && (q.tests?.length ?? 0) > 0;
}

/** Resolves like `p`, or rejects with TestsAborted as soon as the signal is aborted. */
function unlessAborted<T>(p: Promise<T>, signal?: AbortSignal): Promise<T> {
  if (!signal) return p;
  if (signal.aborted) return Promise.reject(new TestsAborted());
  return new Promise<T>((resolve, reject) => {
    const onAbort = () => reject(new TestsAborted());
    signal.addEventListener("abort", onAbort, { once: true });
    p.then(
      (v) => (signal.removeEventListener("abort", onAbort), resolve(v)),
      (e: unknown) => (signal.removeEventListener("abort", onAbort), reject(e)),
    );
  });
}

export const MSG_RUNNER_DOWN = "The code runner is not available right now. This answer was left ungraded: try again later or grade it by hand.";

/** At most this many answers wait on the server runner at once. */
export const REMOTE_CONCURRENCY = 3;
let remoteBusy = 0;
const remoteWaiting: Array<() => void> = [];
async function remoteSlot<T>(job: () => Promise<T>, signal?: AbortSignal): Promise<T> {
  while (remoteBusy >= REMOTE_CONCURRENCY) {
    await unlessAborted(new Promise<void>((res) => remoteWaiting.push(res)), signal);
  }
  remoteBusy += 1;
  try {
    return await job();
  } finally {
    remoteBusy -= 1;
    remoteWaiting.splice(0).forEach((wake) => wake());
  }
}

function remoteStatus(s: TeacherRunTestsResponse["tests"][number]["status"]): TestOutcome["status"] {
  if (s === "ok") return "fail";
  return s === "time_limit" ? "timeout" : "error";
}

/** Maps the teacher run-tests answer to the same outcomes the browser runners give. */
export function outcomesOfRemote(q: GradingQuestion, res: TeacherRunTestsResponse): TestOutcome[] {
  const defs = q.tests ?? [];
  if (res.status === "compile_error") {
    return defs.map((t, i) => ({
      index: i, status: "error", input: t.input, expected: t.expected, stdout: "",
      stderr: res.compile_output || "Compile error.", timeMs: 0, visible: t.visible, points: t.points,
    }));
  }
  return res.tests.map((t) => {
    const d = defs[t.index];
    return {
      index: t.index,
      status: t.passed ? "pass" : remoteStatus(t.status),
      input: d?.input ?? "",
      expected: d?.expected ?? "",
      stdout: t.stdout,
      stderr: t.stderr,
      timeMs: t.time_ms,
      visible: t.visible,
      points: t.points,
    } satisfies TestOutcome;
  });
}

async function scoreRemote(q: GradingQuestion, code: string, examId: string, onProgress?: (o: TestOutcome[]) => void, signal?: AbortSignal): Promise<CodeScore> {
  let res: TeacherRunTestsResponse;
  try {
    res = await remoteSlot(() => teacherApi.runCodeTests(examId, q.question_id, code, signal), signal);
  } catch (e) {
    if (isAborted(e) || signal?.aborted) throw new TestsAborted();
    if (e instanceof NetworkError || (e instanceof ApiError && (e.status === 429 || e.status >= 500))) throw new Error(MSG_RUNNER_DOWN);
    throw e;
  }
  if (signal?.aborted) throw new TestsAborted();
  if (res.status === "internal_error") throw new Error(MSG_RUNNER_DOWN);
  const outcomes = outcomesOfRemote(q, res);
  onProgress?.(outcomes);
  const s = scoreOutcomes(outcomes, q.points);
  return { points: s.points, comment: testSummary(outcomes), outcomes };
}

/** Runs one answer against all its tests in a runner of its own. With a signal, aborting
 *  stops the program at once (the runner is disposed) and rejects with TestsAborted. */
export async function scoreCode(
  q: GradingQuestion,
  code: string,
  onProgress?: (o: TestOutcome[]) => void,
  signal?: AbortSignal,
  pool?: RunnerPool,
  examId = "",
): Promise<CodeScore> {
  if (signal?.aborted) throw new TestsAborted();
  const lang = resolveLanguage(q.language);
  if (canRunRemotely(lang)) return scoreRemote(q, code, examId, onProgress, signal);
  let r: Runner | null;
  if (pool) r = await unlessAborted(pool.acquire(), signal);
  else r = createRunner(lang);
  if (!r) throw new Error("This language cannot run in the browser.");
  const runner = r;
  const stop = () => runner.dispose();
  signal?.addEventListener("abort", stop, { once: true });
  let outcomes: TestOutcome[] | null = null;
  try {
    await unlessAborted(runner.prepare(), signal);
    const tests: CodeTest[] = q.tests ?? [];
    outcomes = await runTests(runner, code, tests, q.time_limit_ms ?? DEFAULT_TIME_LIMIT_MS, onProgress, signal, {
      sqlSetup: q.sql_setup,
      sqlCompare: q.sql_compare,
    });
  } finally {
    signal?.removeEventListener("abort", stop);
    if (pool) pool.release(runner, !outcomes || outcomes.some((o) => o.status === "unavailable"));
    else runner.dispose();
  }
  if (outcomes.some((o) => o.status === "unavailable")) throw new Error(outcomes.find((o) => o.stderr)?.stderr || "The tests could not run.");
  const s = scoreOutcomes(outcomes, q.points);
  return { points: s.points, comment: testSummary(outcomes), outcomes };
}

export function CodeAnswerPanel({
  question,
  code,
  onScored,
  examId: examIdProp,
}: {
  question: GradingQuestion;
  code: string;
  onScored: (score: CodeScore) => void;
  examId?: string;
}) {
  useServerRunnerLanguages();
  const { id: routeExam = "" } = useParams();
  const examId = examIdProp ?? routeExam;
  const lang = resolveLanguage(question.language);
  const tests = question.tests ?? [];
  const hidden = tests.filter((t) => !t.visible).length;
  const [outcomes, setOutcomes] = useState<TestOutcome[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const run = useRef<AbortController | null>(null);

  // another answer (or leaving the page) stops the run of this one and clears its results
  useEffect(() => {
    setOutcomes(null);
    setProblem(null);
    setBusy(false);
    return () => {
      run.current?.abort();
      run.current = null;
    };
  }, [code, question.question_id]);

  async function runAll() {
    if (run.current) return;
    const ac = new AbortController();
    run.current = ac;
    setBusy(true);
    setProblem(null);
    setOutcomes([]);
    try {
      const s = await scoreCode(question, code, (o) => !ac.signal.aborted && setOutcomes(o), ac.signal, undefined, examId);
      if (ac.signal.aborted) return;
      setOutcomes(s.outcomes);
      onScored(s);
    } catch (e) {
      if (ac.signal.aborted || isAborted(e)) return;
      setProblem(e instanceof Error && !(e instanceof ApiError) ? e.message : errorText(e));
    } finally {
      if (run.current === ac) {
        run.current = null;
        setBusy(false);
      }
    }
  }

  function stop() {
    const ac = run.current;
    if (!ac) return;
    run.current = null;
    ac.abort();
    setBusy(false);
    setOutcomes(null);
  }

  const blank = isBlankAnswer(code);
  return (
    <div className="t-code-answer" data-testid="code-answer">
      <div className="t-code-answer-bar">
        <strong>{LANGUAGE_LABEL[lang]}</strong>
        <span className="t-muted t-small">
          {tests.length} {tests.length === 1 ? "test" : "tests"}
          {hidden ? ` (${hidden} hidden)` : ""} · time limit {fmtSeconds(question.time_limit_ms ?? DEFAULT_TIME_LIMIT_MS)}
        </span>
        <span style={{ flex: 1 }} />
        {canAutoRun(question) && !blank && (
          <button type="button" className="btn btn-primary" onClick={() => void runAll()} disabled={busy}>
            {busy ? (outcomes && outcomes.length ? `Running ${outcomes.length + 1} of ${tests.length}…` : "Loading…") : "Run all tests"}
          </button>
        )}
        {busy && (
          <button type="button" className="btn btn-ghost" onClick={stop}>
            Stop
          </button>
        )}
      </div>
      {blank ? (
        <p className="t-essay empty">No answer was given.</p>
      ) : (
        <div className="t-code-editor readonly">
          <CodeEditor key={code} initialValue={code} language={lang} readOnly label="Student's code" />
        </div>
      )}
      {!canRun(lang) && !blank && (
        <p className="t-small t-muted" style={{ margin: 0 }}>
          {LANGUAGE_LABEL[lang]} cannot run inside FairPlay yet. Read the code and enter the score.
        </p>
      )}
      {problem && (
        <span className="t-inline-error" role="alert">
          {problem}
        </span>
      )}
      {outcomes && outcomes.length > 0 && <TestResultsTable outcomes={outcomes} />}
    </div>
  );
}

export function TestResultsTable({ outcomes }: { outcomes: TestOutcome[] }) {
  return (
    <ol className="t-code-results" aria-label="Test results">
      {outcomes.map((o) => (
        <li key={o.index} className={o.status === "pass" ? "pass" : "fail"}>
          <details open={o.status !== "pass"}>
            <summary>
              <span>Test {o.index + 1}</span>
              <span className="t-muted">{o.visible ? "visible" : "hidden"}</span>
              <span className={`t-code-pill ${o.status}`}>{STATUS_LABEL[o.status]}</span>
              {o.points ? <span className="t-muted">{o.status === "pass" ? o.points : 0} / {o.points} pts</span> : null}
              {o.status !== "timeout" && <span className="t-muted">{o.timeMs} ms</span>}
            </summary>
            <div className="t-code-io">
              <div>
                <span>Input</span>
                <pre>{o.input || " "}</pre>
              </div>
              <div>
                <span>Expected</span>
                <pre>{o.expected || " "}</pre>
              </div>
              <div>
                <span>Output</span>
                <pre>{o.stdout || " "}</pre>
              </div>
            </div>
            {o.stderr && <pre className="t-code-stderr">{o.stderr}</pre>}
          </details>
        </li>
      ))}
    </ol>
  );
}

/** "Run all code tests": score every ungraded coding answer with its tests and save it. */
export function BulkCodeRun({
  examId,
  questions,
  items,
  onItemGraded,
}: {
  examId: string;
  questions: GradingQuestion[];
  items: GradingItem[];
  onItemGraded: (item: GradingItem) => void;
}) {
  const byId = new Map(questions.map((q) => [q.question_id, q] as const));
  const todo = items.filter((it) => {
    const q = byId.get(it.question_id);
    return q && canAutoRun(q) && !it.grade && !isBlankAnswer(it.answer);
  });
  useServerRunnerLanguages();
  const [asking, setAsking] = useState(false);
  const [progress, setProgress] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const run = useRef<AbortController | null>(null);
  // the page's latest items: a grade the teacher saves during the run is seen at once
  const latest = useRef(items);
  latest.current = items;
  useEffect(
    () => () => {
      run.current?.abort();
      run.current = null;
    },
    [],
  );
  if (!questions.some((q) => canAutoRun(q))) return null;

  const key = (it: Pick<GradingItem, "attempt_id" | "question_id">) => `${it.attempt_id}\u0000${it.question_id}`;
  const gradedNow = (it: GradingItem) => {
    const cur = latest.current.find((x) => key(x) === key(it));
    return !!cur?.grade;
  };

  async function go() {
    if (run.current) return; // one bulk run at a time
    const ac = new AbortController();
    run.current = ac;
    setAsking(false);
    setBusy(true);
    let done = 0;
    let failed = 0;
    let skipped = 0;
    let started = 0;
    const t0 = Date.now();
    const list = [...todo];
    const pools = new Map<string, RunnerPool>();
    const hasRemote = list.some((it) => canRunRemotely(resolveLanguage(byId.get(it.question_id)?.language)));
    const size = Math.min(Math.max(gradingPoolSize(), hasRemote ? REMOTE_CONCURRENCY : 0), list.length);
    const poolFor = (q: GradingQuestion) => {
      const lang = resolveLanguage(q.language);
      let p = pools.get(lang);
      if (!p) pools.set(lang, (p = new RunnerPool(lang, size)));
      return p;
    };
    const show = () => {
      const finished = done + failed + skipped;
      const perMin = done > 0 ? Math.round((done * 60000) / Math.max(1000, Date.now() - t0)) : 0;
      setProgress(
        `Running tests: ${finished} of ${list.length} answers done${started - finished > 1 ? `, ${started - finished} running` : ""}${perMin ? ` (${perMin} answers/min)` : ""}…`,
      );
    };
    show();
    const one = async (it: GradingItem) => {
      const q = byId.get(it.question_id)!;
      if (gradedNow(it)) {
        skipped += 1;
        return;
      }
      try {
        const s = await scoreCode(q, String(it.answer ?? ""), undefined, ac.signal, canRunRemotely(resolveLanguage(q.language)) ? undefined : poolFor(q), examId);
        if (ac.signal.aborted) return;
        // graded by hand while the tests ran (here or in another tab): keep that grade
        if (gradedNow(it)) {
          skipped += 1;
          return;
        }
        const fresh = await teacherApi.grading(examId);
        if (ac.signal.aborted) return;
        const there = fresh.items.find((x) => key(x) === key(it));
        if (there?.grade) {
          skipped += 1;
          onItemGraded({ ...it, ...there });
          return;
        }
        if (gradedNow(it)) {
          skipped += 1;
          return;
        }
        const input = { points: s.points, comment: s.comment, rubric_scores: null };
        const res = await teacherApi.saveGrade(examId, it.attempt_id, it.question_id, input);
        onItemGraded({ ...it, grade: res.grade ?? { ...input, graded_at: new Date().toISOString() } });
        done += 1;
      } catch (e) {
        if (ac.signal.aborted || isAborted(e)) return;
        failed += 1;
      }
    };
    let next = 0;
    const lane = async () => {
      while (!ac.signal.aborted && next < list.length) {
        const it = list[next++]!;
        started += 1;
        show();
        await one(it);
        show();
      }
    };
    ac.signal.addEventListener("abort", () => pools.forEach((p) => p.dispose()), { once: true });
    try {
      await Promise.all(Array.from({ length: size }, lane));
    } finally {
      pools.forEach((p) => p.dispose());
    }
    const stopped = ac.signal.aborted;
    if (run.current === ac) run.current = null;
    else if (stopped) return; // unmounted
    setBusy(false);
    setProgress(
      `${stopped ? "Stopped. " : ""}Scored ${done} ${done === 1 ? "answer" : "answers"} from the tests${failed ? `, ${failed} could not run (grade those by hand)` : ""}${
        skipped ? `, ${skipped} graded by hand meanwhile (kept)` : ""
      }. You can still change each score.`,
    );
  }

  function cancel() {
    run.current?.abort();
  }

  return (
    <div className="t-bulk-code" role="group" aria-label="Coding answers">
      {asking ? (
        <>
          <span className="t-small">
            Runs every ungraded coding answer ({todo.length}) against all its tests here in your browser and saves the points of
            the passed tests.
          </span>
          <button type="button" className="btn btn-primary" onClick={() => void go()}>
            Run and save
          </button>
          <button type="button" className="btn btn-ghost" onClick={() => setAsking(false)}>
            Cancel
          </button>
        </>
      ) : busy ? (
        <button type="button" className="btn btn-ghost" onClick={cancel}>
          Cancel run
        </button>
      ) : (
        <button type="button" className="t-btn-outline" disabled={todo.length === 0} onClick={() => setAsking(true)}>
          Run all code tests{todo.length ? ` (${todo.length})` : ""}
        </button>
      )}
      {progress && (
        <span className="t-small t-muted" role="status">
          {progress}
        </span>
      )}
    </div>
  );
}
