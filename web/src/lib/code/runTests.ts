// Phase 4: running a program against tests and summing the points of the passed ones.
import { outputsMatch, sqlOutputsMatch } from "./compare";
import type { SqlCompare } from "../../api/types";
import type { RunResult, Runner } from "./runner";

export interface TestCase {
  input: string;
  expected: string;
  /** missing = sample test (what students get) */
  visible?: boolean;
  points?: number;
}

export type TestStatus = "pass" | "fail" | "error" | "timeout" | "unavailable";

export interface TestOutcome {
  index: number;
  status: TestStatus;
  input: string;
  expected: string;
  stdout: string;
  stderr: string;
  timeMs: number;
  visible: boolean;
  points: number;
  truncated?: boolean;
}

/** Options for SQL questions: the setup script and how rows are compared. */
export interface RunTestsOptions {
  sqlSetup?: string;
  sqlCompare?: SqlCompare;
}

export function outcomeOf(index: number, t: TestCase, r: RunResult, opts?: RunTestsOptions): TestOutcome {
  let status: TestStatus;
  if (r.status === "timeout") status = "timeout";
  else if (r.status === "unavailable") status = "unavailable";
  else if (r.status === "error") status = "error";
  else if (opts?.sqlSetup !== undefined || opts?.sqlCompare !== undefined) {
    status = sqlOutputsMatch(r.stdout, t.expected, opts.sqlCompare ?? "unordered") ? "pass" : "fail";
  } else status = outputsMatch(r.stdout, t.expected) ? "pass" : "fail";
  return {
    index,
    status,
    input: t.input,
    expected: t.expected,
    stdout: r.stdout,
    stderr: r.stderr,
    timeMs: r.timeMs,
    visible: t.visible !== false,
    points: t.points ?? 0,
    truncated: r.truncated,
  };
}

/** Thrown by runTests when its signal is aborted (the caller drops the partial results). */
export class TestsAborted extends Error {
  constructor() {
    super("Stopped.");
    this.name = "AbortError";
  }
}

export function isAborted(e: unknown): boolean {
  return e instanceof TestsAborted || (e instanceof Error && e.name === "AbortError");
}

/** Runs the tests one after the other. Stops early when the runner is unavailable. With a
 *  signal: rejects with TestsAborted as soon as it is aborted (the caller disposes the
 *  runner, which ends the program in progress). */
export async function runTests(
  runner: Runner,
  code: string,
  tests: TestCase[],
  timeLimitMs: number,
  onProgress?: (done: TestOutcome[]) => void,
  signal?: AbortSignal,
  opts?: RunTestsOptions,
): Promise<TestOutcome[]> {
  // a SQL test has no input: the run's second argument is the question's setup script
  const sql = runner.language === "sql";
  const o = sql ? { sqlSetup: opts?.sqlSetup ?? "", sqlCompare: opts?.sqlCompare } : undefined;
  const inputOf = (t: TestCase) => (sql ? o!.sqlSetup! : t.input);
  const out: TestOutcome[] = [];
  let onAbort: (() => void) | null = null;
  const aborted = signal
    ? new Promise<never>((_, reject) => {
        onAbort = () => reject(new TestsAborted());
        if (signal.aborted) onAbort();
        else signal.addEventListener("abort", onAbort, { once: true });
      })
    : null;
  aborted?.catch(() => undefined);
  try {
    for (let i = 0; i < tests.length; i++) {
      if (signal?.aborted) throw new TestsAborted();
      const t = tests[i]!;
      const r = aborted ? await Promise.race([runner.run(code, inputOf(t), timeLimitMs), aborted]) : await runner.run(code, inputOf(t), timeLimitMs);
      if (signal?.aborted) throw new TestsAborted();
      out.push(outcomeOf(i, t, r, o));
      onProgress?.([...out]);
      if (r.status === "unavailable") {
        for (let j = i + 1; j < tests.length; j++) out.push(outcomeOf(j, tests[j]!, r, o));
        break;
      }
    }
  } finally {
    if (signal && onAbort) signal.removeEventListener("abort", onAbort);
  }
  return out;
}

export interface TestScore {
  passed: number;
  total: number;
  /** points earned (test points; or the question's points shared out when tests have none) */
  points: number;
}

/** Points for a set of outcomes. When no test carries points, the question's points are
 *  shared out by the share of tests passed (rounded to 2 decimals). */
export function scoreOutcomes(outcomes: TestOutcome[], questionPoints: number): TestScore {
  const total = outcomes.length;
  const passed = outcomes.filter((o) => o.status === "pass").length;
  const testPoints = outcomes.reduce((s, o) => s + (o.points || 0), 0);
  let points: number;
  if (total === 0) points = 0;
  else if (testPoints > 0) points = outcomes.reduce((s, o) => s + (o.status === "pass" ? o.points || 0 : 0), 0);
  else points = Math.round(((questionPoints * passed) / total) * 100) / 100;
  return { passed, total, points: Math.min(points, questionPoints) };
}

/** "Passed 3 of 4 tests (samples 2 of 2, hidden 1 of 2)." — the grade comment. */
export function testSummary(outcomes: TestOutcome[]): string {
  const total = outcomes.length;
  const passed = outcomes.filter((o) => o.status === "pass").length;
  const vis = outcomes.filter((o) => o.visible);
  const hid = outcomes.filter((o) => !o.visible);
  const parts: string[] = [];
  if (vis.length && hid.length) {
    parts.push(`samples ${vis.filter((o) => o.status === "pass").length} of ${vis.length}`);
    parts.push(`hidden ${hid.filter((o) => o.status === "pass").length} of ${hid.length}`);
  }
  return `Passed ${passed} of ${total} ${total === 1 ? "test" : "tests"}${parts.length ? ` (${parts.join(", ")})` : ""}.`;
}

export const STATUS_LABEL: Record<TestStatus, string> = {
  pass: "Passed",
  fail: "Wrong answer",
  error: "Error",
  timeout: "Time limit exceeded",
  unavailable: "Not run",
};
